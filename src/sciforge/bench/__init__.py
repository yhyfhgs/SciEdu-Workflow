"""Golden benchmark（v11 §I）——gt 数据模型、提取结果读取、指标计算。

全部纯离线 0 LLM：gt 锚定 pinned Task0 markdown（tests/golden/docs/），
提取侧消费 checkpoint 镜像 items/stage1/item_*.json，指标为确定性计算。
CLI 入口为 ``sciforge parse bench``。
"""

from sciforge.bench.extracted import ExtractedRecord, load_extracted_records
from sciforge.bench.gt_models import GoldenDoc, GtItem, GtMarker, load_golden_dir, load_golden_doc
from sciforge.bench.metrics import (
    BenchReport,
    DocBenchResult,
    aggregate_report,
    compare_reports,
    compute_doc_metrics,
    load_report,
    match_items,
)

__all__ = [
    "BenchReport",
    "DocBenchResult",
    "ExtractedRecord",
    "GoldenDoc",
    "GtItem",
    "GtMarker",
    "aggregate_report",
    "compare_reports",
    "compute_doc_metrics",
    "load_extracted_records",
    "load_golden_dir",
    "load_golden_doc",
    "load_report",
    "match_items",
]
