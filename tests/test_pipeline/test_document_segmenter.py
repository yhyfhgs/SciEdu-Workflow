"""文档分段器 + 保序重排（Phase 6 #1）回归测试。

重点验证"不漏"不变量：覆盖完整、无重叠、重排不丢条目。
"""

from __future__ import annotations

import pytest

from sciforge.pipeline.document_segmenter import (
    DocSegment,
    assign_order_index,
    segment_document,
)


def _lines(n: int) -> list[str]:
    return [f"{i + 1:>7d} | line {i}" for i in range(n)]


def _assert_full_coverage(segments: list[DocSegment], n: int) -> None:
    assert segments[0].start_idx == 0
    assert segments[-1].end_idx == n
    assert sum(s.line_count for s in segments) == n
    for i in range(len(segments) - 1):
        assert segments[i].end_idx == segments[i + 1].start_idx  # 连续无缝
    # ordinal 连续
    assert [s.ordinal for s in segments] == list(range(len(segments)))


def test_basic_split_at_boundaries() -> None:
    lines = _lines(300)
    segs = segment_document(lines, [100, 200], min_lines=10, max_lines=1000)
    assert len(segs) == 3
    assert (segs[0].start_idx, segs[0].end_idx) == (0, 100)
    assert (segs[1].start_idx, segs[1].end_idx) == (100, 200)
    assert (segs[2].start_idx, segs[2].end_idx) == (200, 300)
    _assert_full_coverage(segs, 300)


def test_coverage_invariant_arbitrary_boundaries() -> None:
    lines = _lines(517)
    segs = segment_document(lines, [50, 51, 200, 480], min_lines=20, max_lines=1000)
    _assert_full_coverage(segs, 517)  # 不漏一行


def test_no_boundaries_single_segment() -> None:
    lines = _lines(150)
    segs = segment_document(lines, [], min_lines=10, max_lines=1000)
    assert len(segs) == 1
    _assert_full_coverage(segs, 150)


def test_empty_document() -> None:
    assert segment_document([], [10, 20]) == []


def test_small_segments_merged() -> None:
    lines = _lines(300)
    # 边界 10,20,30 会产生过小段 → 合并；min_lines=50
    segs = segment_document(lines, [10, 20, 30, 150], min_lines=50, max_lines=1000)
    _assert_full_coverage(segs, 300)
    assert all(s.line_count >= 50 or s is segs[-1] for s in segs)  # 除可能末段外都≥阈值


def test_large_segment_split() -> None:
    lines = _lines(1000)
    segs = segment_document(lines, [], min_lines=10, max_lines=300)
    assert len(segs) == 4  # 1000 / 300 → 300+300+300+100
    assert all(s.line_count <= 300 for s in segs)
    _assert_full_coverage(segs, 1000)


def test_out_of_range_boundaries_ignored() -> None:
    lines = _lines(100)
    segs = segment_document(lines, [-5, 0, 50, 100, 999], min_lines=10, max_lines=1000)
    # 0/100/999 越界或隐含 → 只在 50 切
    assert len(segs) == 2
    _assert_full_coverage(segs, 100)


# ── 保序重排 ──────────────────────────────────────────


def test_assign_order_index_flattens_in_order() -> None:
    per_seg = [["a", "b"], ["c"], ["d", "e", "f"]]
    out = assign_order_index(per_seg)
    assert [oi for oi, _ in out] == [0, 1, 2, 3, 4, 5]
    assert [it for _, it in out] == ["a", "b", "c", "d", "e", "f"]  # 段序+段内序


def test_assign_order_index_start_offset() -> None:
    out = assign_order_index([["x"], ["y"]], start=100)
    assert [oi for oi, _ in out] == [100, 101]


def test_assign_order_index_no_miss() -> None:
    per_seg = [list(range(5)), [], list(range(3)), list(range(7))]
    out = assign_order_index(per_seg)
    assert len(out) == 15  # 5+0+3+7，不漏不重


def test_assign_order_index_empty() -> None:
    assert assign_order_index([]) == []
    assert assign_order_index([[], []]) == []


def test_segment_then_reassign_roundtrip_preserves_all() -> None:
    """端到端：分段 → 每段模拟提取若干条 → 重排，条目总数守恒。"""
    lines = _lines(450)
    segs = segment_document(lines, [120, 300], min_lines=20, max_lines=1000)
    # 每段模拟提取：段内 item 数 = 行数 // 40
    per_seg = [[f"seg{s.ordinal}_item{j}" for j in range(s.line_count // 40)] for s in segs]
    total = sum(len(p) for p in per_seg)
    out = assign_order_index(per_seg)
    assert len(out) == total
    # 段序保持：seg0 的 item 全在 seg1 之前
    items = [it for _, it in out]
    assert items == [it for p in per_seg for it in p]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
