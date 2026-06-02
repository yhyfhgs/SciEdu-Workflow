"""Stage2Review — 单条标注审核

职责：
- 从 q_ann 取 AnnotatedItem
- 用 LLM 复核 annotation：tag 格式/复用、taxonomy 存在性、question_type 一致性、difficulty 校准
- emit ReviewedAnnotatedItem 到 q_done

review 失败（RetryExhausted）→ 降级使用 Stage2Annotate 的原始 annotation
"""

from __future__ import annotations

import asyncio
import json as _json_mod
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from sciforge.core.models import (
    AnnotatedItem,
    Discipline,
    FileExtractionState,
    ReviewedAnnotatedItem,
)
from sciforge.core.sentinel import SENTINEL, _Sentinel
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.pipeline.llm_schemas import SingleAnnotationLLM
from sciforge.pipeline.resume_state import save_state_locked
from sciforge.pipeline.retry import (
    RetryExhausted,
    call_llm_json_with_schema_retry_with_feedback,
)
from sciforge.pipeline.source_classifier import format_baseline_hint
from sciforge.pipeline.tag_tree import TagTree
from sciforge.pipeline.taxonomy_validator import TaxonomyValidator
from sciforge.prompts.templates import (
    build_pipeline_prompt_io,
    get_annotate_stage2_review_parts,
    get_annotate_stage2_review_template,
)

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()

# 高危 item_type（与 Stage1Review 共享定义）
_HIGH_RISK_TYPES = frozenset({
    "theorem", "lemma", "corollary", "proposition",
    "exercise", "example", "problem",
    "property", "formula",
})


