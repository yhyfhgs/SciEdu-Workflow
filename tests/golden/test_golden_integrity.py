"""golden 标注完整性校验：gt JSON 可加载、fixture 存在、行号不越界。

WP-02 起每份提交的标注都必须过这组校验；目录为空时自动跳过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.bench.gt_models import GoldenDoc, load_golden_doc

_GOLDEN_ROOT = Path(__file__).parent
_GT_DIR = _GOLDEN_ROOT / "gt"
_DOCS_DIR = _GOLDEN_ROOT / "docs"

_gt_files = sorted(_GT_DIR.glob("*.json")) if _GT_DIR.is_dir() else []


def _load(path: Path) -> tuple[GoldenDoc, list[str]]:
    doc = load_golden_doc(path)
    fixture = _DOCS_DIR / doc.source_fixture
    assert fixture.is_file(), f"{doc.doc_id}: fixture 不存在 {doc.source_fixture}"
    return doc, fixture.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("gt_path", _gt_files, ids=lambda p: p.stem)
def test_gt_loads_and_lines_in_range(gt_path: Path) -> None:
    doc, lines = _load(gt_path)
    n = len(lines)
    assert doc.items or doc.markers, f"{doc.doc_id}: 空标注"
    for marker in doc.markers:
        assert marker.line <= n, f"{doc.doc_id}: marker line {marker.line} 越界（文档 {n} 行）"
    for item in doc.items:
        assert item.end_line <= n, f"{doc.doc_id}/{item.gt_id}: end_line {item.end_line} 越界"
        if item.end_char is not None:
            assert item.end_char <= len(lines[item.end_line - 1]), (
                f"{doc.doc_id}/{item.gt_id}: end_char 越过行尾"
            )


@pytest.mark.parametrize("gt_path", _gt_files, ids=lambda p: p.stem)
def test_gt_doc_id_matches_filename(gt_path: Path) -> None:
    doc = load_golden_doc(gt_path)
    assert doc.doc_id == gt_path.stem, "gt 文件名必须等于 doc_id"


def test_golden_dirs_exist() -> None:
    assert _GT_DIR.is_dir() and _DOCS_DIR.is_dir()
