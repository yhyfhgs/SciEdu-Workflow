"""Stage3Persist — 合并 item + annotation → KP/Question 并入库

职责：
- 从 q_done 取 ReviewedAnnotatedItem（item_id FIFO 递增）
- 合并构造 KnowledgePoint 或 Question，走 DisciplineSpec.allowed_*_types 校验
- storage.save_batch() 入库（async via asyncio.to_thread）
- tag_tree.insert_path() 累积 tag 树
- 镜像 items/persisted/item_<id>.json 作为 resume 起点
- state.last_persisted_item_id 递增；Stage3 单协程消费保证不倒退，允许上游拒绝 item 造成 gap

无 LLM 调用。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from sciforge.core.models import (
    Discipline,
    Figure,
    FileExtractionState,
    KnowledgePoint,
    Question,
    ReviewedAnnotatedItem,
)
from sciforge.core.sentinel import _Sentinel
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.pipeline.llm_schemas import SingleAnnotationLLM
from sciforge.pipeline.resume_state import save_state_locked
from sciforge.pipeline.tag_canonicalizer import TagCanonicalizer
from sciforge.pipeline.tag_tree import TagTree
from sciforge.pipeline.taxonomy_validator import TaxonomyValidator

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.storage.protocol import BatchStorageBackend

logger = structlog.get_logger()

# REC-18：从条目正文抽取 markdown 图片链接 ![caption](path) → figures 表
_FIGURE_LINK_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def _extract_figures(content: str, item_id: str, item_kind: str, source_file: str) -> list[Figure]:
    out: list[Figure] = []
    for m in _FIGURE_LINK_RE.finditer(content or ""):
        path = m.group(2).strip()
        if path:
            out.append(
                Figure(
                    item_id=item_id,
                    item_kind=item_kind,
                    source_file=source_file,
                    image_path=path,
                    caption=m.group(1).strip(),
                )
            )
    return out


class Stage3Persist:
    """合并入库（无 LLM）"""

    def __init__(
        self,
        storage: BatchStorageBackend,
        config: AppConfig,
        checkpoint_root: Path | None = None,
        discipline: Discipline = Discipline.MATH,
        tag_tree: TagTree | None = None,
    ) -> None:
        self.storage = storage
        self.config = config
        self.checkpoint_root = checkpoint_root
        self.discipline = discipline
        self.discipline_spec = DISCIPLINE_REGISTRY.get(discipline)
        self.tag_tree = tag_tree if tag_tree is not None else TagTree()
        self.taxonomy_validator = TaxonomyValidator()
        # tag 受控词表统一（#6）：用学科别名表初始化，并把已累积的 tag_tree 注册为既有规范
        self.tag_canon = TagCanonicalizer(self.discipline_spec.tag_aliases)
        self.tag_canon.seed_from_tree(self.tag_tree)
        # REC-10：攒小批再一次 save_batch+commit（WAL fsync 大降）
        self.persist_batch_size: int = max(1, int(getattr(config, "stage3_persist_batch_size", 16)))

    async def run(
        self,
        in_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        # REC-10：攒小批入库。buffer 内未提交的 item 也要参与保序断言，故用本地游标 cursor
        # （= last_persisted + 已 buffer 数）跟踪期望 item_id；flush 提交后才推进 last_persisted。
        buffer: list[ReviewedAnnotatedItem] = []
        cursor = state.last_persisted_item_id
        try:
            while True:
                msg = await in_queue.get()
                if isinstance(msg, _Sentinel):
                    break
                if not isinstance(msg, ReviewedAnnotatedItem):
                    logger.warning("stage3_persist_unexpected_msg", type=type(msg).__name__)
                    continue

                # 保序断言（单协程消费 + FIFO queue）：
                # - gap（item_id > expected）是合法的，Stage1Review 可能拒绝中间条目
                # - backward（item_id ≤ cursor）是错误，说明 FIFO 保序被破坏
                expected = cursor + 1
                if msg.item_id < expected:
                    logger.error(
                        "stage3_out_of_order_backward",
                        expected=expected,
                        got=msg.item_id,
                        last_persisted=state.last_persisted_item_id,
                    )
                    if msg.item_id not in state.out_of_order_items:
                        state.out_of_order_items.append(msg.item_id)
                elif msg.item_id > expected:
                    logger.info(
                        "stage3_skipped_items",
                        expected=expected,
                        got=msg.item_id,
                        gap=msg.item_id - expected,
                    )
                cursor = max(cursor, msg.item_id)

                buffer.append(msg)
                if len(buffer) >= self.persist_batch_size:
                    await self._flush_batch(buffer, state)
                    buffer = []

            if buffer:
                await self._flush_batch(buffer, state)
                buffer = []

            state.stage3_completed = True
            state.stage2_completed = True
            if self.checkpoint_root is not None and state.file_hash:
                # 把 tag_tree 落盘到 state
                state.tag_tree_json = self.tag_tree.to_json()
            await save_state_locked(self.checkpoint_root, state, force=True)
            logger.info(
                "stage3_persist_complete",
                persisted=state.stage3_persisted_count,
                out_of_order=len(state.out_of_order_items),
            )
        except Exception:
            # 异常仍要 flush state 便于追查
            await save_state_locked(self.checkpoint_root, state, force=True)
            raise

    def _build_persistable(
        self, msg: ReviewedAnnotatedItem
    ) -> tuple[KnowledgePoint | None, Question | None]:
        """确定性规范化后把一条 item 构造为 KP 或 Question（纯 CPU，无 IO）。

        REC-10：去掉逐条 find_duplicates 预检——去重计数移到 ``_flush_batch`` 批量预查，
        DB 的 content_hash UNIQUE + INSERT OR IGNORE 仍是幂等兜底。
        """
        annotation = msg.annotation
        item = msg.item

        # 强制学科根（兜底防线 —— LLM 偶尔从中层概念起头）
        annotation = self._ensure_discipline_root(annotation, item_id=msg.item_id)

        # tag 受控词表统一（#6）：归并同义异名，使全局 tag tree 一致细粒度
        canon_updates: dict[str, object] = {
            "tags": self.tag_canon.canonicalize_path(annotation.tags)
        }
        if annotation.methods:
            canon_updates["methods"] = self.tag_canon.canonicalize_path(annotation.methods)
        annotation = annotation.model_copy(update=canon_updates)

        # Taxonomy 校验 + 模糊匹配（REC-15：限定本学科，杜绝跨学科 fuzzy 漂移）
        corrected_taxonomy, _ = self.taxonomy_validator.validate(
            annotation.taxonomy_path, discipline=self.discipline.value
        )
        if corrected_taxonomy and corrected_taxonomy != annotation.taxonomy_path:
            annotation = annotation.model_copy(update={"taxonomy_path": corrected_taxonomy})

        # 累积 tag tree（集中在 Stage3）
        self.tag_tree.insert_path(annotation.tags)

        if item.category == "knowledge":
            kp_type = item.item_type
            if kp_type not in self.discipline_spec.allowed_kp_types:
                logger.warning(
                    "stage3_unknown_kp_type",
                    kp_type=kp_type,
                    allowed=list(self.discipline_spec.allowed_kp_types),
                )
                kp_type = self.discipline_spec.allowed_kp_types[0]
            kp = KnowledgePoint(
                discipline=self.discipline,
                kp_type=kp_type,
                number=item.number,
                content=item.corrected_content,
                proof_content=item.corrected_proof,
                difficulty=annotation.difficulty,
                subject=annotation.subject,
                taxonomy_path=annotation.taxonomy_path,
                tags=list(annotation.tags),
                source_file=msg.source_file,
                start_line=item.start_line,
                end_line=item.end_line,
                # v11 §F：字符级溯源（WP-14 resolve_span 填充，未启用时 0=行级）+ 页码
                start_char=int(getattr(item, "resolved_start_char", 0) or 0),
                end_char=int(getattr(item, "resolved_end_char", 0) or 0),
                page=msg.page_start,
                order_index=msg.item_id,
            )
            return kp, None

        q_type = annotation.question_type or item.item_type
        if q_type not in self.discipline_spec.allowed_question_types:
            logger.warning(
                "stage3_unknown_question_type",
                q_type=q_type,
                allowed=list(self.discipline_spec.allowed_question_types),
            )
            q_type = self.discipline_spec.allowed_question_types[0]
        # 题目形式（#5 与认知类别正交）：Stage1 从题面识别，校验白名单
        q_form = self.discipline_spec.question_form_or(getattr(item, "question_form", "") or "", "")
        q = Question(
            discipline=self.discipline,
            question_type=q_type,
            question_form=q_form,
            number=item.number,
            content=item.corrected_content,
            solution_content=item.corrected_solution,
            # REC-19：结构化题型字段从 Stage1 提取直通入库
            options=list(getattr(item, "options", []) or []),
            correct_option=getattr(item, "correct_option", None),
            blanks=getattr(item, "blanks", None),
            difficulty=annotation.difficulty,
            subject=annotation.subject,
            taxonomy_path=annotation.taxonomy_path,
            tags=list(annotation.tags),
            source_file=msg.source_file,
            start_line=item.start_line,
            end_line=item.end_line,
            # v11 §F：字符级溯源（WP-14 resolve_span 填充，未启用时 0=行级）+ 页码
            start_char=int(getattr(item, "resolved_start_char", 0) or 0),
            end_char=int(getattr(item, "resolved_end_char", 0) or 0),
            page=msg.page_start,
            order_index=msg.item_id,
        )
        return None, q

    async def _flush_batch(
        self, buffer: list[ReviewedAnnotatedItem], state: FileExtractionState
    ) -> None:
        """REC-10：一次 save_batch+commit 入库整批，批量预查去重计数，单次 mirror+state。"""
        if not buffer:
            return
        kps: list[KnowledgePoint] = []
        qs: list[Question] = []
        kp_contents: list[str] = []
        q_contents: list[str] = []
        figures: list[Figure] = []
        for msg in buffer:
            kp, q = self._build_persistable(msg)
            if kp is not None:
                kps.append(kp)
                kp_contents.append(msg.item.corrected_content)
                figures.extend(_extract_figures(kp.content, kp.id, "knowledge", kp.source_file))
            if q is not None:
                qs.append(q)
                q_contents.append(msg.item.corrected_content)
                figures.extend(_extract_figures(q.content, q.id, "question", q.source_file))

        # 批量去重计数（每类一次 SELECT，替代逐条 find_duplicates）
        existing = 0
        if kp_contents:
            existing += await asyncio.to_thread(
                self.storage.count_existing_content, kp_contents, "knowledge"
            )
        if q_contents:
            existing += await asyncio.to_thread(
                self.storage.count_existing_content, q_contents, "question"
            )

        attempted = len(kps) + len(qs)
        await asyncio.to_thread(self.storage.save_batch, kps, qs)
        # REC-18：带图条目的图片元数据落库（content 仍保留 ![](path) 链接）
        if figures:
            await asyncio.to_thread(self.storage.save_figures, figures)

        state.stage3_persisted_count += max(0, attempted - existing)
        state.stage3_deduped_count += existing
        state.last_persisted_item_id = max(
            state.last_persisted_item_id, max(m.item_id for m in buffer)
        )
        for msg in buffer:
            self._mirror(msg, state)
        await save_state_locked(self.checkpoint_root, state)

        logger.info(
            "stage3_flush",
            count=len(buffer),
            persisted_new=max(0, attempted - existing),
            deduped=existing,
        )

    def _ensure_discipline_root(
        self,
        annotation: SingleAnnotationLLM,
        item_id: int,
    ) -> SingleAnnotationLLM:
        """兜底补齐学科根：tags[0] = spec.tag_root，taxonomy_path 以 discipline.value 起头。

        当 LLM 输出 `["combinatorics:组合数学", ...]` 缺根、或 `taxonomy_path` 为
        `combinatorics.counting` 缺学科前缀时，统一补齐到本次 pipeline 的学科根。
        若首段已是其他**已注册**学科的根（疑似跨学科误标），保留原值并 warning，
        避免覆盖语义。
        """
        tag_root = self.discipline_spec.tag_root  # e.g. "math:数学"
        discipline_key = self.discipline.value  # e.g. "math"
        updates: dict[str, object] = {}

        # ── tags 补根 ─────────────────────────────────
        tags = list(annotation.tags)
        if not tags or tags[0] != tag_root:
            first_seg_english = tags[0].split(":", 1)[0] if tags else ""
            cross_discipline = False
            if first_seg_english and first_seg_english != discipline_key:
                try:
                    other = Discipline(first_seg_english)
                    cross_discipline = other != self.discipline and DISCIPLINE_REGISTRY.has(other)
                except ValueError:
                    cross_discipline = False

            if cross_discipline:
                logger.warning(
                    "stage3_tag_root_cross_discipline",
                    item_id=item_id,
                    first=tags[0] if tags else None,
                    expected=tag_root,
                )
            else:
                # 去重后前置 root（保留原始顺序）
                deduped = [t for t in tags if t != tag_root]
                new_tags = [tag_root, *deduped]
                updates["tags"] = new_tags
                logger.info(
                    "stage3_tag_root_normalized",
                    item_id=item_id,
                    original_first=tags[0] if tags else None,
                    new_first=tag_root,
                )

        # ── taxonomy_path 补根 ────────────────────────
        tax = annotation.taxonomy_path.strip()
        if tax and tax != discipline_key and not tax.startswith(discipline_key + "."):
            first_seg = tax.split(".", 1)[0]
            cross_tax = False
            if first_seg and first_seg != discipline_key:
                try:
                    other = Discipline(first_seg)
                    cross_tax = other != self.discipline and DISCIPLINE_REGISTRY.has(other)
                except ValueError:
                    cross_tax = False

            if cross_tax:
                logger.warning(
                    "stage3_taxonomy_cross_discipline",
                    item_id=item_id,
                    path=tax,
                    expected_root=discipline_key,
                )
            else:
                new_tax = f"{discipline_key}.{tax}"
                updates["taxonomy_path"] = new_tax
                logger.info(
                    "stage3_taxonomy_root_normalized",
                    item_id=item_id,
                    original=tax,
                    corrected=new_tax,
                )

        if updates:
            return annotation.model_copy(update=updates)
        return annotation

    def _mirror(self, msg: ReviewedAnnotatedItem, state: FileExtractionState) -> None:
        if self.checkpoint_root is None or not state.file_hash:
            return
        target_dir = Path(self.checkpoint_root) / state.file_hash / "items" / "persisted"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            fname = f"item_{msg.item_id:06d}.json"
            (target_dir / fname).write_text(msg.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("stage3_persist_mirror_failed", error=str(exc))
