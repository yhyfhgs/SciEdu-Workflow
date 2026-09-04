"""bench 运行编排：gt 选择、fixture 读取、单文档跑分（CLI 之下的纯逻辑层）。"""

from __future__ import annotations

from pathlib import Path

from sciforge.bench.extracted import load_extracted_records
from sciforge.bench.gt_models import GoldenDoc, load_golden_dir
from sciforge.bench.metrics import DocBenchResult, compute_doc_metrics


def select_golden_docs(golden_root: Path, doc_ids: list[str] | None) -> list[GoldenDoc]:
    """按 doc_id 选择标注集；``doc_ids=None`` = 全部已标注。

    请求了不存在的 doc_id 时抛 ValueError（列出可用 id 便于排查）。
    """
    gt_dir = golden_root / "gt"
    all_docs = load_golden_dir(gt_dir) if gt_dir.is_dir() else []
    if not doc_ids:
        return all_docs
    by_id = {d.doc_id: d for d in all_docs}
    missing = [i for i in doc_ids if i not in by_id]
    if missing:
        available = ", ".join(sorted(by_id)) or "（无）"
        raise ValueError(f"未找到标注 {missing}；可用 doc_id: {available}")
    return [by_id[i] for i in doc_ids]


def fixture_path(golden_root: Path, doc: GoldenDoc) -> Path:
    """gt 引用的 pinned markdown fixture 绝对路径（不存在即抛错）。"""
    path = golden_root / "docs" / doc.source_fixture
    if not path.is_file():
        raise FileNotFoundError(f"{doc.doc_id}: fixture 不存在 {path}")
    return path


def fixture_lines(golden_root: Path, doc: GoldenDoc) -> list[str]:
    return fixture_path(golden_root, doc).read_text(encoding="utf-8").splitlines()


def bench_doc_from_checkpoint(
    doc: GoldenDoc, golden_root: Path, checkpoint_dir: Path
) -> DocBenchResult:
    """对单文档已有 checkpoint（``<checkpoint>/<hash>``）离线跑分，0 LLM。"""
    records = load_extracted_records(checkpoint_dir)
    return compute_doc_metrics(doc, records, fixture_lines(golden_root, doc))
