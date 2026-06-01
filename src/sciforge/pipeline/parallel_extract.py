"""并行段提取协调器（Phase 6 #1 单文档内并行）

把整篇文档用 :func:`document_segmenter.segment_document` 切成独立段后，**并发**对各段
提取，再用 :func:`document_segmenter.assign_order_index` 按 (段序, 段内序) 重排回文档顺序。

设计要点（围绕"不漏"）：
- 段在 section 边界切分 → 条目不跨段 → 各段提取相互独立、可并行。
- ``extract_fn`` 注入（async）→ 生产环境包 ``Stage1Extract._extract_batch``，单测用 mock。
- 段 ≤ ``window_lines`` 时每段恰一次 extract 调用；更大段按 window 顺序分块（段内串行，段间并行）。
- 每段提取后断言"消费行数不超过段长"，最终 :func:`assign_order_index` 断言条目数守恒。

并发度由 ``concurrency`` 控制（``asyncio.Semaphore``）。**默认 opt-in**（见
``AppConfig.intra_doc_parallel_extract``）——默认流式串行 Stage1 不变，本路径供大文档提速。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from sciforge.pipeline.document_segmenter import assign_order_index, segment_document

logger = structlog.get_logger()

# extract_fn(window_lines, window_start_line, window_last_line) -> list[item]
ExtractFn = Callable[[list[str], int, int], Awaitable[list[Any]]]


class ParallelSegmentExtractor:
    """并发段级提取 + 保序重排"""

    def __init__(
        self,
        extract_fn: ExtractFn,
        *,
        window_lines: int = 400,
        concurrency: int = 4,
        line_no_of: Callable[[str], int] | None = None,
    ) -> None:
        self.extract_fn = extract_fn
        self.window_lines = max(1, window_lines)
        self.concurrency = max(1, concurrency)
        # 从 annotated 行取全局行号（默认解析 "NNN | text"）
        self._line_no_of = line_no_of or self._default_line_no

    @staticmethod
    def _default_line_no(line: str) -> int:
        prefix = line.split("|", 1)[0].strip()
        return int(prefix) if prefix.isdigit() else 0

    async def extract(
        self,
        lines: list[str],
        boundary_indices: list[int],
        *,
        start_order_index: int = 0,
        min_segment_lines: int = 40,
    ) -> list[tuple[int, Any]]:
        """对整篇文档并行分段提取，返回 ``[(order_index, item), ...]``（文档顺序）。"""
        segments = segment_document(
            lines,
            boundary_indices,
            min_lines=min_segment_lines,
            max_lines=self.window_lines,
        )
        if not segments:
            return []

        sem = asyncio.Semaphore(self.concurrency)

        async def run_segment(seg: Any) -> list[Any]:
            async with sem:
                return await self._extract_segment(seg)

        # gather 保持顺序：per_seg[i] 对应 segments[i]（段序）
        per_seg: list[list[Any]] = await asyncio.gather(
            *(run_segment(s) for s in segments)
        )

        result = assign_order_index(per_seg, start=start_order_index)
        logger.info(
            "parallel_extract_done",
            segments=len(segments),
            total_lines=len(lines),
            items=len(result),
            concurrency=self.concurrency,
        )
        return result

    async def _extract_segment(self, seg: Any) -> list[Any]:
        """段内提取：段 ≤ window 时一次调用；更大段按 window 顺序分块（段内串行）。"""
        items: list[Any] = []
        seg_lines = seg.lines
        for chunk_start in range(0, len(seg_lines), self.window_lines):
            chunk = seg_lines[chunk_start : chunk_start + self.window_lines]
            if not chunk:
                continue
            start_line = self._line_no_of(chunk[0])
            last_line = self._line_no_of(chunk[-1])
            chunk_items = await self.extract_fn(chunk, start_line, last_line)
            items.extend(chunk_items)
        return items
