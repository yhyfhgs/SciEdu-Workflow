"""III.1 marker ledger + audit_coverage（WP-12）——查漏对账三类问题。"""

from __future__ import annotations

from sciforge.pipeline.boundary_patch import (
    AuditItem,
    audit_coverage,
    build_marker_ledger,
)

_WINDOW = [
    "定理 3.1 连续函数有界。",  # line 101
    "证明：显然。",  # 102
    "",  # 103
    "定理 3.2 一致连续性。",  # 104
    "证明：略。",  # 105
    "",  # 106
    "习题 3.1 求极限。",  # 107（LLM 漏提的条目）
    "",  # 108
    "例 3.1 计算积分。",  # 109
    "解：直接计算。",  # 110
]


def _item(ref: str, start: int, end: int, **overrides: object) -> AuditItem:
    base: dict[str, object] = {
        "item_ref": ref,
        "category": "knowledge",
        "item_type": "theorem",
        "solution_status": "complete",
        "start_line": start,
        "end_line": end,
    }
    base.update(overrides)
    return AuditItem.model_validate(base)


def test_ledger_spans_window() -> None:
    ledger = build_marker_ledger(_WINDOW, line_base=101)
    assert ledger.window_line_span == (101, 110)
    strong = ledger.strong_item_markers()
    assert [m.global_line for m in strong] == [101, 104, 107, 109]


def test_clean_audit_all_covered() -> None:
    ledger = build_marker_ledger(_WINDOW, line_base=101)
    items = [
        _item("i1", 101, 103),
        _item("i2", 104, 106),
        _item("i3", 107, 108, category="question", item_type="exercise"),
        _item("i4", 109, 110, category="question", item_type="example"),
    ]
    report = audit_coverage(ledger, items)
    assert report.clean, (report.missed_markers, report.boundary_suspects)
    assert report.markers_covered == report.markers_total
    covered_by = {m.global_line: m.covered_by for m in ledger.markers}
    assert covered_by[107] == "i3"


def test_missed_marker_detected_with_gap_interval() -> None:
    """10 个 marker 提了 8 条 → 审计立刻指出缺哪几行（Docs/20 III.1）。"""
    ledger = build_marker_ledger(_WINDOW, line_base=101)
    items = [
        _item("i1", 101, 103),
        _item("i2", 104, 106),
        # 习题 3.1（107）漏提
        _item("i4", 109, 110, category="question", item_type="example"),
    ]
    report = audit_coverage(ledger, items)
    assert not report.clean
    assert [m.global_line for m in report.missed_markers] == [107]
    gaps = [c for c in report.coverage_intervals if c.kind == "gap"]
    assert gaps == [gaps[0]]
    assert (gaps[0].start_global_line, gaps[0].end_global_line) == (107, 108)


def test_boundary_suspect_when_item_swallows_next() -> None:
    ledger = build_marker_ledger(_WINDOW, line_base=101)
    items = [
        _item("big", 101, 106),  # 吞并了 104 行的定理 3.2
        _item("i3", 107, 108),
        _item("i4", 109, 110),
    ]
    report = audit_coverage(ledger, items)
    assert len(report.boundary_suspects) == 1
    suspect = report.boundary_suspects[0]
    assert suspect.item_ref == "big"
    assert suspect.marker.global_line == 104


def test_pairing_suspect_no_solution_but_proof_inside() -> None:
    ledger = build_marker_ledger(_WINDOW, line_base=101)
    items = [
        _item("i1", 101, 103, solution_status="no_solution_in_source"),  # 102 行有"证明："
        _item("i2", 104, 106),
        _item("i3", 107, 108),
        _item("i4", 109, 110),
    ]
    report = audit_coverage(ledger, items)
    assert len(report.pairing_suspects) == 1
    assert report.pairing_suspects[0].item_ref == "i1"


def test_weak_marker_not_missed_but_ambiguous() -> None:
    window = ["1. 第一步推导。", "2. 第二步推导。"]  # 解答步骤样式的裸编号
    ledger = build_marker_ledger(window, line_base=1)
    report = audit_coverage(ledger, [])
    assert report.missed_markers == []  # weak 不进 missed
    assert all(m.status == "ambiguous" for m in ledger.markers)


def test_empty_window_ledger() -> None:
    ledger = build_marker_ledger([], line_base=50)
    report = audit_coverage(ledger, [])
    assert report.clean
    assert report.markers_total == 0
