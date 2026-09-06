"""Golden benchmark 指标计算（v11 §I.2）——纯离线、0 LLM、确定性。

所有区间先映射到 pinned markdown 的**绝对字符空间**再计算 IoU：
- v10 提取只有行级边界 → 整行投影（行级 IoU）
- M1 char span 落地后 ExtractedRecord 带 start_char/end_char → 自动升级为字符级

指标语义（目标值见 Docs/20 §I.2）：
- marker_recall     被某提取条目行区间覆盖的 gt marker 占比（漏条目的直接度量）
- item_recall       与某提取条目成功匹配（IoU ≥ 阈值）的 gt 条目占比
- char_iou          匹配对的平均字符级 IoU（边界精度）
- pairing_accuracy  匹配对中 solution_status 与 gt 一致的占比（题答配对）
- cross_window_merge_rate  跨窗 gt 条目被单条提取记录基本覆盖（≥90%）的占比
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sciforge.bench.extracted import ExtractedRecord
from sciforge.bench.gt_models import GoldenDoc, GtItem

# 匹配阈值：gt 条目与提取条目 IoU 低于此值不视为同一条目
MATCH_MIN_IOU = 0.3
# 跨窗合并判定：单条提取记录覆盖 gt 条目绝对区间的最小比例
MERGE_MIN_COVERAGE = 0.9


class DocBenchResult(BaseModel):
    """单文档跑分结果。None = 该指标在此文档上无定义（如无跨窗条目）。"""

    model_config = ConfigDict(extra="forbid")

    doc_id: str
    marker_recall: float | None = None
    item_recall: float | None = None
    char_iou: float | None = None
    pairing_accuracy: float | None = None
    cross_window_merge_rate: float | None = None
    gt_markers: int = 0
    gt_items: int = 0
    extracted_items: int = 0
    matched_items: int = 0
    unmatched_extracted: int = 0


_METRIC_FIELDS = (
    "marker_recall",
    "item_recall",
    "char_iou",
    "pairing_accuracy",
    "cross_window_merge_rate",
)


class BenchReport(BaseModel):
    """一次 bench 运行的完整报告（多文档 + 聚合均值）。"""

    model_config = ConfigDict(extra="forbid")

    docs: list[DocBenchResult] = Field(default_factory=list)
    aggregate: dict[str, float] = Field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False, indent=2)


def load_report(path: Path) -> BenchReport:
    return BenchReport.model_validate(json.loads(path.read_text(encoding="utf-8")))


# ── 绝对字符空间映射 ──────────────────────────────────────


def _line_starts(doc_lines: list[str]) -> list[int]:
    """1-based 行号 → 该行首字符的绝对偏移（额外附一个 EOF 哨兵）。"""
    starts = [0]
    total = 0
    for line in doc_lines:
        total += len(line) + 1  # +1 = 换行符
        starts.append(total)
    return starts


def _abs_span(
    start_line: int,
    end_line: int,
    start_char: int | None,
    end_char: int | None,
    doc_lines: list[str],
    starts: list[int],
) -> tuple[int, int]:
    """(行, 行内偏移) → 绝对字符区间 [a, b)。行号越界时夹到文档范围内。"""
    n = len(doc_lines)
    s_line = min(max(start_line, 1), n)
    e_line = min(max(end_line, 1), n)
    a = starts[s_line - 1] + min(start_char or 0, len(doc_lines[s_line - 1]))
    end_len = len(doc_lines[e_line - 1])
    b = starts[e_line - 1] + (end_len if end_char is None else min(end_char, end_len))
    return (a, b) if a <= b else (a, a)


def _iou(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def _coverage(inner: tuple[int, int], outer: tuple[int, int]) -> float:
    """outer 覆盖 inner 的比例（inner 为 gt 条目区间）。"""
    span = inner[1] - inner[0]
    if span <= 0:
        return 0.0
    inter = max(0, min(inner[1], outer[1]) - max(inner[0], outer[0]))
    return inter / span


# ── 匹配与指标 ────────────────────────────────────────────


def match_items(
    gt_items: list[GtItem],
    extracted: list[ExtractedRecord],
    doc_lines: list[str],
) -> list[tuple[GtItem, ExtractedRecord, float]]:
    """贪心最大 IoU 一对一匹配（IoU ≥ MATCH_MIN_IOU 才成对）。"""
    starts = _line_starts(doc_lines)
    gt_spans = {
        g.gt_id: _abs_span(g.start_line, g.end_line, g.start_char, g.end_char, doc_lines, starts)
        for g in gt_items
    }
    ex_spans = {
        e.item_id: _abs_span(e.start_line, e.end_line, e.start_char, e.end_char, doc_lines, starts)
        for e in extracted
    }
    candidates = [
        (_iou(gt_spans[g.gt_id], ex_spans[e.item_id]), gi, ei)
        for gi, g in enumerate(gt_items)
        for ei, e in enumerate(extracted)
    ]
    candidates.sort(key=lambda t: t[0], reverse=True)
    used_gt: set[int] = set()
    used_ex: set[int] = set()
    pairs: list[tuple[GtItem, ExtractedRecord, float]] = []
    for iou, gi, ei in candidates:
        if iou < MATCH_MIN_IOU:
            break
        if gi in used_gt or ei in used_ex:
            continue
        used_gt.add(gi)
        used_ex.add(ei)
        pairs.append((gt_items[gi], extracted[ei], iou))
    return pairs


def compute_doc_metrics(
    gt: GoldenDoc,
    extracted: list[ExtractedRecord],
    doc_lines: list[str],
) -> DocBenchResult:
    """对一份 golden 文档计算全部指标。"""
    starts = _line_starts(doc_lines)
    pairs = match_items(gt.items, extracted, doc_lines)

    marker_recall: float | None = None
    if gt.markers:
        covered = sum(
            1 for m in gt.markers if any(e.start_line <= m.line <= e.end_line for e in extracted)
        )
        marker_recall = covered / len(gt.markers)

    item_recall = len(pairs) / len(gt.items) if gt.items else None
    char_iou = sum(iou for _, _, iou in pairs) / len(pairs) if pairs else None
    pairing_accuracy = (
        sum(1 for g, e, _ in pairs if g.solution_status == e.solution_status) / len(pairs)
        if pairs
        else None
    )

    crossing = [g for g in gt.items if g.crosses_window]
    merge_rate: float | None = None
    if crossing:
        by_gt_id = {g.gt_id: e for g, e, _ in pairs}
        merged = 0
        for g in crossing:
            e = by_gt_id.get(g.gt_id)
            if e is None:
                continue
            g_span = _abs_span(
                g.start_line, g.end_line, g.start_char, g.end_char, doc_lines, starts
            )
            e_span = _abs_span(
                e.start_line, e.end_line, e.start_char, e.end_char, doc_lines, starts
            )
            if _coverage(g_span, e_span) >= MERGE_MIN_COVERAGE:
                merged += 1
        merge_rate = merged / len(crossing)

    return DocBenchResult(
        doc_id=gt.doc_id,
        marker_recall=marker_recall,
        item_recall=item_recall,
        char_iou=char_iou,
        pairing_accuracy=pairing_accuracy,
        cross_window_merge_rate=merge_rate,
        gt_markers=len(gt.markers),
        gt_items=len(gt.items),
        extracted_items=len(extracted),
        matched_items=len(pairs),
        unmatched_extracted=len(extracted) - len(pairs),
    )


def aggregate_report(results: list[DocBenchResult]) -> BenchReport:
    """跨文档聚合：各指标对"有定义的文档"取算术均值。"""
    aggregate: dict[str, float] = {}
    for field in _METRIC_FIELDS:
        values = [v for r in results if (v := getattr(r, field)) is not None]
        if values:
            aggregate[field] = sum(values) / len(values)
    return BenchReport(docs=results, aggregate=aggregate)


def compare_reports(
    baseline: BenchReport, current: BenchReport, *, tolerance: float = 0.05
) -> list[str]:
    """对比两份报告，返回退化项描述（空 = 无退化）。

    退化定义（Docs/20 §I.2 CI gate）：同一文档同一指标，current < baseline - tolerance。
    仅比较双方都有定义的指标；聚合值同样参与比较。
    """
    regressions: list[str] = []
    base_docs = {d.doc_id: d for d in baseline.docs}
    for cur in current.docs:
        base = base_docs.get(cur.doc_id)
        if base is None:
            continue
        for field in _METRIC_FIELDS:
            b, c = getattr(base, field), getattr(cur, field)
            if b is not None and c is not None and c < b - tolerance:
                regressions.append(f"{cur.doc_id}.{field}: {b:.3f} -> {c:.3f}")
    for field, b_val in baseline.aggregate.items():
        c_val = current.aggregate.get(field)
        if c_val is not None and c_val < b_val - tolerance:
            regressions.append(f"aggregate.{field}: {b_val:.3f} -> {c_val:.3f}")
    return regressions
