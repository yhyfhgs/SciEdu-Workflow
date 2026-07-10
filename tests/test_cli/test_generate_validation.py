"""CLI generate 子命令的参数校验测试

覆盖修复 B2（--llm-config fail-fast）+ B3（solve 题目不存在保护）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from sciforge.agents.runtime import RunStatus, SQLiteRunStore
from sciforge.cli.generate import _resolve_question_contract, _save_generated_question
from sciforge.cli.main import app
from sciforge.core.models import (
    DifficultyScore,
    Discipline,
    GeneratedQuestion,
    Question,
    Solution,
    VerificationResult,
)
from sciforge.storage.sqlite_backend import SQLiteStorageBackend

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


def test_question_contract_uses_per_discipline_default_and_rejects_bad_form() -> None:
    assert _resolve_question_contract(Discipline.PHYSICS, "", "choice") == (
        "calculation",
        "choice",
    )

    with pytest.raises(typer.BadParameter, match="video_essay"):
        _resolve_question_contract(Discipline.BIOLOGY, "", "video_essay")


def test_durable_cli_defaults_to_shared_qgen_runtime_db() -> None:
    for command in ("durable", "resume", "cancel"):
        result = runner.invoke(
            app,
            ["generate", command, "--help"],
            terminal_width=240,
        )
        assert result.exit_code == 0
        assert "qgen_runtime.db" in result.output


def test_save_generated_question_materializes_public_solution(tmp_path: Path) -> None:
    question = Question(id="generated", content="一道可复用的完整测试题目。")
    generated = GeneratedQuestion(
        question=question,
        solution=Solution(
            question_id=question.id,
            solution_latex="第一步分析条件；第二步推导并得到结论。",
            confidence="high",
        ),
        difficulty_score=DifficultyScore(overall=5.0),
        verification=VerificationResult(passed=True),
    )
    with SQLiteStorageBackend(tmp_path / "bank.db") as storage:
        saved = _save_generated_question(storage, generated)
        loaded = storage.load_question(saved.id)

    assert loaded.solution_content == "第一步分析条件；第二步推导并得到结论。"
    assert generated.question.solution_content == loaded.solution_content


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
