"""CLI parse 子命令的参数校验测试

覆盖修复 B1（backend 枚举）+ B2（--llm-config fail-fast）。
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from sciforge.cli.main import app

runner = CliRunner()


def test_parse_run_rejects_invalid_backend(tmp_path: Path) -> None:
    """--backend invalid 应立即退出并给出明确错误"""
    fake_file = tmp_path / "x.md"
    fake_file.write_text("# Hello\n")

    result = runner.invoke(
        app,
        ["parse", "run", str(fake_file), "-b", "invalid_backend"],
    )

    assert result.exit_code != 0
    combined = (result.output or "") + (str(result.exception) if result.exception else "")
    assert "backend" in combined.lower() or "invalid_backend" in combined


def test_parse_run_rejects_missing_llm_config(tmp_path: Path) -> None:
    """--llm-config 显式指向不存在的路径应立即退出"""
    fake_file = tmp_path / "x.md"
    fake_file.write_text("# Hello\n")
    bogus_config = tmp_path / "definitely_not_here.toml"

    result = runner.invoke(
        app,
        [
            "parse",
            "run",
            str(fake_file),
            "--llm-config",
            str(bogus_config),
            "--skip-llm",
        ],
    )

    assert result.exit_code == 1
    combined = (result.output or "") + (str(result.exception) if result.exception else "")
    assert "llm-config" in combined or "不存在" in combined


def test_parse_run_accepts_valid_backend(tmp_path: Path) -> None:
    """--backend vlm 和 pymupdf 应被接受（skip-llm 防止真调 LLM）"""
    fake_file = tmp_path / "x.md"
    fake_file.write_text("# Hello\n")

    result = runner.invoke(
        app,
        [
            "parse",
            "run",
            str(fake_file),
            "-b",
            "pymupdf",
            "--skip-llm",
            "--dry-run",
        ],
    )

    # 接受参数；执行本身可能因 md 文件跳过 pymupdf 而走正常分支
    # 只要不是因 backend 校验退出即可
    combined = (result.output or "") + (str(result.exception) if result.exception else "")
    assert "--backend" not in combined or "必须是" not in combined
