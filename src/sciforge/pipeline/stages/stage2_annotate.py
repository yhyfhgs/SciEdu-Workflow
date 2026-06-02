"""Stage2Annotate — 单条 item 标注（tags/difficulty/subject/taxonomy_path/...）

职责：
- 从 q_rev 取 ReviewedItem
- 串行按 item_id 递增消费（queue FIFO + 单协程消费保证）
- 用 LLM 给**一条**item 打标注，输出 SingleAnnotationLLM
- emit AnnotatedItem 到 q_ann

Tag 子树注入和 taxonomy 校验由 Stage3 做最终落地；本阶段只负责打标注。
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
    ReviewedItem,
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
    get_annotate_stage2_initial_parts,
    get_annotate_stage2_initial_template,
)

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()


class Stage2Annotate:
    """单条标注"""

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
        # Prompt caching（#9）：静态指令放 system（稳定可缓存前缀），动态输入留 user。
        self.prompt_cache_enabled = bool(getattr(config, "pipeline_prompt_cache_enabled", True))
        self.system_prompt, self.user_template = build_pipeline_prompt_io(
            get_annotate_stage2_initial_parts(discipline),
            get_annotate_stage2_initial_template(discipline),
            self.prompt_cache_enabled,
        )
        self.tag_tree = tag_tree if tag_tree is not None else TagTree()
        self.taxonomy_validator = TaxonomyValidator()

        self.max_retries: int = int(getattr(config, "stage2_annotate_max_retries", 3))
        self.retry_base_delay: float = float(getattr(config, "extract_retry_base_delay", 2.0))
        self.tag_tree_max_depth: int = int(getattr(config, "tag_tree_max_depth", 4))
        self.tag_tree_max_nodes: int = int(getattr(config, "tag_tree_max_nodes", 200))
        # 预缓存全量 taxonomy（整个 run 不变）
        self._full_taxonomy_cache: str = self.taxonomy_validator.serialize_for_prompt(
            discipline=str(self.discipline), max_depth=3
        )
        # 并发化配置
        self.concurrency: int = max(
            1, int(getattr(config, "stage2_annotate_concurrency", 1))
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

    async def run(
        self,
        in_queue: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        """N-worker 并发消费：concurrency==1 时走串行（向后兼容）；>1 时走 reorder buffer。"""
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
        """单 worker 串行消费（原有逻辑）。"""
        sentinel_seen = False
        try:
            while not sentinel_seen:
                msg = await in_queue.get()
                if isinstance(msg, _Sentinel):
                    sentinel_seen = True
                    break
                if not isinstance(msg, ReviewedItem):
                    logger.warning("stage2_annotate_unexpected_msg", type=type(msg).__name__)
                    continue

                annotated = await self._annotate_one(msg, state)
                if annotated is None:
                    continue

                self._mirror(annotated, state)
                await out_queue.put(annotated)
                state.stage2_annotated_count += 1
                await save_state_locked(self.checkpoint_root, state)

            await save_state_locked(self.checkpoint_root, state, force=True)
            logger.info(
                "stage2_annotate_complete",
                annotated=state.stage2_annotated_count,
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
        """N-worker 并发消费 + ReorderBuffer 恢复顺序。"""
        from sciforge.pipeline.reorder_buffer import ReorderBuffer

        # 从当前 state 推断起点
        start_id = max(state.last_persisted_item_id + 1, 0)
        buf = ReorderBuffer(out_queue, start_id=start_id)
        # worker 私有的内部任务 queue（非 asyncio queue，而是任务派发通道）
        task_queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self.concurrency * 2)
        counter_lock = asyncio.Lock()

        async def worker(worker_id: int) -> None:
            while True:
                msg = await task_queue.get()
                if isinstance(msg, _Sentinel):
                    return
                if not isinstance(msg, ReviewedItem):
                    continue
                try:
                    annotated = await self._annotate_one(msg, state)
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "stage2_annotate_worker_error",
                        worker_id=worker_id,
                        item_id=msg.item_id,
                        error=str(exc),
                    )
                    await buf.skip(msg.item_id)
                    continue
                if annotated is None:
                    await buf.skip(msg.item_id)
                    continue
                self._mirror(annotated, state)
                await buf.put(annotated.item_id, annotated)
                async with counter_lock:
                    state.stage2_annotated_count += 1

        # 启动 worker
        workers = [
            asyncio.create_task(worker(i), name=f"stage2_annotate_worker_{i}")
            for i in range(self.concurrency)
        ]

        try:
            # 分发主循环。
            # 关键：上游（Stage1Review 拒绝、resume 重放）会产生缺失 item_id 形成 gap。
            # in_queue 内 item_id 单调递增；分发器须对每个 gap 主动 buf.skip()，否则
            # ReorderBuffer 的 _next_id 会卡在缺失 id 上，把后续所有 item 积压到 _pending
            # 直到 close() 才一次性 flush——streaming 失效且大文档内存暴涨。
            last_dispatched = start_id - 1
            while True:
                msg = await in_queue.get()
                if isinstance(msg, _Sentinel):
                    break
                if not isinstance(msg, ReviewedItem):
                    logger.warning("stage2_annotate_unexpected_msg", type=type(msg).__name__)
                    continue
                if msg.item_id > last_dispatched + 1:
                    for missing in range(last_dispatched + 1, msg.item_id):
                        await buf.skip(missing)
                if msg.item_id > last_dispatched:
                    last_dispatched = msg.item_id
                await task_queue.put(msg)

            # 通知所有 worker 终止
            for _ in range(self.concurrency):
                await task_queue.put(SENTINEL)
            await asyncio.gather(*workers)

            # flush 剩余 pending
            await buf.close()
            await save_state_locked(self.checkpoint_root, state, force=True)
            logger.info(
                "stage2_annotate_complete",
                annotated=state.stage2_annotated_count,
                mode="concurrent",
                concurrency=self.concurrency,
            )
        finally:
            await out_queue.put(SENTINEL)

    async def _annotate_one(
        self, reviewed: ReviewedItem, state: FileExtractionState
    ) -> AnnotatedItem | None:
        item_dict = reviewed.item.model_dump()
        item_json = _json_mod.dumps(item_dict, ensure_ascii=False, indent=2)
        tag_subtree = self.tag_tree.serialize_for_prompt(
            max_depth=self.tag_tree_max_depth, max_nodes=self.tag_tree_max_nodes
        )
        # 局部 taxonomy 注入：从 structural_context 推断分支
        branch_hint = self._infer_branch(reviewed)
        taxonomy_tree = self.taxonomy_validator.serialize_for_prompt(
            discipline=str(self.discipline), max_depth=3, branch_filter=branch_hint
        ) if branch_hint else self._full_taxonomy_cache
        source_genre_hint = format_baseline_hint(reviewed.source_file or "")

        def build_prompt(feedback: str) -> str:
            retry_block = ""
            if feedback:
                retry_block = f"## 上一次输出被拒绝，错误信息（请修正）\n{feedback}\n"
            return self.user_template.format(
                item_json=item_json,
                tag_subtree=tag_subtree,
                taxonomy_tree=taxonomy_tree,
                source_genre_hint=source_genre_hint,
                retry_feedback=retry_block,
            )

        latency = 0.0
        t0 = time.monotonic()
        try:
            annotation = await call_llm_json_with_schema_retry_with_feedback(
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
                stage_name="stage2_annotate",
                rate_limit_base_sleep=self.rate_limit_base_sleep,
                rate_limit_max_sleep=self.rate_limit_max_sleep,
                rate_limit_total_budget=self.rate_limit_total_budget,
            )
            latency = time.monotonic() - t0
        except RetryExhausted:
            latency = time.monotonic() - t0
            logger.warning("stage2_annotate_fallback", item_id=reviewed.item_id)
            annotation = self._fallback_annotation(reviewed)

        logger.info(
            "stage2_annotate_done",
            item_id=reviewed.item_id,
            tags=len(annotation.tags),
            latency_sec=round(latency, 2),
        )

        return AnnotatedItem(
            item_id=reviewed.item_id,
            item=reviewed.item,
            annotation=annotation,
            block_index=reviewed.block_index,
            source_file=reviewed.source_file,
            page_start=reviewed.page_start,
            page_end=reviewed.page_end,
        )

    def _infer_branch(self, reviewed: ReviewedItem) -> str | None:
        """从 structural_context 的中文章节标题推断 taxonomy 英文主分支。

        修复历史 bug：旧实现读 ``sc.chapter/section``（实际字段是
        ``chapter_title/section_title``，hasattr 永远 False），且用英文分支名
        匹配中文标题 → 永远返回 None。现用真实字段 + DisciplineSpec.branch_keywords
        中文关键词映射。命中失败返回 None（fallback 到全量 taxonomy，安全）。
        """
        sc = reviewed.item.structural_context
        if sc is None:
            return None
        return self.discipline_spec.infer_branch(
            sc.chapter_title,
            sc.section_title,
            sc.subsection_title,
        )

    def _fallback_annotation(self, reviewed: ReviewedItem) -> SingleAnnotationLLM:
        """LLM 重试耗尽时的兜底标注"""
        spec = self.discipline_spec
        fallback_subject = spec.prompt_vars.get("discipline_example_subject", "general:通用")
        fallback_taxonomy = spec.prompt_vars.get(
            "discipline_example_taxonomy", f"{self.discipline}"
        )
        fallback_tags = [
            spec.tag_root,
            "uncategorized:未分类",
            "auto_fallback:自动兜底",
        ]
        is_question = reviewed.item.category == "question"
        question_type: str | None = None
        if is_question and spec.allowed_question_types:
            question_type = spec.allowed_question_types[0]

        return SingleAnnotationLLM(
            subject=fallback_subject,
            taxonomy_path=fallback_taxonomy,
            tags=fallback_tags,
            difficulty=5,
            question_type=question_type,
            methods=[],
        )

    def _mirror(self, annotated: AnnotatedItem, state: FileExtractionState) -> None:
        if self.checkpoint_root is None or not state.file_hash:
            return
        target_dir = Path(self.checkpoint_root) / state.file_hash / "items" / "stage2"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            fname = f"item_{annotated.item_id:06d}.json"
            (target_dir / fname).write_text(annotated.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("stage2_annotate_mirror_failed", error=str(exc))
