"""WP-45 parse cost-report CLI 子命令。"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sciforge.cli.main import app

runner = CliRunner()


def _write_jsonl(tmp_path: Path, lines: list[dict]) -> Path:
    path = tmp_path / "pipeline.log"
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8"
    )
    return path


def test_cost_report_renders_table(tmp_path: Path) -> None:
    log_path = _write_jsonl(
        tmp_path,
        [
            {
                "event": "llm_call_metrics",
                "stage": "stage1_extract",
                "attempt": 1,
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_read_input_tokens": 900,
                "cache_creation_input_tokens": 0,
            }
        ],
    )
    result = runner.invoke(app, ["parse", "cost-report", str(log_path)])
    assert result.exit_code == 0
    assert "stage1_extract" in result.output
    assert "总计" in result.output


def test_cost_report_empty_metrics_errors(tmp_path: Path) -> None:
    log_path = tmp_path / "empty.log"
    log_path.write_text("这不是 JSON\n", encoding="utf-8")
    result = runner.invoke(app, ["parse", "cost-report", str(log_path)])
    assert result.exit_code == 1


def test_cost_report_nonexistent_file_errors(tmp_path: Path) -> None:
    result = runner.invoke(app, ["parse", "cost-report", str(tmp_path / "does_not_exist.log")])
    assert result.exit_code != 0
