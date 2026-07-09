"""CLI generate 子命令的参数校验测试

覆盖修复 B2（--llm-config fail-fast）+ B3（solve 题目不存在保护）。
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from sciforge.agents.runtime import RunStatus, SQLiteRunStore
from sciforge.cli.main import app

runner = CliRunner()


def test_generate_free_rejects_missing_llm_config(tmp_path: Path) -> None:
    """--llm-config 显式指向不存在的路径应立即退出"""
    bogus_config = tmp_path / "missing.toml"

    result = runner.invoke(
        app,
        [
            "generate",
            "free",
            "-t",
            "math:数学",
            "--llm-config",
            str(bogus_config),
        ],
    )

    assert result.exit_code == 1
    combined = (result.output or "") + (str(result.exception) if result.exception else "")
    assert "llm-config" in combined or "不存在" in combined


def test_generate_solve_rejects_missing_llm_config(tmp_path: Path) -> None:
    bogus_config = tmp_path / "no.toml"
    result = runner.invoke(
        app,
        [
            "generate",
            "solve",
            "dummy-id",
            "--llm-config",
            str(bogus_config),
        ],
    )
    assert result.exit_code == 1


def test_generate_free_rejects_empty_topic(tmp_path: Path) -> None:
    """空 topic 应立即 fail"""
    result = runner.invoke(
        app,
        ["generate", "free", "-t", " , , "],
    )
    assert result.exit_code == 1
    combined = (result.output or "") + (str(result.exception) if result.exception else "")
    assert "topic" in combined


def test_generate_batch_docstring_is_alias(tmp_path: Path) -> None:
    """generate batch --help 应清晰标注为 free 的别名"""
    result = runner.invoke(app, ["generate", "batch", "--help"])
    assert result.exit_code == 0
    # 应明确指出是 free --count 的别名
    assert "free" in result.output or "别名" in result.output


def test_generate_durable_commands_are_exposed() -> None:
    result = runner.invoke(app, ["generate", "--help"])

    assert result.exit_code == 0
    assert "durable" in result.output
    assert "resume" in result.output
    assert "cancel" in result.output


def test_generate_cancel_immediately_finishes_pending_run(tmp_path: Path) -> None:
    runtime_db = tmp_path / "runtime.db"
    store = SQLiteRunStore(runtime_db)
    store.create_run("qgen", {"topics": ["analysis"]}, run_id="cli-cancel")

    result = runner.invoke(
        app,
        ["generate", "cancel", "cli-cancel", "--runtime-db", str(runtime_db)],
    )

    assert result.exit_code == 0
    assert "cancelled" in result.output
    assert store.require_run("cli-cancel").status is RunStatus.CANCELLED
