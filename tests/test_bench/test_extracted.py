"""checkpoint 镜像读取（bench/extracted.py）。"""

from __future__ import annotations

import json
from pathlib import Path

from sciforge.bench.extracted import load_extracted_records


def _write_snapshot(items_dir: Path, item_id: int, **item_overrides: object) -> None:
    item: dict[str, object] = {
        "category": "question",
        "item_type": "exercise",
        "number": str(item_id),
        "solution_status": "complete",
        "corrected_content": "内容",
        "start_line": 1,
        "end_line": 3,
    }
    item.update(item_overrides)
    snapshot = {
        "item_id": item_id,
        "item": item,
        "window_lines": ["0000001 | x"],
        "stop_reason": "all_complete",
        "window_line_range": [1, 3],
        "window_last_line": 3,
        "block_index": 0,
        "source_file": "demo.md",
        "page_start": None,
        "page_end": None,
    }
    (items_dir / f"item_{item_id:06d}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
    )


def test_load_reads_mirror_sorted(tmp_path: Path) -> None:
    items_dir = tmp_path / "items" / "stage1"
    items_dir.mkdir(parents=True)
    _write_snapshot(items_dir, 7, start_line=10, end_line=12)
    _write_snapshot(items_dir, 3)
    records = load_extracted_records(tmp_path)
    assert [r.item_id for r in records] == [3, 7]
    assert records[1].start_line == 10
    assert records[0].start_char is None  # v10 镜像无 char span → 行级


def test_load_skips_corrupt_file(tmp_path: Path) -> None:
    items_dir = tmp_path / "items" / "stage1"
    items_dir.mkdir(parents=True)
    _write_snapshot(items_dir, 1)
    (items_dir / "item_000002.json").write_text("{broken", encoding="utf-8")
    records = load_extracted_records(tmp_path)
    assert [r.item_id for r in records] == [1]


def test_load_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert load_extracted_records(tmp_path) == []
