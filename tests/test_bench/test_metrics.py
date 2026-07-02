"""bench 指标计算（bench/metrics.py）——每个指标的边界情形。"""

from __future__ import annotations

from sciforge.bench.extracted import ExtractedRecord
from sciforge.bench.gt_models import GoldenDoc, GtItem, GtMarker
from sciforge.bench.metrics import (
    aggregate_report,
    compare_reports,
    compute_doc_metrics,
    match_items,
)

# 20 行合成文档：每行 10 个字符（内容不参与指标，只提供行长度）
_DOC = [f"line-{i:04d}" for i in range(1, 21)]


def _gt_item(gt_id: str, start: int, end: int, **overrides: object) -> GtItem:
    base: dict[str, object] = {
        "gt_id": gt_id,
        "category": "question",
        "kind": "exercise",
        "start_line": start,
        "end_line": end,
    }
    base.update(overrides)
    return GtItem.model_validate(base)


def _record(item_id: int, start: int, end: int, **overrides: object) -> ExtractedRecord:
    base: dict[str, object] = {
        "item_id": item_id,
        "category": "question",
        "item_type": "exercise",
        "solution_status": "complete",
        "start_line": start,
        "end_line": end,
    }
    base.update(overrides)
    return ExtractedRecord.model_validate(base)


def _doc(items: list[GtItem], markers: list[GtMarker] | None = None) -> GoldenDoc:
    return GoldenDoc(
        doc_id="synthetic",
        source_fixture="synthetic.md",
        markers=markers or [],
        items=items,
    )


# ── 匹配 ─────────────────────────────────────────────────


def test_match_exact_overlap() -> None:
    pairs = match_items([_gt_item("a", 1, 5)], [_record(1, 1, 5)], _DOC)
    assert len(pairs) == 1
    assert pairs[0][2] == 1.0


def test_match_below_threshold_rejected() -> None:
    # 区间 [1,2] vs [10,20]：无重叠 → 不匹配
    pairs = match_items([_gt_item("a", 1, 2)], [_record(1, 10, 20)], _DOC)
    assert pairs == []


def test_match_greedy_one_to_one() -> None:
    # 两条 gt、一条提取横跨两者 → 只能匹配 IoU 更高的一条
    gt = [_gt_item("a", 1, 5), _gt_item("b", 6, 10)]
    pairs = match_items(gt, [_record(1, 1, 6)], _DOC)
    assert len(pairs) == 1
    assert pairs[0][0].gt_id == "a"


# ── marker_recall ────────────────────────────────────────


def test_marker_recall_counts_covered() -> None:
    markers = [GtMarker(line=1, kind="exercise"), GtMarker(line=15, kind="exercise")]
    doc = _doc([_gt_item("a", 1, 5)], markers)
    result = compute_doc_metrics(doc, [_record(1, 1, 5)], _DOC)
    assert result.marker_recall == 0.5  # line 15 未被任何提取区间覆盖


def test_marker_recall_none_when_unannotated() -> None:
    result = compute_doc_metrics(_doc([_gt_item("a", 1, 5)]), [_record(1, 1, 5)], _DOC)
    assert result.marker_recall is None


# ── item_recall / char_iou / pairing ─────────────────────


def test_item_recall_and_unmatched_extracted() -> None:
    gt = [_gt_item("a", 1, 5), _gt_item("b", 11, 15)]
    extracted = [_record(1, 1, 5), _record(2, 17, 20)]  # 第二条与任何 gt 无重叠
    result = compute_doc_metrics(_doc(gt), extracted, _DOC)
    assert result.item_recall == 0.5
    assert result.matched_items == 1
    assert result.unmatched_extracted == 1


def test_char_iou_line_level_partial() -> None:
    # gt [1,10] vs 提取 [1,5]：行级投影，IoU 明显 < 1 且 > 0
    result = compute_doc_metrics(_doc([_gt_item("a", 1, 10)]), [_record(1, 1, 5)], _DOC)
    assert result.char_iou is not None
    assert 0.3 < result.char_iou < 0.6


def test_char_iou_upgrades_with_char_offsets() -> None:
    # gt 精确到行内偏移；提取带 char span 时 IoU 精确到字符
    gt = [_gt_item("a", 1, 1, start_char=2, end_char=8)]
    exact = compute_doc_metrics(_doc(gt), [_record(1, 1, 1, start_char=2, end_char=8)], _DOC)
    assert exact.char_iou == 1.0
    line_level = compute_doc_metrics(_doc(gt), [_record(1, 1, 1)], _DOC)
    assert line_level.char_iou is not None
    assert line_level.char_iou < 1.0  # 整行投影比 gt 区间宽 → IoU < 1


def test_pairing_accuracy_compares_status() -> None:
    gt = [
        _gt_item("a", 1, 5, solution_status="no_solution_in_source"),
        _gt_item("b", 11, 15, solution_status="complete"),
    ]
    extracted = [
        _record(1, 1, 5, solution_status="complete"),  # 误判
        _record(2, 11, 15, solution_status="complete"),
    ]
    result = compute_doc_metrics(_doc(gt), extracted, _DOC)
    assert result.pairing_accuracy == 0.5


# ── cross_window_merge_rate ──────────────────────────────


def test_merge_rate_none_without_crossing_items() -> None:
    result = compute_doc_metrics(_doc([_gt_item("a", 1, 5)]), [_record(1, 1, 5)], _DOC)
    assert result.cross_window_merge_rate is None


def test_merge_rate_requires_high_coverage() -> None:
    gt = [
        _gt_item("a", 1, 10, crosses_window=True),
        _gt_item("b", 11, 20, crosses_window=True),
    ]
    extracted = [
        _record(1, 1, 10),  # 完整覆盖 → 合并成功
        _record(2, 11, 15),  # 只覆盖一半 → 视为截断
    ]
    result = compute_doc_metrics(_doc(gt), extracted, _DOC)
    assert result.cross_window_merge_rate == 0.5


# ── 聚合与对比 ────────────────────────────────────────────


def test_aggregate_skips_undefined_metrics() -> None:
    r1 = compute_doc_metrics(_doc([_gt_item("a", 1, 5)]), [_record(1, 1, 5)], _DOC)
    report = aggregate_report([r1])
    assert report.aggregate["item_recall"] == 1.0
    assert "marker_recall" not in report.aggregate  # 无标注 → 不聚合
    assert "cross_window_merge_rate" not in report.aggregate


def test_compare_reports_flags_regression() -> None:
    gt = _doc([_gt_item("a", 1, 5), _gt_item("b", 11, 15)])
    good = aggregate_report([compute_doc_metrics(gt, [_record(1, 1, 5), _record(2, 11, 15)], _DOC)])
    bad = aggregate_report([compute_doc_metrics(gt, [_record(1, 1, 5)], _DOC)])
    regressions = compare_reports(good, bad)
    assert any("item_recall" in r for r in regressions)
    assert compare_reports(good, good) == []


def test_compare_reports_tolerates_small_drift() -> None:
    gt = _doc([_gt_item(f"g{i}", 1 + i, 1 + i) for i in range(0, 10, 2)])
    full = aggregate_report(
        [compute_doc_metrics(gt, [_record(i, 1 + i, 1 + i) for i in range(0, 10, 2)], _DOC)]
    )
    # 容忍度放宽到 0.5 时，掉一条不算退化
    slightly_worse = aggregate_report(
        [compute_doc_metrics(gt, [_record(i, 1 + i, 1 + i) for i in range(0, 8, 2)], _DOC)]
    )
    assert compare_reports(full, slightly_worse, tolerance=0.5) == []
