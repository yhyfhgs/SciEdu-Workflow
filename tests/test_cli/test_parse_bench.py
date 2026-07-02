"""parse bench 子命令（WP-01）——离线 --from-checkpoint 模式与 --compare 门禁。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sciforge.cli.main import app

runner = CliRunner()


def _make_golden(root: Path) -> None:
    """构造最小 golden root：3 行 fixture + 1 条 gt。"""
    (root / "docs").mkdir(parents=True)
    (root / "gt").mkdir()
    (root / "docs" / "demo.md").write_text("习题 1\n求证 1+1=2。\n证明：略。\n", encoding="utf-8")
    gt = {
        "doc_id": "demo",
        "source_fixture": "demo.md",
        "genre": "exam_paper",
        "markers": [{"line": 1, "kind": "exercise", "number": "1"}],
        "items": [
            {
                "gt_id": "ex-1",
                "category": "question",
                "kind": "exercise",
                "number": "1",
                "start_line": 1,
                "end_line": 3,
                "solution_status": "complete",
            }
        ],
    }
    (root / "gt" / "demo.json").write_text(json.dumps(gt, ensure_ascii=False), encoding="utf-8")


def _make_checkpoint(root: Path, *, start: int = 1, end: int = 3) -> Path:
    items_dir = root / "items" / "stage1"
    items_dir.mkdir(parents=True)
    snapshot = {
        "item_id": 1,
        "item": {
            "category": "question",
            "item_type": "exercise",
            "number": "1",
            "solution_status": "complete",
            "corrected_content": "求证 1+1=2。",
            "start_line": start,
            "end_line": end,
        },
        "window_lines": [],
        "stop_reason": "all_complete",
        "window_line_range": [1, 3],
        "window_last_line": 3,
        "block_index": 0,
        "source_file": "demo.md",
        "page_start": None,
        "page_end": None,
    }
    (items_dir / "item_000001.json").write_text(
        json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
    )
    return root


def _bench(*args: str) -> object:
    return runner.invoke(app, ["parse", "bench", *args])


def test_bench_from_checkpoint_outputs_report(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    _make_golden(golden)
    checkpoint = _make_checkpoint(tmp_path / "chk")
    report_path = tmp_path / "report.json"

    result = _bench(
        "--doc",
        "demo",
        "--golden-root",
        str(golden),
        "--from-checkpoint",
        str(checkpoint),
        "--output",
        str(report_path),
    )
    assert result.exit_code == 0, result.output
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["docs"][0]["doc_id"] == "demo"
    assert report["docs"][0]["item_recall"] == 1.0
    assert report["docs"][0]["marker_recall"] == 1.0
    assert report["aggregate"]["pairing_accuracy"] == 1.0


def test_bench_compare_passes_when_no_regression(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    _make_golden(golden)
    checkpoint = _make_checkpoint(tmp_path / "chk")
    baseline = tmp_path / "baseline.json"

    first = _bench(
        "--doc",
        "demo",
        "--golden-root",
        str(golden),
        "--from-checkpoint",
        str(checkpoint),
        "--output",
        str(baseline),
    )
    assert first.exit_code == 0, first.output

    again = _bench(
        "--doc",
        "demo",
        "--golden-root",
        str(golden),
        "--from-checkpoint",
        str(checkpoint),
        "--compare",
        str(baseline),
    )
    assert again.exit_code == 0, again.output
    assert "无退化" in again.output


def test_bench_compare_fails_on_regression(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    _make_golden(golden)
    good_chk = _make_checkpoint(tmp_path / "chk_good")
    baseline = tmp_path / "baseline.json"
    assert (
        _bench(
            "--doc",
            "demo",
            "--golden-root",
            str(golden),
            "--from-checkpoint",
            str(good_chk),
            "--output",
            str(baseline),
        ).exit_code
        == 0
    )

    # 边界大幅缩水的提取 → char_iou 退化
    bad_chk = _make_checkpoint(tmp_path / "chk_bad", start=3, end=3)
    result = _bench(
        "--doc",
        "demo",
        "--golden-root",
        str(golden),
        "--from-checkpoint",
        str(bad_chk),
        "--compare",
        str(baseline),
    )
    assert result.exit_code == 1
    assert "退化" in result.output


def test_bench_unknown_doc_fails_fast(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    _make_golden(golden)
    result = _bench("--doc", "nope", "--golden-root", str(golden))
    assert result.exit_code == 1
    assert "未找到标注" in result.output


def test_bench_from_checkpoint_requires_single_doc(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    _make_golden(golden)
    # 不带 --doc（选择全部 = 1 份）本应可行；这里验证显式两份的拒绝路径需要第二份 gt
    gt2 = json.loads((golden / "gt" / "demo.json").read_text(encoding="utf-8"))
    gt2["doc_id"] = "demo2"
    (golden / "gt" / "demo2.json").write_text(json.dumps(gt2, ensure_ascii=False), encoding="utf-8")
    result = _bench(
        "--doc",
        "demo",
        "--doc",
        "demo2",
        "--golden-root",
        str(golden),
        "--from-checkpoint",
        str(tmp_path / "chk"),
    )
    assert result.exit_code == 1
    assert "恰好一个" in result.output


def test_bench_empty_gt_dir_hints_annotation(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    (golden / "gt").mkdir(parents=True)
    (golden / "docs").mkdir()
    result = _bench("--golden-root", str(golden))
    assert result.exit_code == 1
    assert "标注" in result.output
