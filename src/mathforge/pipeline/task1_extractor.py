"""Task1Extractor — Pipeline Consumer

从 queue 取 MdBlockSource，按累计行号注入到行缓冲，每 200 行送给 LLM 做
"提取 + 校正 + 分类 + 打标签 + 难度评估" 单次调用，结果直接入库。

核心特性:
- 累计全局行号（跨 mdblock 连续）
- TagTree 跨调用注入，提示 LLM 复用已有路径
- 单调递增的 order_index 用于后续知识图谱构建
- "incomplete" 时等下一个 mdblock 来扩展窗口
- 失败 mdblock 不阻断整体流程，记入 state.failed_blocks
- 行级 checkpoint 持久化（每次 commit 后保存）
"""

from __future__ import annotations

import asyncio
import bisect
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from mathforge.core.models import (
    FileExtractionState,
    KnowledgePoint,
    KPType,
    MdBlockSource,
    Question,
    QuestionType,
    StoredItemBatch,
)
from mathforge.pipeline.resume_state import save_state_locked
from mathforge.pipeline.retry import RetryExhausted, call_llm_json_with_retry
from mathforge.pipeline.tag_tree import TagTree
from mathforge.prompts.templates import EXTRACT_AND_CLASSIFY_PROMPT

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig
    from mathforge.llm.client import LLMClient
    from mathforge.storage.protocol import BatchStorageBackend

logger = structlog.get_logger()


# ── Sentinel for queue ──────────────────────────────


class _Sentinel:
    """Producer 通知 Consumer 流结束"""

    __slots__ = ()


SENTINEL = _Sentinel()


# ── 知识/题目类型映射 ──────────────────────────────


_KP_TYPE_MAP: dict[str, KPType] = {
    "definition": KPType.DEFINITION,
    "theorem": KPType.THEOREM,
    "lemma": KPType.LEMMA,
    "corollary": KPType.COROLLARY,
    "proposition": KPType.PROPOSITION,
    "property": KPType.PROPOSITION,
    "formula": KPType.FORMULA,
}


_Q_TYPE_MAP: dict[str, QuestionType] = {
    "proof": QuestionType.PROOF,
    "calculation": QuestionType.CALCULATION,
    "choice": QuestionType.CHOICE,
    "fill_blank": QuestionType.FILL_BLANK,
    "true_false": QuestionType.TRUE_FALSE,
    "construction": QuestionType.PROOF,
}


# ── Task1Extractor ──────────────────────────────────


