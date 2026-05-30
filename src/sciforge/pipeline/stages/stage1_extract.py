"""Stage1Extract — 批量滑动窗口提取（v3）

职责：
- 从 q_md 取 MdBlockSource，按累计全局行号注入 line_buffer
- 滑动窗口（默认 500 行，可扩到 3000），每窗口**一次** LLM 调用提取**所有完整**条目
- 提取成功 → 连续分配 item_ids、滑动到 last_consumed_line+1、emit 一个 ExtractedBatch 到 q_raw
- 窗口为空提取 → 按 expand_step 扩大；达 max_window 后连续 N 次空 → force consume
- 每条 item 镜像到 <checkpoint>/<hash>/items/stage1/item_<id>.json（dict 格式）

输出 schema: `BatchItemExtractPayloadLLM`（items 数组 + stop_reason + last_consumed_line）。
不完整条目（截断/未闭合解答/不确定有解）**不进 items**，留给下一窗口
（stop_reason="incomplete_at_tail"）。
"""

from __future__ import annotations

import asyncio
import bisect
import json as _json_mod
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from sciforge.core.models import (
    Discipline,
    ExtractedBatch,
    FileExtractionState,
    MdBlockSource,
)
from sciforge.core.sentinel import SENTINEL, _Sentinel
from sciforge.pipeline.llm_schemas import BatchItemExtractPayloadLLM
from sciforge.pipeline.resume_state import save_state_locked
from sciforge.pipeline.retry import (
    RetryExhausted,
    call_llm_json_with_schema_retry_with_feedback,
)
from sciforge.prompts.templates import get_extract_stage1_initial_template

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()

# 条目起始标记预检（用于智能窗口跳过）
# 设计原则：宁可 false-positive（多调一次 LLM 验证，无害）也不 false-negative（漏条目）。
# 中文 OCR 教材常见格式：
#   "定义 1．2．2"（全角句号，空格分隔）
#   "定理1.1.2"（连写，半角句号）
#   "例1．2．7"（例、例题；可含或不含空格）
#   "**定义 1.1**"（带 markdown 粗体）
# 因此**不强制** `**` 前缀，只要求数字紧跟条目关键字。
_ITEM_MARKER_RE = re.compile(
    r"(?:"
    # LaTeX 环境
    r"\\begin\{(?:theorem|definition|lemma|corollary|proposition|"
    r"property|example|problem|exercise|remark|proof|solution)\}"
    # 中文关键字 + 可选空格 + 数字（允许半角或全角数字，允许可选 ** 粗体）
    # 关键字后允许 0-2 个空白字符（半角/全角），必须跟着一个数字或字母 X（如 "定义 X"）
    r"|(?:\*\*\s*)?"
    r"(?:定[理义]|引理|推论|命题|性质|公式|例题?|习题|问题|注记?|备注|方法|"
    r"结论|法则|算法)"
    r"[\s\u00a0\u3000]{0,3}[\d\uff10-\uff19X]"
    # 英文标题 + 编号
    r"|(?:Theorem|Definition|Lemma|Corollary|Proposition|Property|"
    r"Example|Exercise|Problem|Remark|Formula|Method|Algorithm)"
    r"\s+[\d.]"
    r")",
    re.IGNORECASE,
)


