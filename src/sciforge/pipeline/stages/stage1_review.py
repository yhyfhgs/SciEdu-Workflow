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
from sciforge.core.numbering import parse_number_components
from sciforge.core.sentinel import SENTINEL, _Sentinel
from sciforge.pipeline.llm_schemas import (
    _EXERCISE_LIKE_TYPES,
    _NEAR_TAIL_MARGIN,
    BatchReviewPayloadLLM,
)
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
        self.conditional_skip: bool = bool(getattr(config, "review_conditional_enabled", True))
        self.rate_limit_base_sleep: float = float(getattr(config, "rate_limit_base_sleep", 300.0))
        self.rate_limit_max_sleep: float = float(getattr(config, "rate_limit_max_sleep", 1800.0))
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

            # 注：stage1_completed 由 Stage1Extract 设置，此处不覆盖。
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
        """风险信号制（REC-06）：仅在**无任何风险信号**时跳过 LLM review。

        旧实现把 theorem/lemma/exercise 等几乎所有实质条目列为"高危类型"一律强审，
        使条件化跳过在真实教材上近乎失效。改为只在以下风险信号出现时才 review：

        1. 截断/不完整提取：``stop_reason != "all_complete"``（可能有内容被切走）
        2. 习题/例题声称源中无解且贴近窗口末尾：解答可能被切到下一窗口（漏解防线）
        3. 编号跳号：用确定性 ``parse_number_components`` 解析（原生支持中文前缀/全角），
           替换旧的 ``float(number.split('.')[-1])``（对"第1题"/"例5"/选项 A 一律误判）

        纯定义/定理/知识点 + 干净提取 + 编号连续 → 跳过（不再因类型一律强审）。
        """
        items = batch.items
        if not items:
            return True  # 空 batch 无需 review

        # 风险信号 1：提取不完整/截断
        if getattr(batch, "stop_reason", "all_complete") != "all_complete":
            return False

        # 风险信号 2：习题/例题贴近窗口末尾且声称源中无解（解答可能在下一窗口）
        window_last = getattr(batch, "window_last_line", 0) or 0
        if window_last > 0:
            for it in items:
                if (
                    (it.item_type or "").lower() in _EXERCISE_LIKE_TYPES
                    and it.solution_status == "no_solution_in_source"
                    and window_last - it.end_line < _NEAR_TAIL_MARGIN
                ):
                    return False

        # 风险信号 3：编号跳号（确定性解析）
        if self._has_numbering_gap(items):
            return False

        return True

    @staticmethod
    def _has_numbering_gap(items: list[Any]) -> bool:
        """用确定性 numbering 解析检查相邻条目 index 是否漏号。

        只比较能解析出 index 的条目；index 递减视为跨章重置（非漏号），
        递增 >1 视为漏号。无法解析编号（如选项 A/B/C、无编号要点）的条目跳过，
        不再像旧实现那样因解析失败一律强审。
        """
        indices: list[int] = []
        for it in items:
            _, _, idx = parse_number_components(getattr(it, "number", "") or "")
            if idx is not None:
                indices.append(idx)
        for i in range(1, len(indices)):
            if indices[i] - indices[i - 1] > 1:
                return True
        return False

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
