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
        extract_fn: ExtractFn | None = None,
        *,
        window_lines: int = 400,
        concurrency: int = 4,
        line_no_of: Callable[[str], int] | None = None,
        segment_extract_fn: Callable[[list[str]], Awaitable[list[Any]]] | None = None,
        halo_lines: int = 0,
    ) -> None:
        """
        Args:
            extract_fn: **chunk 级**提取器 ``(window_lines, start, last) -> items``。
                段 > window 时段内按 ``window_lines`` **无 carry** 顺序分块——仅适合
                每段 ≤ window 或条目不跨 chunk 的场景（单测/简单路径）。
            segment_extract_fn: **整段级**提取器 ``(seg_lines) -> items``（优先于
                ``extract_fn``）。生产路径传入 carry-aware 的滑窗提取器，使**超大 section**
                也能按滑动窗口 + carry 完整提取、不在 chunk 边界切断条目（完整性优先）。
                提供它时 ``window_lines`` 只用于 :func:`segment_document` 的过大段硬切阈值，
                生产应传较大值以**仅按 section 边界切段**。
            halo_lines: **v11 III.4 段重叠区**（默认 0 = 关，行为与现状完全一致）。
                >0 时每段（除末段）的提取输入向后多读 halo 行——跨段边界的条目
                被前段完整看到；随后 :func:`arbitrate_halo_overlap` 对相邻段重叠区
                确定性去重合并。halo 应大于典型条目长度；超长跨界由段内 carry 兜底。
        """
        if extract_fn is None and segment_extract_fn is None:
            raise ValueError("extract_fn 与 segment_extract_fn 必须至少提供一个")
        self.extract_fn = extract_fn
        self.segment_extract_fn = segment_extract_fn
        self.window_lines = max(1, window_lines)
        self.concurrency = max(1, concurrency)
        self.halo_lines = max(0, halo_lines)
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
        grouped = await self.extract_grouped(
            lines, boundary_indices, min_segment_lines=min_segment_lines
        )
        per_seg = [items for _, items in grouped]
        result = assign_order_index(per_seg, start=start_order_index)
        logger.info(
            "parallel_extract_done",
            segments=len(grouped),
            total_lines=len(lines),
            items=len(result),
            concurrency=self.concurrency,
        )
        return result

    async def extract_grouped(
        self,
        lines: list[str],
        boundary_indices: list[int],
        *,
        min_segment_lines: int = 40,
    ) -> list[tuple[Any, list[Any]]]:
        """同 :meth:`extract`，但**保留每段分组** ``[(DocSegment, [items]), ...]``。

        供需要按段携带 window 上下文 emit 的调用方（如 Stage1Extract 并行路径）使用：
        ``extract`` 会把各段展平，丢失段→window 的对应关系。
        """
        segments = segment_document(
            lines,
            boundary_indices,
            min_lines=min_segment_lines,
            max_lines=self.window_lines,
        )
        if not segments:
            return []

        sem = asyncio.Semaphore(self.concurrency)
        n = len(lines)

        async def run_segment(seg: Any) -> list[Any]:
            async with sem:
                # v11 III.4：halo——除末段外，提取输入向后多读 halo 行（只读重叠，
                # 段的规范范围不变；重叠区产物由下方仲裁器去重）
                if self.halo_lines > 0 and seg.end_idx < n:
                    ext_lines = lines[seg.start_idx : min(n, seg.end_idx + self.halo_lines)]
                    return await self._extract_segment(seg, ext_lines=ext_lines)
                return await self._extract_segment(seg)

        # gather 保持顺序：per_seg[i] 对应 segments[i]（段序）
        per_seg: list[list[Any]] = await asyncio.gather(*(run_segment(s) for s in segments))

        # v11 III.4：相邻段 halo 重叠区确定性仲裁（0 LLM）
        if self.halo_lines > 0 and len(segments) > 1:
            from sciforge.pipeline.boundary_patch import arbitrate_halo_overlap

            total_dropped = 0
            for i in range(len(segments) - 1):
                boundary_line = self._line_no_of(lines[segments[i + 1].start_idx])
                kept_prev, kept_next, dropped = arbitrate_halo_overlap(
                    per_seg[i], per_seg[i + 1], boundary_line=boundary_line
                )
                per_seg[i], per_seg[i + 1] = kept_prev, kept_next
                total_dropped += dropped
            if total_dropped:
                logger.info(
                    "halo_arbitration_done",
                    segments=len(segments),
                    halo_lines=self.halo_lines,
                    deduped=total_dropped,
                )
        return list(zip(segments, per_seg, strict=True))

    async def _extract_segment(self, seg: Any, *, ext_lines: list[str] | None = None) -> list[Any]:
        """段内提取。``ext_lines`` 为含 halo 的扩展输入（III.4，None = 段原始行）。

        - 若提供 ``segment_extract_fn``（生产 carry-aware 路径）→ 整段交给它，
          段内由其自行滑窗 + carry，**不在 chunk 边界切断条目**。
        - 否则走 ``extract_fn`` chunk 路径：段 ≤ window 一次调用；更大段按
          ``window_lines`` 顺序分块（段内串行、无 carry）。
        """
        if self.segment_extract_fn is not None:
            return list(await self.segment_extract_fn(ext_lines or seg.lines))

        assert self.extract_fn is not None  # __init__ 已保证二者不全空
        items: list[Any] = []
        seg_lines = ext_lines or seg.lines
        for chunk_start in range(0, len(seg_lines), self.window_lines):
            chunk = seg_lines[chunk_start : chunk_start + self.window_lines]
            if not chunk:
                continue
            start_line = self._line_no_of(chunk[0])
            last_line = self._line_no_of(chunk[-1])
            chunk_items = await self.extract_fn(chunk, start_line, last_line)
            items.extend(chunk_items)
        return items
