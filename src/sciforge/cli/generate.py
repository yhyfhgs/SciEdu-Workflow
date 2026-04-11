"""CLI generate 命令组 — 占位版本

生成路径相关代码（engine/generator/retriever/solver/verifier/evolution_engine
/annotator/file_backend）已在 2026-04 清理周期中移除，等待 parse 主线稳定后
基于新 models + SQLiteStorageBackend 重建。当前所有 generate 子命令均 raise
NotImplementedError，CLI 结构保留以便 cli/main.py 正常注册。
"""

from __future__ import annotations

from pathlib import Path

import typer

from sciforge.cli.display import error

generate_app = typer.Typer(name="generate", help="AI 题目生成（暂未对接，待重建）")


_NOT_IMPLEMENTED_MSG = (
    "generate pipeline 在 models/storage 重构后暂未对接，"
    "待 parse 主线稳定后重建。详见 AGENTS.md 的 Generation Agent 段落。"
)


def _not_implemented() -> None:
    error(_NOT_IMPLEMENTED_MSG)
    raise typer.Exit(code=2)


@generate_app.command("free")
def generate_free(
    subject: str = typer.Option(..., "-s", "--subject", help="科目"),
    topic: str = typer.Option(..., "-t", "--topic", help="专题（逗号分隔）"),
    difficulty: int = typer.Option(5, "-d", "--difficulty"),
    question_type: str = typer.Option("proof", "--type"),
    count: int = typer.Option(1, "-n", "--count"),
    output_format: str = typer.Option("rich", "--format", "-f"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config"),
) -> None:
    """自由模式生成新数学题"""
    _not_implemented()


@generate_app.command("variant")
def generate_variant(
    source_id: str = typer.Argument(..., help="源题 ID"),
    strategy: str = typer.Option("A1", "--strategy"),
    difficulty: int | None = typer.Option(None, "-d", "--difficulty"),
    output_format: str = typer.Option("rich", "--format", "-f"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config"),
) -> None:
    """基于已有题目生成变式"""
    _not_implemented()


@generate_app.command("batch")
def generate_batch(
    subject: str = typer.Option(..., "-s", "--subject"),
    topic: str = typer.Option(..., "-t", "--topic"),
    difficulty: int = typer.Option(5, "-d", "--difficulty"),
    count: int = typer.Option(5, "-n", "--count"),
    question_type: str = typer.Option("proof", "--type"),
    output_format: str = typer.Option("rich", "--format", "-f"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config"),
) -> None:
    """批量生成题目"""
    _not_implemented()


@generate_app.command("solve")
def generate_solve(
    question_id: str = typer.Argument(..., help="题目 ID"),
    output_format: str = typer.Option("rich", "--format", "-f"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config"),
) -> None:
    """为指定题目生成详细解答"""
    _not_implemented()


@generate_app.command("evolve")
def generate_evolve(
    subject: str = typer.Option(..., "-s", "--subject"),
    topic: str = typer.Option(..., "-t", "--topic"),
    difficulty: int = typer.Option(5, "-d", "--difficulty"),
    question_type: str = typer.Option("proof", "--type"),
    max_improve: int = typer.Option(2, "--max-improve"),
    output_format: str = typer.Option("rich", "--format", "-f"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config"),
) -> None:
    """进化模式生成"""
    _not_implemented()


@generate_app.command("solve-deep")
def generate_solve_deep(
    question_id: str = typer.Argument(..., help="题目 ID"),
    output_format: str = typer.Option("rich", "--format", "-f"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config"),
) -> None:
    """深度求解: 多轮推理 + 代码计算辅助"""
    _not_implemented()
