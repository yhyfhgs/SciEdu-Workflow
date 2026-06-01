"""Stage1Review — 批量提取结果校验（v3 批量版）

职责：
- 从 q_raw 取 ExtractedBatch（一批 N 条 items 共享 window 上下文）
- 携带原 window + items JSON 调一次 LLM 做批量审核（修正 solution 绑定/反跳过/行号/内容）
- LLM 输出 reviewed_items 长度严格等于输入；位置 i=null 表示拒绝该 item（不进 q_rev）
- 按位置拆分为单条 ReviewedItem 推 q_rev（保持 item_id 顺序）

Stage1 (Extract) 完成一批后立即进入此阶段；批量化把 N 次 LLM 调用压缩为 1 次。
"""

from __future__ import annotations

import asyncio
import json as _json_mod
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from sciforge.core.models import Discipline, ExtractedBatch, FileExtractionState, ReviewedItem
from sciforge.core.sentinel import SENTINEL, _Sentinel
from sciforge.pipeline.llm_schemas import BatchReviewPayloadLLM
from sciforge.pipeline.resume_state import save_state_locked
from sciforge.pipeline.retry import (
    RetryExhausted,
    call_llm_json_with_schema_retry_with_feedback,
)
from sciforge.prompts.templates import (
    build_pipeline_prompt_io,
    get_extract_stage1_review_parts,
    get_extract_stage1_review_template,
)

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()

# 高危 item_type：容易遗漏证明/解答/论证，必须经过 Review
_HIGH_RISK_TYPES = frozenset({
    # 可能遗漏证明
    "theorem", "lemma", "corollary", "proposition",
    # 可能遗漏解答
    "exercise", "example", "problem",
    # 可能遗漏论证/推导
    "property", "formula",
})


