"""WP-23 segment halo + boundary arbiter（III.4）——仲裁四情形与并行/串行一致性。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from sciforge.pipeline.boundary_patch import arbitrate_halo_overlap
from sciforge.pipeline.parallel_extract import ParallelSegmentExtractor

# ── 仲裁器单元 ────────────────────────────────────────────


def _fake_item(
    start: int, end: int, number: str = "", solution: str | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        start_line=start,
        end_line=end,
        number=number,
        corrected_proof=None,
        corrected_solution=solution,
        corrected_content=f"item@{start}-{end}",
    )


def test_arbiter_keeps_items_before_boundary() -> None:
    prev = [_fake_item(1, 50), _fake_item(51, 99)]
    kept_prev, kept_next, dropped = arbitrate_halo_overlap(prev, [], boundary_line=100)
    assert kept_prev == prev
    assert dropped == 0


def test_arbiter_drops_halo_interior_items_from_prev() -> None:
    """完全落在 halo 内的条目属邻段领土 → 前段丢弃。"""
    prev = [_fake_item(1, 99), _fake_item(105, 130)]
    next_items = [_fake_item(105, 130)]
    kept_prev, kept_next, dropped = arbitrate_halo_overlap(prev, next_items, boundary_line=100)
    assert [i.start_line for i in kept_prev] == [1]
    assert kept_next == next_items
    assert dropped == 1


def test_arbiter_cross_boundary_prev_more_complete() -> None:
    """前段完整看到跨界条目（含解答），后段只有头部残片 → 保前段。"""
    prev = [_fake_item(90, 140, number="7", solution="完整解答")]
    next_items = [_fake_item(100, 140, number="7")]  # 无解答的残片
    kept_prev, kept_next, dropped = arbitrate_halo_overlap(prev, next_items, boundary_line=100)
    assert kept_prev == prev
    assert kept_next == []
    assert dropped == 1


def test_arbiter_cross_boundary_next_more_complete() -> None:
    """前段跨界只提到头（halo 不够长），后段版本更完整 → 丢前段残片。"""
    prev = [_fake_item(90, 110, number="7")]  # 头部残片
    next_items = [_fake_item(90, 160, number="7", solution="完整解答")]
    kept_prev, kept_next, dropped = arbitrate_halo_overlap(prev, next_items, boundary_line=100)
    assert kept_prev == []
    assert kept_next == next_items
    assert dropped == 1


def test_arbiter_cross_boundary_next_missed_entirely() -> None:
    """后段漏提跨界条目 → 前段跨界版本保住（halo 的核心价值）。"""
    prev = [_fake_item(90, 140, number="7", solution="解")]
    kept_prev, kept_next, dropped = arbitrate_halo_overlap(prev, [], boundary_line=100)
    assert kept_prev == prev
    assert dropped == 0


def test_arbiter_does_not_merge_disjoint_items_by_number_alone() -> None:
    """章节局部编号会重复；非重叠且正文不同的同编号条目都必须保留。"""
    prev = [_fake_item(95, 108, number="3.2", solution="解")]
    next_items = [_fake_item(110, 118, number="3.2")]
    kept_prev, kept_next, dropped = arbitrate_halo_overlap(prev, next_items, boundary_line=100)
    assert kept_prev == prev
    assert kept_next == next_items
    assert dropped == 0


def test_arbiter_can_merge_disjoint_items_with_same_number_and_content() -> None:
    prev_item = _fake_item(95, 108, number="3.2", solution="完整解答")
    next_item = _fake_item(110, 118, number="3.2")
    next_item.corrected_content = prev_item.corrected_content

    kept_prev, kept_next, dropped = arbitrate_halo_overlap(
        [prev_item], [next_item], boundary_line=100
    )

    assert kept_prev == [prev_item]
    assert kept_next == []
    assert dropped == 1


# ── 并行提取集成（fake extract_fn，0 LLM）─────────────────


def _annotated(lines: list[str], base: int = 1) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


def _line_no(line: str) -> int:
    prefix = line.split("|", 1)[0].strip()
    return int(prefix) if prefix.isdigit() else 0


def _scan_items(seg_lines: list[str]) -> list[Any]:
    """确定性 fake 提取器：'习题 N' 行开题、'。' 结尾行闭合（含解答行）。"""
    items: list[Any] = []
    current: dict[str, Any] | None = None
    for ln in seg_lines:
        no = _line_no(ln)
        text = ln.split("|", 1)[-1].strip()
        if text.startswith("习题"):
            number = text.split()[1] if len(text.split()) > 1 else ""
            current = {"start": no, "number": number, "has_solution": False}
        if current is not None:
            if text.startswith("解："):
                current["has_solution"] = True
            if text.endswith("完。"):
                items.append(
                    _fake_item(
                        current["start"],
                        no,
                        number=current["number"],
                        solution="解" if current["has_solution"] else None,
                    )
                )
                current = None
    return items  # 未闭合条目不 emit（模拟 incomplete_at_tail）


# 文档：习题 1（1-3 行）在段 A；习题 2（4-8 行）跨 A/B 边界（边界在第 6 行）
_DOC = _annotated(
    [
        "习题 1 求极限。",  # 1
        "解：直接计算。",  # 2
        "答案是 0。完。",  # 3
        "习题 2 设 $G$ 是群。",  # 4（跨边界条目开始）
        "解：考虑中心。",  # 5
        "于是得证。完。",  # 6  ← 段边界在此行（boundary_indices=[6] → 第 7 行起新段）
        "习题 3 证明不等式。",  # 7
        "解：由均值不等式。完。",  # 8
    ]
)
# boundary at 0-based index 5（第 6 行）——习题 2 的最后一行被切进前段之外
_BOUNDARY = [5]


async def _run(halo: int) -> list[Any]:
    async def seg_fn(seg_lines: list[str]) -> list[Any]:
        return _scan_items(seg_lines)

    extractor = ParallelSegmentExtractor(
        segment_extract_fn=seg_fn,
        window_lines=10_000,
        concurrency=2,
        halo_lines=halo,
    )
    grouped = await extractor.extract_grouped(_DOC, _BOUNDARY, min_segment_lines=1)
    return [item for _, items in grouped for item in items]


async def test_without_halo_cross_boundary_item_lost() -> None:
    """现状（halo=0）：跨段条目被切断——前段没等到'完。'，后段没看到题头。"""
    items = await _run(halo=0)
    numbers = [i.number for i in items]
    assert "2" not in numbers  # 习题 2 漏失（这正是 III.4 要修的缺陷）


async def test_with_halo_parallel_matches_serial() -> None:
    """开 halo：并行提取的条目集合与整篇串行扫描一致（不漏、不重）。"""
    serial = _scan_items(_DOC)
    parallel = await _run(halo=3)
    assert [(i.number, i.start_line, i.end_line) for i in parallel] == [
        (i.number, i.start_line, i.end_line) for i in serial
    ]
    numbers = [i.number for i in parallel]
    assert numbers == ["1", "2", "3"]
    assert len(set(numbers)) == len(numbers)  # 无双写


async def test_halo_zero_behavior_unchanged_for_clean_boundaries() -> None:
    """条目不跨界时 halo 开关结果一致（回归保护）。"""
    doc = _annotated(
        [
            "习题 1 求极限。完。",  # 1
            "习题 2 求导数。完。",  # 2（边界后）
        ]
    )

    async def seg_fn(seg_lines: list[str]) -> list[Any]:
        return _scan_items(seg_lines)

    for halo in (0, 5):
        extractor = ParallelSegmentExtractor(
            segment_extract_fn=seg_fn, window_lines=10_000, concurrency=2, halo_lines=halo
        )
        grouped = await extractor.extract_grouped(doc, [1], min_segment_lines=1)
        items = [item for _, items in grouped for item in items]
        assert [i.number for i in items] == ["1", "2"], f"halo={halo}"