class Stage1Extract:
    """批量提取 + 滑动窗口"""

    def __init__(
        self,
        llm_client: LLMClient,
        config: AppConfig,
        checkpoint_root: Path | None = None,
        discipline: Discipline = Discipline.MATH,
        order_index_allocator: Any | None = None,
    ) -> None:
        self.llm = llm_client
        self.config = config
        self.checkpoint_root = checkpoint_root
        self.discipline = discipline
        self.order_index_allocator = order_index_allocator
        self.prompt_template = get_extract_stage1_initial_template(discipline)

        # Runtime 状态
        self.line_buffer: list[str] = []
        self.line_offset = 0
        self.next_item_id = 0
        self.current_window_size: int = int(getattr(config, "extract_window_lines", 500))
        self.consecutive_empty = 0

        # Config 派生常量
        self.base_window: int = int(getattr(config, "extract_window_lines", 500))
        self.max_window: int = max(
            int(getattr(config, "extract_max_window_lines", 3000)),
            self.base_window,
        )
        self.expand_step: int = int(getattr(config, "extract_window_expand_step", 500))
        self.empty_force_limit: int = int(getattr(config, "extract_empty_force_limit", 2))
        self.max_retries: int = int(getattr(config, "stage1_extract_max_retries", 3))
        self.retry_base_delay: float = float(getattr(config, "extract_retry_base_delay", 2.0))
        # 429 rate limit 配置
        self.rate_limit_base_sleep: float = float(
            getattr(config, "rate_limit_base_sleep", 300.0)
        )
        self.rate_limit_max_sleep: float = float(
            getattr(config, "rate_limit_max_sleep", 1800.0)
        )
        self.rate_limit_total_budget: float = float(
            getattr(config, "rate_limit_total_budget", 21600.0)
        )

        self._prev_tail_summary: str = ""
        self._recent_item_examples: list[str] = []
        self._max_examples = 3
        self.fast_skip_enabled: bool = bool(
            getattr(config, "empty_window_fast_skip", True)
        )

    # ── 主循环 ─────────────────────────────────────────

    async def run(
        self,
        in_queue: asyncio.Queue[Any],
        out_queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> None:
        """从 q_md 消费 MdBlockSource → 滑动窗口提取 → emit 到 q_raw"""
        self._restore_state(state)
        sentinel_seen = False
        current_block: MdBlockSource | None = None

        try:
            while not sentinel_seen:
                msg = await in_queue.get()

                if isinstance(msg, _Sentinel):
                    sentinel_seen = True
                    is_last = True
                else:
                    if not isinstance(msg, MdBlockSource):
                        logger.warning("stage1_extract_unexpected_msg", type=type(msg).__name__)
                        continue
                    current_block = msg

                    if current_block.block_index < state.current_block_idx:
                        logger.debug(
                            "stage1_skip_block_checkpoint",
                            block=current_block.block_index,
                            until=state.current_block_idx,
                        )
                        continue
                    if current_block.block_index in state.failed_blocks:
                        logger.debug("stage1_skip_failed_block", block=current_block.block_index)
                        continue

                    start_no = self.line_offset + len(self.line_buffer) + 1
                    new_lines = self._annotate(current_block.md_text, start_no)
                    self.line_buffer.extend(new_lines)
                    state.current_block_idx = current_block.block_index + 1
                    await self._sync_state(state, force=True)
                    is_last = False

                    logger.debug(
                        "stage1_block_loaded",
                        block=current_block.block_index,
                        lines=len(new_lines),
                        buffer_size=len(self.line_buffer),
                    )

                await self._process_windows(state, current_block, is_last, out_queue)

                if not isinstance(msg, _Sentinel):
                    await self._sync_state(state)

            # 循环结束，残留处理已在 is_last 路径内
            if self.line_buffer:
                logger.warning(
                    "stage1_residual_buffer",
                    residual_lines=len(self.line_buffer),
                    line_offset=self.line_offset,
                )

            state.stage1_completed = True
            await self._sync_state(state, force=True)
            logger.info(
                "stage1_extract_complete",
                extracted=state.stage1_extracted_count,
                next_item_id=self.next_item_id,
                failed_blocks=len(state.failed_blocks),
            )
        finally:
            # 无论成功/异常都把 SENTINEL 传递给下游，避免 Stage1Review 死锁
            await out_queue.put(SENTINEL)

    # ── 窗口处理 ────────────────────────────────────────

    async def _process_windows(
        self,
        state: FileExtractionState,
        current_block: MdBlockSource | None,
        is_last: bool,
        out_queue: asyncio.Queue[Any],
    ) -> None:
        while True:
            if not self.line_buffer:
                break

            if len(self.line_buffer) < self.current_window_size and not is_last:
                break

            window_len = min(self.current_window_size, len(self.line_buffer))
            if window_len == 0:
                break
            window = self.line_buffer[:window_len]
            window_last_line = self._line_no_of(window[-1])
            window_last_int = int(window_last_line) if isinstance(window_last_line, int) else 0
            window_start_int = self.line_offset + 1

            # ── 第一层：条目标记预检（zero-cost 跳过无条目区域）────
            has_markers = _ITEM_MARKER_RE.search("".join(window)) is not None
            if self.fast_skip_enabled and not has_markers and not is_last:
                # 无条目标记且不是最后窗口 → 直接跳过，不调 LLM
                slide = window_len  # 跳整个窗口（比只跳 base_window 更激进）
                logger.info(
                    "stage1_window_fast_skip",
                    window_lines=window_len,
                    line_range=f"{window_start_int}-{window_last_int}",
                    slide=slide,
                )
                self.line_buffer = self.line_buffer[slide:]
                self.line_offset += slide
                self.current_window_size = self.base_window
                self.consecutive_empty = 0
                await self._sync_state(state, force=True)
                continue

            try:
                payload = await self._extract_batch(window, window_start_int, window_last_int)
            except RetryExhausted as exc:
                # 判断失败根因：429 / rate-limit 类（临时性）vs schema / content（永久性）
                from sciforge.core.exceptions import LLMRateLimitError

                is_rate_limit = isinstance(exc.last_exc, LLMRateLimitError)
                logger.error(
                    "stage1_window_failed",
                    block=current_block.block_index if current_block else -1,
                    line_range=f"{self.line_offset + 1}-{self.line_offset + window_len}",
                    attempts=exc.attempts,
                    reason="rate_limit_budget_exhausted" if is_rate_limit else "other",
                    last_error=str(exc.last_exc)[:200] if exc.last_exc else "",
                )
                if is_rate_limit:
                    # 429 预算耗尽：**不要**把 block 标记为 failed（它不是真的失败），
                    # 直接中断当前文件，保留 state.next_item_id / line_buffer 等信息，
                    # 等用户重启 pipeline 时续跑。记录为 state.errors 以便观察。
                    err_msg = (
                        f"rate_limit_budget_exhausted at block="
                        f"{current_block.block_index if current_block else -1}, "
                        f"line_range={self.line_offset + 1}-{self.line_offset + window_len}"
                    )
                    if err_msg not in state.errors:
                        state.errors.append(err_msg)
                    await self._sync_state(state, force=True)
                    # 抛出异常让 orchestrator 终止整个文件的处理
                    raise
                # 非 rate-limit：保留原行为，把 block 加入 failed_blocks
                if (
                    current_block is not None
                    and current_block.block_index not in state.failed_blocks
                ):
                    state.failed_blocks.append(current_block.block_index)
                # 跳过本窗口内容
                slide = min(self.base_window, window_len)
                self.line_buffer = self.line_buffer[slide:]
                self.line_offset += slide
                self.current_window_size = self.base_window
                self.consecutive_empty = 0
                await self._sync_state(state, force=True)
                break

            if not payload.items:
                # 未提到条目 → 扩窗口或 force consume
                self.consecutive_empty += 1
                at_max = self.current_window_size >= self.max_window

                # ── 第三层：no_items_found + 无标记 → 快速跳过整个窗口 ──
                if (
                    self.fast_skip_enabled
                    and payload.stop_reason == "no_items_found"
                    and not has_markers
                ):
                    slide = window_len  # LLM 确认无条目 + regex 也无标记 → 跳整个窗口
                    logger.info(
                        "stage1_no_items_fast_skip",
                        window=self.current_window_size,
                        slide=slide,
                    )
                    self.line_buffer = self.line_buffer[slide:]
                    self.line_offset += slide
                    self.current_window_size = self.base_window
                    self.consecutive_empty = 0
                    await self._sync_state(state, force=True)
                    continue

                # ── 第二层：改进 force-consume ──
                if at_max and self.consecutive_empty >= self.empty_force_limit:
                    # 判断是否有不完整尾部条目
                    if (
                        payload.stop_reason == "incomplete_at_tail"
                        and payload.last_consumed_line > 0
                    ):
                        # 只跳到已消费位置，保留不完整尾部给下一窗口
                        cut = self._find_buffer_idx(payload.last_consumed_line)
                        slide = (cut + 1) if 0 <= cut < window_len else window_len
                    else:
                        # 纯空 → 跳整个窗口（而非原来的 base_window）
                        slide = min(self.current_window_size, window_len)
                    logger.warning(
                        "stage1_force_consume",
                        window=self.current_window_size,
                        consecutive_empty=self.consecutive_empty,
                        slide=slide,
                        stop_reason=payload.stop_reason,
                        preserved_tail=payload.stop_reason == "incomplete_at_tail",
                    )
                    self.line_buffer = self.line_buffer[slide:]
                    self.line_offset += slide
                    self.current_window_size = self.base_window
                    self.consecutive_empty = 0
                    await self._sync_state(state, force=True)
                    continue
                if not at_max:
                    next_size = min(self.current_window_size + self.expand_step, self.max_window)
                    logger.debug(
                        "stage1_window_expand",
                        old=self.current_window_size,
                        new=next_size,
                        buffer=len(self.line_buffer),
                        stop_reason=payload.stop_reason,
                    )
                    self.current_window_size = next_size
                    await self._sync_state(state, force=True)
                    if len(self.line_buffer) >= self.current_window_size or is_last:
                        continue
                    break
                # 已达 max 但未到 force_limit，且非末尾 → 等下一 block
                await self._sync_state(state, force=True)
                if is_last:
                    continue
                break

            # 提到一批 → 分配连续 id、滑动到 last_consumed_line+1、emit 一个 ExtractedBatch
            self.consecutive_empty = 0
            items = payload.items
            # 上 LLM schema 已校验 last_consumed_line == items[-1].end_line（非空时）
            last_end_line = payload.last_consumed_line

            cut_idx = self._find_buffer_idx(last_end_line)
            if 0 <= cut_idx < window_len:
                consumed = cut_idx + 1
            else:
                consumed = window_len

            # 连续分配 item_ids。目录并发模式下必须从共享 allocator 动态预留，
            # 不能在文件启动时只拿一个起点，否则不同文件会分配重叠 ID。
            item_ids = await self._allocate_item_ids(len(items))
            first_id = item_ids[0]

            window_range = (self.line_offset + 1, self.line_offset + window_len)
            window_copy = list(window)

            batch = ExtractedBatch(
                item_ids=item_ids,
                items=items,
                window_lines=window_copy,
                window_line_range=window_range,
                window_last_line=window_last_int,
                block_index=current_block.block_index if current_block else -1,
                source_file=current_block.file_path if current_block else "",
                page_start=current_block.page_start if current_block else None,
                page_end=current_block.page_end if current_block else None,
                stop_reason=payload.stop_reason,
            )

            self._mirror_batch(batch, state, stage_dir="stage1")
            await out_queue.put(batch)
            state.stage1_extracted_count += len(items)

            tail_item = items[-1]
            self._prev_tail_summary = (
                f"批量提取 {len(items)} 条，最后一条："
                f"类型 {tail_item.category}/{tail_item.item_type}, 编号 {tail_item.number}, "
                f"行号 {tail_item.start_line}-{tail_item.end_line}, "
                f"solution_status: {tail_item.solution_status}; "
                f"stop_reason: {payload.stop_reason}"
            )
            for it in items:
                self._update_examples(it)

            logger.info(
                "stage1_extract_batch_emitted",
                count=len(items),
                first_id=first_id,
                last_id=item_ids[-1],
                last_end_line=last_end_line,
                stop_reason=payload.stop_reason,
            )

            self.line_buffer = self.line_buffer[consumed:]
            self.line_offset += consumed
            self.current_window_size = self.base_window
            await self._sync_state(state, force=True)

    # ── LLM 调用 ──────────────────────────────────────

    async def _extract_batch(
        self, window: list[str], window_start_line: int, window_last_line: int
    ) -> BatchItemExtractPayloadLLM:
        """单次 LLM 调用，批量提取窗口内**所有完整**条目。

        不完整（截断/未闭合解答/不确定有解）的条目**不进 items**，留给下一窗口。
        """
        chunk_text = "".join(window)

        extra_sections: list[str] = []
        extra_sections.append(
            "## 当前窗口行号范围（**反跳过校验基准**）\n"
            f"- 起点行号：{window_start_line}\n"
            f"- 末行行号：{window_last_line}\n"
            f"**要求**：`items[0].start_line` 必须接近 {window_start_line}（窗口顶端）；"
            "起点到 items[0].start_line 之间、相邻 items 之间**不得遗留**任何条目起始标记"
            "（`**定义/例/性质/引理/命题/公式/推论/注 X.Y**` / "
            "`\\begin{definition/example/property/...}` 等）"
        )
        if self._recent_item_examples:
            examples_json = ",\n    ".join(self._recent_item_examples)
            extra_sections.append(
                f"## 前序已提取的条目示例（字段格式参照，不要重复提取）\n[{examples_json}]"
            )
        if self._prev_tail_summary:
            extra_sections.append(
                "## 前一批已提取条目（**编号连续性基准**）\n"
                f"{self._prev_tail_summary}\n"
                "**要求**：本批 `items[0]` 的 number 应连续延续上一批最后一条（按教材编号规律）；"
                "若出现跳号（如前一批末 3.1，本批 items[0] 是 3.3），说明 3.2 就在当前窗口内，"
                "必须回窗口顶端重新扫描，先提取跳过的那条；"
                "items 内部相邻两条的 number 也必须连续"
            )

        def build_prompt(feedback: str) -> str:
            retry_block = ""
            if feedback:
                retry_block = f"## 上一次输出被拒绝，错误信息（请修正）\n{feedback}\n"
            base = self.prompt_template.format(
                current_block=chunk_text,
                retry_feedback=retry_block,
            )
            if extra_sections:
                return "\n".join(extra_sections) + "\n\n" + base
            return base

        t0 = time.monotonic()
        result = await call_llm_json_with_schema_retry_with_feedback(
            self.llm,
            build_prompt,
            model=BatchItemExtractPayloadLLM,
            max_attempts=self.max_retries,
            base_delay=self.retry_base_delay,
            validation_context={"window_last_line": window_last_line},
            stage_name="stage1_extract",
            rate_limit_base_sleep=self.rate_limit_base_sleep,
            rate_limit_max_sleep=self.rate_limit_max_sleep,
            rate_limit_total_budget=self.rate_limit_total_budget,
        )
        latency = time.monotonic() - t0
        logger.info(
            "stage1_extract_done",
            window_lines=len(window),
            items_count=len(result.items),
            stop_reason=result.stop_reason,
            last_consumed_line=result.last_consumed_line,
            latency_sec=round(latency, 2),
        )
        return result

    # ── 产出工具 ────────────────────────────────────────

    def _mirror_batch(
        self, batch: ExtractedBatch, state: FileExtractionState, *, stage_dir: str
    ) -> None:
        """把批次中每条 item 单独镜像为 item_<id>.json（兼容旧 checkpoint 浏览习惯）"""
        if self.checkpoint_root is None or not state.file_hash:
            return
        target_dir = Path(self.checkpoint_root) / state.file_hash / "items" / stage_dir
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            for item_id, item in zip(batch.item_ids, batch.items, strict=True):
                fname = f"item_{item_id:06d}.json"
                snapshot = {
                    "item_id": item_id,
                    "item": item.model_dump(),
                    "window_line_range": list(batch.window_line_range),
                    "window_last_line": batch.window_last_line,
                    "block_index": batch.block_index,
                    "source_file": batch.source_file,
                    "page_start": batch.page_start,
                    "page_end": batch.page_end,
                }
                (target_dir / fname).write_text(
                    _json_mod.dumps(snapshot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("stage1_mirror_failed", error=str(exc))

    def _update_examples(self, item: Any) -> None:
        compact = {
            "category": item.category,
            "item_type": item.item_type,
            "number": item.number,
            "solution_status": item.solution_status,
            "corrected_content_preview": (item.corrected_content or "")[:200] + "...",
        }
        self._recent_item_examples.append(_json_mod.dumps(compact, ensure_ascii=False))
        if len(self._recent_item_examples) > self._max_examples:
            self._recent_item_examples.pop(0)

    async def _allocate_item_ids(self, count: int) -> list[int]:
        """为一个批次预留连续 item_id。"""
        if count <= 0:
            return []
        if self.order_index_allocator is None:
            first_id = self.next_item_id
        else:
            first_id = await self.order_index_allocator.reserve(count)
        self.next_item_id = max(self.next_item_id, first_id + count)
        return list(range(first_id, first_id + count))

    # ── state 恢复 ──────────────────────────────────────

    def _restore_state(self, state: FileExtractionState) -> None:
        self.line_buffer = list(state.line_buffer_carry)
        self.line_offset = state.line_offset
        self.next_item_id = state.next_item_id
        self.current_window_size = state.current_window_size or self.base_window
        self.consecutive_empty = state.consecutive_empty

    async def _sync_state(self, state: FileExtractionState, *, force: bool = False) -> None:
        state.line_buffer_carry = list(self.line_buffer)
        state.line_offset = self.line_offset
        state.next_item_id = self.next_item_id
        state.current_window_size = self.current_window_size
        state.consecutive_empty = self.consecutive_empty
        await save_state_locked(self.checkpoint_root, state, force=force)

    @staticmethod
    def _annotate(md_text: str, start_line_no: int) -> list[str]:
        """给每行加全局行号前缀"""
        lines = md_text.splitlines(keepends=True)
        return [f"{start_line_no + i:>7d} | {ln}" for i, ln in enumerate(lines)]

    def _find_buffer_idx(self, global_line_no: int) -> int:
        if not self.line_buffer:
            return -1

        def line_no_of(line: str) -> int:
            prefix = line.split("|", 1)[0].strip()
            return int(prefix) if prefix.isdigit() else -1

        first_no = line_no_of(self.line_buffer[0])
        if first_no < 0:
            return -1
        idx = global_line_no - first_no
        if 0 <= idx < len(self.line_buffer):
            if line_no_of(self.line_buffer[idx]) == global_line_no:
                return idx
        line_numbers = [line_no_of(ln) for ln in self.line_buffer]
        pos = bisect.bisect_left(line_numbers, global_line_no)
        if 0 <= pos < len(line_numbers) and line_numbers[pos] == global_line_no:
            return pos
        return -1

    @staticmethod
    def _line_no_of(line: str) -> int | str:
        prefix = line.split("|", 1)[0].strip()
        return int(prefix) if prefix.isdigit() else prefix
