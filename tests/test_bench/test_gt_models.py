"""gt 数据模型（bench/gt_models.py）——schema 约束与加载。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sciforge.bench.gt_models import GoldenDoc, GtItem, GtMarker, load_golden_dir, load_golden_doc


def _item(**overrides: object) -> GtItem:
    base: dict[str, object] = {
        "gt_id": "thm-1",
        "category": "knowledge",
        "kind": "theorem",
        "start_line": 3,
        "end_line": 10,
    }
    base.update(overrides)
    return GtItem.model_validate(base)


def test_gt_item_rejects_inverted_range() -> None:
    with pytest.raises(ValidationError, match="start_line"):
        _item(start_line=10, end_line=3)


def test_gt_item_defaults_line_level() -> None:
    item = _item()
    assert item.start_char == 0
    assert item.end_char is None  # 行尾
    assert item.solution_status == "complete"
    assert item.crosses_window is False


def test_gt_marker_requires_kind() -> None:
    with pytest.raises(ValidationError):
        GtMarker.model_validate({"line": 1, "kind": ""})


def test_golden_doc_rejects_duplicate_gt_ids() -> None:
    with pytest.raises(ValidationError, match="重复 gt_id"):
        GoldenDoc(
            doc_id="d",
            source_fixture="d.md",
            items=[_item(), _item()],
        )


def test_golden_doc_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        GoldenDoc.model_validate({"doc_id": "d", "source_fixture": "d.md", "unexpected": 1})


def test_load_golden_doc_roundtrip(tmp_path: Path) -> None:
    doc = GoldenDoc(
        doc_id="demo",
        source_fixture="demo.md",
        genre="exam_paper",
        markers=[GtMarker(line=1, kind="exercise", number="1")],
        items=[_item(gt_id="ex-1", category="question", kind="exercise")],
    )
    path = tmp_path / "demo.json"
    path.write_text(json.dumps(doc.model_dump(), ensure_ascii=False), encoding="utf-8")
    loaded = load_golden_doc(path)
    assert loaded == doc


def test_load_golden_dir_sorted(tmp_path: Path) -> None:
    for name in ("b", "a"):
        doc = GoldenDoc(doc_id=name, source_fixture=f"{name}.md")
        (tmp_path / f"{name}.json").write_text(
            json.dumps(doc.model_dump(), ensure_ascii=False), encoding="utf-8"
        )
    assert [d.doc_id for d in load_golden_dir(tmp_path)] == ["a", "b"]