class Stage1Review:
    """批量 item 校验"""

    def __init__(
        self,
        llm_client: LLMClient,
        config: AppConfig,
        checkpoint_root: Path | None = None,
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        self.llm = llm_client
        self.config = config
        self.checkpoint_root = checkpoint_root
        self.discipline = discipline
        self.prompt_cache_enabled = bool(getattr(config, "pipeline_prompt_cache_enabled", True))
        self.system_prompt, self.user_template = build_pipeline_prompt_io(
            get_extract_stage1_review_parts(discipline),
            get_extract_stage1_review_template(discipline),
            self.prompt_cache_enabled,
        )
        self.enabled: bool = bool(
            getattr(config, "stage1_review_enabled", True)
            and getattr(config, "extract_review_enabled", True)
        )
        self.max_retries: int = int(getattr(config, "stage1_review_max_retries", 2))
        self.retry_base_delay: float = float(getattr(config, "extract_retry_base_delay", 2.0))
        self.conditional_skip: bool = bool(
            getattr(config, "review_conditional_enabled", True)
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
        sentinel_seen = False
        try:
            while not sentinel_seen:
                msg = await in_queue.get()
                if isinstance(msg, _Sentinel):
                    sentinel_seen = True
                    break
                if not isinstance(msg, ExtractedBatch):
                    logger.warning("stage1_review_unexpected_msg", type=type(msg).__name__)
                    continue

                reviewed_list = await self._review_batch(msg, state)
                # 按位置拆分推 q_rev（保持 item_id 顺序；None 即拒绝）
                for item_id, reviewed in zip(msg.item_ids, reviewed_list, strict=True):
                    if reviewed is None:
                        logger.info("stage1_review_rejected", item_id=item_id)
                        continue
                    out = ReviewedItem(
                        item_id=item_id,
                        item=reviewed,
                        block_index=msg.block_index,
                        source_file=msg.source_file,
                        page_start=msg.page_start,
                        page_end=msg.page_end,
                    )
                    self._mirror_item(out, state)
                    await out_queue.put(out)
                    state.stage1_reviewed_count += 1

                await save_state_locked(self.checkpoint_root, state)

            state.stage1_completed = state.stage1_completed  # 不覆盖（Stage1Extract 设置）
            await save_state_locked(self.checkpoint_root, state, force=True)
            logger.info(
                "stage1_review_complete",
                reviewed=state.stage1_reviewed_count,
            )
        finally:
            await out_queue.put(SENTINEL)

    async def _review_batch(self, batch: ExtractedBatch, state: FileExtractionState) -> list[Any]:
        """批量审核一组 items，返回长度等于 batch.items 的列表

        每项是 SingleItemExtractLLM 或 None：
        - 未启用 review → 直接返回输入 items（等价直通）
        - retry 耗尽 → 降级使用原始 items（等价直通）
        - LLM 输出长度异常 → 降级直通并 warning
        """
        if not self.enabled:
            return list(batch.items)

        # 条件化跳过：低风险 batch 直通
        if self.conditional_skip and self._should_skip_review(batch):
            logger.info(
                "stage1_review_skipped",
                first_id=batch.item_ids[0] if batch.item_ids else -1,
                count=len(batch.items),
                reason="low_risk",
            )
            return list(batch.items)

        window_text = "".join(batch.window_lines)
        items_json = _json_mod.dumps(
            [it.model_dump() for it in batch.items],
            ensure_ascii=False,
            indent=2,
        )

        def build_prompt(feedback: str) -> str:
            retry_block = ""
            if feedback:
                retry_block = f"## 上一次输出被拒绝，错误信息（请修正）\n{feedback}\n"
            return self.user_template.format(
                window_text=window_text,
                items_json=items_json,
                retry_feedback=retry_block,
            )

        try:
            t0 = time.monotonic()
            payload = await call_llm_json_with_schema_retry_with_feedback(
                self.llm,
                build_prompt,
                model=BatchReviewPayloadLLM,
                system=self.system_prompt,
                max_attempts=self.max_retries,
                base_delay=self.retry_base_delay,
                validation_context={
                    "window_last_line": batch.window_last_line,
                    "expected_review_count": len(batch.items),
                },
                stage_name="stage1_review",
                rate_limit_base_sleep=self.rate_limit_base_sleep,
                rate_limit_max_sleep=self.rate_limit_max_sleep,
                rate_limit_total_budget=self.rate_limit_total_budget,
            )
            latency = time.monotonic() - t0
        except RetryExhausted:
            logger.warning(
                "stage1_review_fallback_use_original",
                first_id=batch.item_ids[0] if batch.item_ids else -1,
                count=len(batch.items),
            )
            # 审核失败 → 降级使用 Stage1Extract 原始 items
            return list(batch.items)

        # 长度校验：LLM 必须返回 1:1 对应数组
        reviewed = payload.reviewed_items
        if len(reviewed) != len(batch.items):
            logger.warning(
                "stage1_review_length_mismatch_fallback",
                expected=len(batch.items),
                got=len(reviewed),
                first_id=batch.item_ids[0] if batch.item_ids else -1,
            )
            # 长度不匹配 → 降级直通
            return list(batch.items)

        accepted = sum(1 for r in reviewed if r is not None)
        # 检查 review 是否实际改动了原始结果
        modified_count = 0
        for orig, rev in zip(batch.items, reviewed, strict=True):
            if rev is not None and rev != orig:
                modified_count += 1
        logger.info(
            "stage1_review_done",
            count=len(batch.items),
            accepted=accepted,
            rejected=len(batch.items) - accepted,
            modified=modified_count,
            latency_sec=round(latency, 2),
        )
        return list(reviewed)

    def _should_skip_review(self, batch: ExtractedBatch) -> bool:
        """判断 batch 是否足够低风险可以跳过 review。

        全部条件同时满足才返回 True：
        1. 所有 item 的 item_type 不在 _HIGH_RISK_TYPES 中
        2. stop_reason == "all_complete"（干净提取，无截断风险）
        3. 所有 item 的 solution_status 一致
        4. batch 内 items ≤ 3（小 batch 排序错误概率低）
        5. items 之间 number 无跳号（相邻编号连续）
        """
        items = batch.items
        if not items:
            return True  # 空 batch 无需 review

        # 1. 高危类型检查
        for it in items:
            if (it.item_type or "").lower() in _HIGH_RISK_TYPES:
                return False

        # 2. 干净提取（LLM 报告 stop_reason=all_complete）
        if getattr(batch, "stop_reason", "") != "all_complete":
            return False

        # 3. solution_status 一致
        statuses = {it.solution_status for it in items}
        if len(statuses) > 1:
            return False

        # 4. 小 batch
        if len(items) > 3:
            return False

        # 5. 编号连续性（简单检查：无法做完整连续性检测，但检查有无明显跳号）
        # 跳号检查通过 number 字段的最后一个数字部分
        numbers: list[float] = []
        for it in items:
            try:
                # 取编号最后的数字部分，如 "3.2.1" → 1, "例 5" → 5
                parts = it.number.replace(" ", "").split(".")
                numbers.append(float(parts[-1]))
            except (ValueError, IndexError):
                return False  # 无法解析编号，保守不跳

        for i in range(1, len(numbers)):
            if numbers[i] - numbers[i - 1] > 1.5:
                return False  # 跳号

        return True

    def _mirror_item(self, reviewed: ReviewedItem, state: FileExtractionState) -> None:
        if self.checkpoint_root is None or not state.file_hash:
            return
        target_dir = Path(self.checkpoint_root) / state.file_hash / "items" / "stage1_review"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            fname = f"item_{reviewed.item_id:06d}.json"
            (target_dir / fname).write_text(reviewed.model_dump_json(indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("stage1_review_mirror_failed", error=str(exc))