class Stage2Review:
    """单条 annotation 审核"""

    def __init__(
        self,
        llm_client: LLMClient,
        config: AppConfig,
        checkpoint_root: Path | None = None,
        discipline: Discipline = Discipline.MATH,
        tag_tree: TagTree | None = None,
    ) -> None:
        self.llm = llm_client
        self.config = config
        self.checkpoint_root = checkpoint_root
        self.discipline = discipline
        self.discipline_spec = DISCIPLINE_REGISTRY.get(discipline)
        self.prompt_cache_enabled = bool(getattr(config, "pipeline_prompt_cache_enabled", True))
        self.system_prompt, self.user_template = build_pipeline_prompt_io(
            get_annotate_stage2_review_parts(discipline),
            get_annotate_stage2_review_template(discipline),
            self.prompt_cache_enabled,
        )
        self.enabled: bool = bool(
            getattr(config, "stage2_review_enabled", True)
            and getattr(config, "annotate_stage2_review_enabled", True)
        )
        self.max_retries: int = int(getattr(config, "stage2_review_max_retries", 2))
        self.retry_base_delay: float = float(getattr(config, "extract_retry_base_delay", 2.0))
        self.conditional_skip: bool = bool(
            getattr(config, "review_conditional_enabled", True)
        )
        self.concurrency: int = max(
            1, int(getattr(config, "stage2_review_concurrency", 1))
        )
        self.rate_limit_base_sleep: float = float(
            getattr(config, "rate_limit_base_sleep", 300.0)
        )
        self.rate_limit_max_sleep: float = float(
            getattr(config, "rate_limit_max_sleep", 1800.0)
        )
        self.rate_limit_total_budget: float = float(
            getattr(config, "rate_limit_total_budget", 21600.0)
        )
        self.tag_tree = tag_tree if tag_tree is not None else TagTree()
        self.taxonomy_validator = TaxonomyValidator()
        self.tag_tree_max_depth: int = int(getattr(config, "tag_tree_max_depth", 4))
        self.tag_tree_max_nodes: int = int(getattr(config, "tag_tree_max_nodes", 200))

    async def run(
        self,
        in_queue: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        """N-worker 并发消费（concurrency==1 串行，>1 走 reorder buffer）。"""
        if self.concurrency <= 1:
            await self._run_serial(in_queue, out_queue, state)
        else:
            await self._run_concurrent(in_queue, out_queue, state)

    async def _run_serial(
        self,
        in_queue: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        sentinel_seen = False
        try:
            while not sentinel_seen:
                msg = await in_queue.get()
                if isinstance(msg, _Sentinel):
                    sentinel_seen = True
                    break
                if not isinstance(msg, AnnotatedItem):
                    logger.warning("stage2_review_unexpected_msg", type=type(msg).__name__)
                    continue

                reviewed = await self._review_one(msg)
                self._mirror(reviewed, state)
                await out_queue.put(reviewed)
                state.stage2_reviewed_count += 1
                await save_state_locked(self.checkpoint_root, state)

            await save_state_locked(self.checkpoint_root, state, force=True)
            logger.info(
                "stage2_review_complete",
                reviewed=state.stage2_reviewed_count,
                mode="serial",
            )
        finally:
            await out_queue.put(SENTINEL)

    async def _run_concurrent(
        self,
        in_queue: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        from sciforge.pipeline.reorder_buffer import ReorderBuffer

        start_id = max(state.last_persisted_item_id + 1, 0)
        buf = ReorderBuffer(out_queue, start_id=start_id)
        task_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self.concurrency * 2)
        counter_lock = asyncio.Lock()

        async def worker(worker_id: int) -> None:
            while True:
                msg = await task_queue.get()
                if isinstance(msg, _Sentinel):
                    return
                if not isinstance(msg, AnnotatedItem):
                    continue
                try:
                    reviewed = await self._review_one(msg)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "stage2_review_worker_error",
                        worker_id=worker_id,
                        item_id=msg.item_id,
                        error=str(exc),
                    )
                    await buf.skip(msg.item_id)
                    continue
                self._mirror(reviewed, state)
                await buf.put(reviewed.item_id, reviewed)
                async with counter_lock:
                    state.stage2_reviewed_count += 1

        workers = [
            asyncio.create_task(worker(i), name=f"stage2_review_worker_{i}")
            for i in range(self.concurrency)
        ]

        try:
            # 分发主循环。in_queue 内 item_id 单调递增；对每个 gap（上游拒绝/重放缺口）
            # 主动 buf.skip()，否则 ReorderBuffer 卡在缺失 id 上、后续 item 积压到 close()。
            last_dispatched = start_id - 1
            while True:
                msg = await in_queue.get()
                if isinstance(msg, _Sentinel):
                    break
                if not isinstance(msg, AnnotatedItem):
                    logger.warning("stage2_review_unexpected_msg", type=type(msg).__name__)
                    continue
                if msg.item_id > last_dispatched + 1:
                    for missing in range(last_dispatched + 1, msg.item_id):
                        await buf.skip(missing)
                if msg.item_id > last_dispatched:
                    last_dispatched = msg.item_id
                await task_queue.put(msg)

            for _ in range(self.concurrency):
                await task_queue.put(SENTINEL)
            await asyncio.gather(*workers)

            await buf.close()
            await save_state_locked(self.checkpoint_root, state, force=True)
            logger.info(
                "stage2_review_complete",
                reviewed=state.stage2_reviewed_count,
                mode="concurrent",
                concurrency=self.concurrency,
            )
        finally:
            await out_queue.put(SENTINEL)

    async def _review_one(self, annotated: AnnotatedItem) -> ReviewedAnnotatedItem:
        if not self.enabled:
            return ReviewedAnnotatedItem(
                item_id=annotated.item_id,
                item=annotated.item,
                annotation=annotated.annotation,
                block_index=annotated.block_index,
                source_file=annotated.source_file,
                page_start=annotated.page_start,
                page_end=annotated.page_end,
            )

        # 条件化跳过：低风险标注直通
        if self.conditional_skip and self._should_skip_review(annotated):
            logger.info(
                "stage2_review_skipped",
                item_id=annotated.item_id,
                reason="low_risk",
            )
            return ReviewedAnnotatedItem(
                item_id=annotated.item_id,
                item=annotated.item,
                annotation=annotated.annotation,
                block_index=annotated.block_index,
                source_file=annotated.source_file,
                page_start=annotated.page_start,
                page_end=annotated.page_end,
            )

        item_json = _json_mod.dumps(annotated.item.model_dump(), ensure_ascii=False, indent=2)
        annotation_json = _json_mod.dumps(
            annotated.annotation.model_dump(), ensure_ascii=False, indent=2
        )
        tag_subtree = self.tag_tree.serialize_for_prompt(
            max_depth=self.tag_tree_max_depth, max_nodes=self.tag_tree_max_nodes
        )
        taxonomy_tree = self.taxonomy_validator.serialize_for_prompt(
            discipline=str(self.discipline), max_depth=3
        )
        source_genre_hint = format_baseline_hint(annotated.source_file or "")

        def build_prompt(feedback: str) -> str:
            retry_block = ""
            if feedback:
                retry_block = f"## 上一次输出被拒绝，错误信息（请修正）\n{feedback}\n"
            return self.user_template.format(
                item_json=item_json,
                annotation_json=annotation_json,
                tag_subtree=tag_subtree,
                taxonomy_tree=taxonomy_tree,
                source_genre_hint=source_genre_hint,
                retry_feedback=retry_block,
            )

        try:
            t0 = time.monotonic()
            reviewed_annot = await call_llm_json_with_schema_retry_with_feedback(
                self.llm,
                build_prompt,
                model=SingleAnnotationLLM,
                system=self.system_prompt,
                max_attempts=self.max_retries,
                base_delay=self.retry_base_delay,
                validation_context={
                    "discipline_tag_root": self.discipline_spec.tag_root,
                    "discipline_taxonomy_root": self.discipline.value,
                    "discipline_allowed_branches": tuple(self.discipline_spec.allowed_branches),
                },
                stage_name="stage2_review",
                rate_limit_base_sleep=self.rate_limit_base_sleep,
                rate_limit_max_sleep=self.rate_limit_max_sleep,
                rate_limit_total_budget=self.rate_limit_total_budget,
            )
            latency = time.monotonic() - t0
            modified = reviewed_annot != annotated.annotation
            logger.info(
                "stage2_review_done",
                item_id=annotated.item_id,
                modified=modified,
                latency_sec=round(latency, 2),
            )
        except RetryExhausted:
            logger.warning("stage2_review_fallback_original", item_id=annotated.item_id)
            reviewed_annot = annotated.annotation

        return ReviewedAnnotatedItem(
            item_id=annotated.item_id,
            item=annotated.item,
            annotation=reviewed_annot,
            block_index=annotated.block_index,
            source_file=annotated.source_file,
            page_start=annotated.page_start,
            page_end=annotated.page_end,
        )

    def _should_skip_review(self, annotated: AnnotatedItem) -> bool:
        """判断标注结果是否足够低风险可以跳过 review。

        全部条件同时满足才返回 True：
        1. tags[0] 匹配 discipline root
        2. tags 第二段的英文部分在 allowed_branches 内
        3. taxonomy_path 以 discipline 前缀开头
        4. 3 ≤ difficulty ≤ 8（非极端值）
        5. item_type 不在 _HIGH_RISK_TYPES 或 category=="knowledge" 且无 question_type
        6. tag 段数在 [2, 6] 范围内
        """
        ann = annotated.annotation
        item = annotated.item

        # 1. 根标签正确
        if not ann.tags or ann.tags[0] != self.discipline_spec.tag_root:
            return False

        # 2. 第二段分支合法
        if len(ann.tags) >= 2:
            second_eng = ann.tags[1].split(":")[0] if ":" in ann.tags[1] else ann.tags[1]
            if second_eng not in self.discipline_spec.allowed_branches:
                return False

        # 3. taxonomy 前缀
        taxonomy_root = self.discipline.value
        taxonomy_path = ann.taxonomy_path.strip()
        if taxonomy_path != taxonomy_root and not taxonomy_path.startswith(taxonomy_root + "."):
            return False

        # 4. 难度非极端
        if not (3 <= ann.difficulty <= 8):
            return False

        # 5. 高危类型检查
        item_type_lower = (item.item_type or "").lower()
        is_high_risk_type = item_type_lower in _HIGH_RISK_TYPES
        if is_high_risk_type:
            # 高危类型仅在 category=="knowledge" 且无 question_type 时可跳过
            if item.category != "knowledge" or ann.question_type is not None:
                return False

        # 6. tag 段数合理
        tag_count = len(ann.tags)
        if tag_count < 2 or tag_count > 6:
            return False

        return True

    def _mirror(self, reviewed: ReviewedAnnotatedItem, state: FileExtractionState) -> None:
        if self.checkpoint_root is None or not state.file_hash:
            return
        target_dir = Path(self.checkpoint_root) / state.file_hash / "items" / "stage2_review"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            fname = f"item_{reviewed.item_id:06d}.json"
            (target_dir / fname).write_text(reviewed.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("stage2_review_mirror_failed", error=str(exc))