class Task1Extractor:
    """Pipeline Consumer：从 queue 取 mdblock → 滑动窗口 → 提取入库"""

    def __init__(
        self,
        llm_client: LLMClient,
        config: AppConfig,
        storage: BatchStorageBackend | None,
        checkpoint_root: Path,
    ) -> None:
        self.llm = llm_client
        self.config = config
        self.storage = storage
        self.checkpoint_root = checkpoint_root

        # Runtime state
        self.tag_tree = TagTree()
        self.line_buffer: list[str] = []
        self.line_offset = 0
        self.order_index = 0

    # ── 主循环 ─────────────────────────────────────────

    async def run(
        self,
        queue: asyncio.Queue[Any],
        state: FileExtractionState,
    ) -> AsyncIterator[StoredItemBatch]:
        """从 queue 消费 MdBlockSource，按窗口提取入库

        Yields StoredItemBatch 用于进度上报。
        """
        # 恢复状态
        self._restore_state(state)

        sentinel_seen = False
        current_block: MdBlockSource | None = None

        while not sentinel_seen:
            msg = await queue.get()

            if isinstance(msg, _Sentinel):
                sentinel_seen = True
                is_last = True
            else:
                if not isinstance(msg, MdBlockSource):
                    logger.warning("task1_unexpected_msg", type=type(msg).__name__)
                    continue

                current_block = msg

                # 跳过 checkpoint 之前的 block
                if current_block.block_index < state.current_block_idx:
                    logger.debug(
                        "task1_skip_block_checkpoint",
                        block=current_block.block_index,
                        until=state.current_block_idx,
                    )
                    continue

                # 跳过 failed 集合
                if current_block.block_index in state.failed_blocks:
                    logger.debug("task1_skip_failed_block", block=current_block.block_index)
                    continue

                # 注入全局行号到 mdblock 的每一行后追加到 buffer
                start_no = self.line_offset + len(self.line_buffer) + 1
                new_lines = self._annotate(current_block.md_text, start_no)
                self.line_buffer.extend(new_lines)
                is_last = False

                logger.debug(
                    "task1_block_loaded",
                    block=current_block.block_index,
                    lines=len(new_lines),
                    buffer_size=len(self.line_buffer),
                    pages=f"{current_block.page_start}-{current_block.page_end}",
                )

            # 在 buffer 中尽可能多地处理窗口
            async for stored in self._process_windows(state, current_block, is_last):
                yield stored

            # 当前 block 处理完，更新 checkpoint
            if not isinstance(msg, _Sentinel) and current_block is not None:
                state.current_block_idx = current_block.block_index + 1
                state.line_buffer_carry = list(self.line_buffer)
                state.line_offset = self.line_offset
                state.tag_tree_json = self.tag_tree.to_json()
                state.order_index_next = self.order_index
                await save_state_locked(self.checkpoint_root, state)

        # 全部完成 — force 刷盘确保完成状态落盘
        state.phase = "completed"
        state.line_buffer_carry = list(self.line_buffer)
        state.line_offset = self.line_offset
        state.tag_tree_json = self.tag_tree.to_json()
        state.order_index_next = self.order_index
        await save_state_locked(self.checkpoint_root, state, force=True)
        logger.info(
            "task1_run_complete",
            saved_items=state.saved_item_count,
            order_index_next=self.order_index,
            failed_blocks=len(state.failed_blocks),
        )

    # ── 窗口处理 ────────────────────────────────────────

    async def _process_windows(
        self,
        state: FileExtractionState,
        current_block: MdBlockSource | None,
        is_last: bool,
    ) -> AsyncIterator[StoredItemBatch]:
        window_size = self.config.extract_window_lines

        while True:
            # buffer 不够大且不是最后一批 → 等下一个 block
            if len(self.line_buffer) < window_size and not is_last:
                break

            if not self.line_buffer:
                break

            window = self.line_buffer[:window_size]

            try:
                llm_out = await self._extract_and_classify(window)
            except RetryExhausted as exc:
                logger.error(
                    "task1_window_failed",
                    block=current_block.block_index if current_block else -1,
                    line_range=f"{self.line_offset + 1}-{self.line_offset + len(window)}",
                    attempts=exc.attempts,
                )
                if (
                    current_block is not None
                    and current_block.block_index not in state.failed_blocks
                ):
                    state.failed_blocks.append(current_block.block_index)
                # 跳过该窗口的行
                self.line_buffer = self.line_buffer[len(window):]
                self.line_offset += len(window)
                break

            trunc = llm_out.get("truncation_line")
            items = llm_out.get("items", [])

            # 处理 incomplete
            if trunc == "incomplete":
                if is_last and len(self.line_buffer) <= window_size:
                    # 最后窗口仍 incomplete → 丢弃，避免死锁
                    logger.warning(
                        "task1_final_incomplete_discard",
                        lines=len(self.line_buffer),
                    )
                    self.line_offset += len(self.line_buffer)
                    self.line_buffer = []
                    break
                else:
                    # 等下一个 block 进 queue 来扩窗
                    logger.debug(
                        "task1_incomplete_wait_next",
                        buffer_size=len(self.line_buffer),
                    )
                    break

            # 计算消费的行数：LLM 可指定 truncation_line 表示最后一个完整条目的
            # 结束行，本轮 consumed 到该行之前（不含），下一轮 window 从该行开始，
            # 确保不会在条目中间被句号切开
            if isinstance(trunc, int) and trunc > 0:
                cut_idx = self._find_buffer_idx(trunc)
                if cut_idx <= 0:
                    consumed = len(window)
                else:
                    consumed = cut_idx
            else:
                consumed = len(window)

            # 入库
            stored = await self._store_items(items, current_block, state)

            # 推进 buffer + 全局行号
            self.line_buffer = self.line_buffer[consumed:]
            self.line_offset += consumed

            yield stored

    # ── 单次 LLM 调用 ──────────────────────────────────

    async def _extract_and_classify(self, window: list[str]) -> dict[str, Any]:
        """单次 LLM 调用做：提取 + 校正 + 分类 + 打标签 + 难度评估"""
        chunk_text = "".join(window)

        tag_subtree = self.tag_tree.serialize_for_prompt(
            max_depth=self.config.tag_tree_max_depth,
            max_nodes=self.config.tag_tree_max_nodes,
        )

        # 转义 prompt 中的 { }
        safe_chunk = chunk_text.replace("{", "{{").replace("}", "}}")
        safe_subtree = tag_subtree.replace("{", "{{").replace("}", "}}")

        prompt = EXTRACT_AND_CLASSIFY_PROMPT.format(
            current_block=safe_chunk,
            tag_subtree=safe_subtree,
        )

        t0 = time.monotonic()
        result = await call_llm_json_with_retry(
            self.llm,
            prompt,
            max_tokens=32768,
            max_attempts=self.config.max_extract_retries,
            base_delay=self.config.extract_retry_base_delay,
            validate=lambda d: "items" in d,
        )
        latency = time.monotonic() - t0
        logger.info(
            "task1_llm_done",
            window_lines=len(window),
            items=len(result.get("items", [])),
            truncation=result.get("truncation_line"),
            latency_sec=round(latency, 2),
        )
        return result

    # ── 入库 ───────────────────────────────────────────

    async def _store_items(
        self,
        items: list[dict[str, Any]],
        current_block: MdBlockSource | None,
        state: FileExtractionState,
    ) -> StoredItemBatch:
        kps: list[KnowledgePoint] = []
        qs: list[Question] = []
        order_start = self.order_index
        # 快照 tag_tree，入库失败时与 order_index 一起整体回滚
        tree_snapshot = self.tag_tree.to_json()

        for it in items:
            if it.get("overall_quality") == "F":
                continue

            oi = self.order_index
            self.order_index += 1

            path = it.get("tags", [])
            if isinstance(path, list) and path:
                self.tag_tree.insert_path(path)

            if it.get("category") == "question":
                qs.append(self._build_question(it, current_block, oi))
            else:
                kps.append(self._build_kp(it, current_block, oi))

        # 入库（同步调用包成 to_thread）
        if (kps or qs) and self.storage is not None:
            try:
                await asyncio.to_thread(self.storage.save_batch, kps, qs)
                state.saved_item_count += len(kps) + len(qs)
            except Exception as exc:
                logger.error(
                    "task1_save_failed",
                    error=str(exc),
                    kps=len(kps),
                    qs=len(qs),
                )
                # 整体回滚：order_index + tag_tree 必须保持一致，
                # 否则重启后 tag_tree 与已入库条目错位
                self.order_index = order_start
                self.tag_tree = TagTree.from_json(tree_snapshot)
                state.errors.append(f"save_batch: {exc}")
                return StoredItemBatch(
                    chunk_index=state.saved_item_count,
                    line_range=(self.line_offset, self.line_offset),
                    kp_count=0,
                    question_count=0,
                    order_index_range=(order_start, order_start),
                )

        return StoredItemBatch(
            chunk_index=state.saved_item_count,
            line_range=(self.line_offset, self.line_offset),
            kp_count=len(kps),
            question_count=len(qs),
            order_index_range=(order_start, self.order_index),
        )

    def _build_kp(
        self,
        it: dict[str, Any],
        block: MdBlockSource | None,
        order_index: int,
    ) -> KnowledgePoint:
        item_type = str(it.get("item_type", "theorem"))
        kp_type = _KP_TYPE_MAP.get(item_type, KPType.THEOREM)

        ctx = it.get("structural_context") or {}

        return KnowledgePoint(
            kp_type=kp_type,
            number=str(it.get("number") or ""),
            content=str(it.get("corrected_content") or ""),
            proof_content=it.get("corrected_proof"),
            proof_methods=self._safe_list(it.get("methods")),
            difficulty=self._clamp_difficulty(it.get("difficulty", 5)),
            subject=str(it.get("subject") or ""),
            taxonomy_path=str(it.get("taxonomy_path") or ""),
            tags=self._safe_list(it.get("tags")),
            source_file=block.file_path if block else "",
            source_chapter=str(ctx.get("chapter_title") or ""),
            source_section=str(ctx.get("section_title") or ""),
            start_line=int(it.get("start_line") or 0),
            end_line=int(it.get("end_line") or 0),
            order_index=order_index,
            page_start=block.page_start if block else None,
            page_end=block.page_end if block else None,
            quality_grade=str(it.get("overall_quality") or "B"),
        )

    def _build_question(
        self,
        it: dict[str, Any],
        block: MdBlockSource | None,
        order_index: int,
    ) -> Question:
        q_type_str = str(it.get("question_type") or "proof")
        q_type = _Q_TYPE_MAP.get(q_type_str, QuestionType.PROOF)

        ctx = it.get("structural_context") or {}

        return Question(
            question_type=q_type,
            number=str(it.get("number") or ""),
            content=str(it.get("corrected_content") or ""),
            solution_content=it.get("corrected_solution"),
            solution_methods=self._safe_list(it.get("methods")),
            difficulty=self._clamp_difficulty(it.get("difficulty", 5)),
            subject=str(it.get("subject") or ""),
            taxonomy_path=str(it.get("taxonomy_path") or ""),
            tags=self._safe_list(it.get("tags")),
            source_file=block.file_path if block else "",
            source_chapter=str(ctx.get("chapter_title") or ""),
            start_line=int(it.get("start_line") or 0),
            end_line=int(it.get("end_line") or 0),
            order_index=order_index,
            page_start=block.page_start if block else None,
            page_end=block.page_end if block else None,
            quality_grade=str(it.get("overall_quality") or "B"),
        )

    # ── 工具 ───────────────────────────────────────────

    def _restore_state(self, state: FileExtractionState) -> None:
        """从 state 恢复内部 buffer / tag_tree / 计数器"""
        self.tag_tree = (
            TagTree.from_json(state.tag_tree_json) if state.tag_tree_json else TagTree()
        )
        self.order_index = state.order_index_next
        self.line_offset = state.line_offset
        self.line_buffer = list(state.line_buffer_carry)

    @staticmethod
    def _annotate(md_text: str, start_line_no: int) -> list[str]:
        """给每行加全局行号前缀: '    123 | <内容>\\n'

        7 位宽足够支持千万行文件，对任何实际教材都绰绰有余。
        """
        lines = md_text.splitlines(keepends=True)
        return [f"{start_line_no + i:>7d} | {ln}" for i, ln in enumerate(lines)]

    def _find_buffer_idx(self, global_line_no: int) -> int:
        """在 line_buffer 中查找指定全局行号对应的索引

        line_buffer 的行号是 `_annotate` 顺序加前缀的严格单调序列，
        可用二分查找。

        Returns: 行号匹配的索引（0-based），找不到返回 -1
        """
        if not self.line_buffer:
            return -1

        # 每行格式固定为 "    123 | ..."，前 7 位是行号
        def line_no_of(line: str) -> int:
            prefix = line.split("|", 1)[0].strip()
            return int(prefix) if prefix.isdigit() else -1

        # 构造行号数组用 bisect 定位
        first_no = line_no_of(self.line_buffer[0])
        if first_no < 0:
            return -1
        # 推断索引（第一行 = first_no，连续递增）
        idx = global_line_no - first_no
        if 0 <= idx < len(self.line_buffer):
            # 确认命中（防御：跨 block 注入可能有跳变）
            actual = line_no_of(self.line_buffer[idx])
            if actual == global_line_no:
                return idx
        # fallback：二分搜索
        line_numbers = [line_no_of(ln) for ln in self.line_buffer]
        pos = bisect.bisect_left(line_numbers, global_line_no)
        if 0 <= pos < len(line_numbers) and line_numbers[pos] == global_line_no:
            return pos
        return -1

    @staticmethod
    def _safe_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v) for v in value if v is not None]
        return []

    @staticmethod
    def _clamp_difficulty(value: Any) -> int:
        try:
            return max(1, min(10, int(value)))
        except (TypeError, ValueError):
            return 5
