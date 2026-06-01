"""并行段提取协调器（Phase 6 #1）回归测试。

验证：段间并发、(段序+段内序) 保序、不漏条目、行号传递正确。
"""

from __future__ import annotations

import asyncio

from sciforge.pipeline.parallel_extract import ParallelSegmentExtractor


def _lines(n: int) -> list[str]:
    return [f"{i + 1:>7d} | content line {i}" for i in range(n)]


def test_order_preserved_across_parallel_segments() -> None:
    """每段提取 1 条带其全局起始行号的 item；结果必须按文档顺序。"""

    async def extract_fn(window, start_line, last_line):
        # 模拟：每个 window 提取一条，标注其起始行号
        return [{"start_line": start_line}]

    ex = ParallelSegmentExtractor(extract_fn, window_lines=100, concurrency=4)
    lines = _lines(500)
    out = asyncio.run(ex.extract(lines, [100, 200, 300, 400], min_segment_lines=10))
    # 5 段 → 5 条，order_index 连续，且 start_line 升序（文档顺序保持）
    assert [oi for oi, _ in out] == [0, 1, 2, 3, 4]
    starts = [it["start_line"] for _, it in out]
    assert starts == sorted(starts)
    assert starts == [1, 101, 201, 301, 401]  # 各段首行全局行号


def test_no_items_lost() -> None:
    """每段提取多条，总数 = 各段之和（不漏不重）。"""

    async def extract_fn(window, start_line, last_line):
        # 提取条数 = 窗口行数 // 30
        return [{"s": start_line, "j": j} for j in range(len(window) // 30)]

    ex = ParallelSegmentExtractor(extract_fn, window_lines=120, concurrency=3)
    lines = _lines(600)
    out = asyncio.run(ex.extract(lines, [120, 240, 360, 480], min_segment_lines=10))
    # 5 段各 120 行 → 每段 4 条 → 20 条
    assert len(out) == 20
    assert [oi for oi, _ in out] == list(range(20))


def test_start_order_index_offset() -> None:
    async def extract_fn(window, start_line, last_line):
        return [{"x": 1}]

    ex = ParallelSegmentExtractor(extract_fn, window_lines=100)
    out = asyncio.run(ex.extract(_lines(200), [100], start_order_index=1000))
    assert [oi for oi, _ in out] == [1000, 1001]


def test_large_segment_chunked_within_segment() -> None:
    """单段大于 window 时段内按 window 顺序分块（段内串行，顺序保持）。"""
    calls: list[tuple[int, int]] = []

    async def extract_fn(window, start_line, last_line):
        calls.append((start_line, last_line))
        return [{"start": start_line}]

    # 无边界 → 1 段 250 行，window=100 → 段内切 100/100/50 三块
    ex = ParallelSegmentExtractor(extract_fn, window_lines=100)
    out = asyncio.run(ex.extract(_lines(250), [], min_segment_lines=10))
    assert len(out) == 3
    assert [it["start"] for _, it in out] == [1, 101, 201]  # 段内顺序


def test_concurrency_actually_parallel() -> None:
    """并发度>1 时多段确实并行（用并发计数验证）。"""
    active = 0
    max_active = 0

    async def extract_fn(window, start_line, last_line):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return [{"s": start_line}]

    ex = ParallelSegmentExtractor(extract_fn, window_lines=50, concurrency=4)
    # 10 段
    bounds = list(range(50, 500, 50))
    asyncio.run(ex.extract(_lines(500), bounds, min_segment_lines=10))
    assert max_active >= 2  # 确有并发


def test_empty_document() -> None:
    async def extract_fn(window, start_line, last_line):
        return [{"x": 1}]

    ex = ParallelSegmentExtractor(extract_fn)
    assert asyncio.run(ex.extract([], [])) == []
