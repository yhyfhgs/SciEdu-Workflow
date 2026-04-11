"""CLI generate 命令组 — 题目/讲义/解答生成

MVP 阶段已实现：
- `generate free` — 自由模式生成题目（QGenAgent 5 阶段闭环）
- `generate solve` — 为已有题目生成详细解答（SolveAgent）
- `generate batch` — 批量 free 模式
- `generate lecture` — 生成 markdown 讲义（DocAssemblyAgent lite）

Post-MVP 占位（raise NotImplementedError）：
- `generate variant` — 变式生成
- `generate evolve` — 进化模式
- `generate solve-deep` — 深度求解（sandbox + 多轮工具调用）
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from sciforge.cli.display import console, error, success
from sciforge.core.models import (
    Discipline,
    GenerationRequest,
    VariantStrategy,
)

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

generate_app = typer.Typer(name="generate", help="AI 题目/讲义/解答生成")


# ── 共用 helper ──────────────────────────────────────


def _parse_discipline(value: str) -> Discipline:
    try:
        return Discipline(value)
    except ValueError as exc:
        allowed = ", ".join(d.value for d in Discipline)
        raise typer.BadParameter(
            f"未知学科 {value!r}；允许值: {allowed}"
        ) from exc


def _build_llm_client(
    task: str,
    llm_config_file: Path | None,
    llm_provider: str | None,
    llm_model: str | None,
) -> LLMClient:
    """按 task 构造一个 LLMClient 实例（失败直接 typer.Exit）"""
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient
    from sciforge.llm.config import LLMConfig

    if llm_config_file and not llm_config_file.exists():
        llm_config_file = None

    config = AppConfig(llm_config_file=llm_config_file)
    cli_overrides: dict[str, str] = {}
    if llm_provider:
        cli_overrides["provider"] = llm_provider
    if llm_model:
        cli_overrides["model"] = llm_model

    try:
        task_cfg = LLMConfig.from_app_config(config, task, **cli_overrides)
        return LLMClient(task_cfg)
    except Exception as exc:
        error(f"LLM 客户端初始化失败 (task={task}): {exc}")
        raise typer.Exit(1) from exc


def _open_storage(db: Path) -> SQLiteStorageBackend:
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

    if not db.parent.exists():
        db.parent.mkdir(parents=True, exist_ok=True)
    return SQLiteStorageBackend(db)


def _not_implemented(cmd: str, reason: str) -> None:
    error(f"`generate {cmd}` {reason}")
    raise typer.Exit(code=2)


# ── generate free ─────────────────────────────────────


@generate_app.command("free")
def generate_free(
    subject: str = typer.Option("", "-s", "--subject", help="科目 (如 real_analysis:实分析)"),
    topic: str = typer.Option(..., "-t", "--topic", help="专题标签（逗号分隔）"),
    difficulty: int = typer.Option(5, "-d", "--difficulty", min=1, max=10),
    question_type: str = typer.Option("proof", "--type", help="题型 (proof/calculation/...)"),
    count: int = typer.Option(1, "-n", "--count", help="生成数量"),
    discipline: str = typer.Option(
        "math", "--discipline", "-D", help="学科 (math/physics/...)"
    ),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="SQLite 数据库路径"),
    save: bool = typer.Option(True, "--save/--no-save", help="是否保存生成结果到 DB"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        Path("config/sciforge.toml"), "--llm-config"
    ),
) -> None:
    """自由模式生成新题目（QGenAgent 5 阶段闭环）"""
    from sciforge.agents import QGenAgent, QGenInput

    discipline_enum = _parse_discipline(discipline)
    topics = [t.strip() for t in topic.split(",") if t.strip()]
    if not topics:
        error("至少提供一个 topic")
        raise typer.Exit(1)

    llm = _build_llm_client("generate", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)

    try:
        agent = QGenAgent(llm=llm, storage=storage, discipline=discipline_enum)

        async def run_once() -> None:
            request = GenerationRequest(
                discipline=discipline_enum,
                subject=subject,
                topics=topics,
                difficulty_target=difficulty,
                question_type=question_type,
                strategy=VariantStrategy.TOPIC_GENERATION,
            )

            for i in range(count):
                console.print(f"[bold cyan]── 第 {i + 1}/{count} 轮 ──[/]")
                result = await agent.run(QGenInput(request=request))
                if result.generated is None:
                    error(f"  ✗ 未生成合格题目: {result.reason}")
                    continue
                gq = result.generated
                _print_generated(gq)
                if save:
                    storage.save_batch([], [gq.question])
                    success(f"  ✓ 已保存题目 id={gq.question.id[:8]}")

        asyncio.run(run_once())
    finally:
        storage.close()


# ── generate solve ────────────────────────────────────


@generate_app.command("solve")
def generate_solve(
    question_id: str = typer.Argument(..., help="题目 ID"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
    save: bool = typer.Option(
        False,
        "--save/--no-save",
        help="是否把生成的 solution 写回 question.solution_content",
    ),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        Path("config/sciforge.toml"), "--llm-config"
    ),
) -> None:
    """为已有题目生成详细解答"""
    from sciforge.agents import SolveAgent, SolveInput

    discipline_enum = _parse_discipline(discipline)
    llm = _build_llm_client("solve", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)

    try:
        agent = SolveAgent(llm=llm, storage=storage, discipline=discipline_enum)

        async def run_once() -> None:
            result = await agent.run(SolveInput(question_id=question_id))
            if result.solution is None:
                error("求解失败")
                raise typer.Exit(1)
            console.print(f"[bold green]置信度:[/] {result.confidence}")
            console.print("[bold]解答:[/]")
            console.print(result.solution.solution_latex or "(空)")

            if save:
                q = storage.load_question(question_id)
                q.solution_content = result.solution.solution_latex
                storage.save_batch([], [q])
                success(f"已更新 question {question_id[:8]}.solution_content")

        asyncio.run(run_once())
    finally:
        storage.close()


# ── generate batch ────────────────────────────────────


@generate_app.command("batch")
def generate_batch(
    subject: str = typer.Option("", "-s", "--subject"),
    topic: str = typer.Option(..., "-t", "--topic"),
    difficulty: int = typer.Option(5, "-d", "--difficulty", min=1, max=10),
    count: int = typer.Option(5, "-n", "--count"),
    question_type: str = typer.Option("proof", "--type"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
    save: bool = typer.Option(True, "--save/--no-save"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        Path("config/sciforge.toml"), "--llm-config"
    ),
) -> None:
    """批量生成题目（内部循环 QGenAgent.run）"""
    # 直接复用 free 的实现
    generate_free(
        subject=subject,
        topic=topic,
        difficulty=difficulty,
        question_type=question_type,
        count=count,
        discipline=discipline,
        db=db,
        save=save,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_config=llm_config,
    )


# ── generate lecture ──────────────────────────────────


@generate_app.command("lecture")
def generate_lecture(
    topic: str = typer.Option(..., "-t", "--topic", help="专题标签（逗号分隔）"),
    title: str = typer.Option("", "--title"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    audience: str = typer.Option("大学一年级", "--audience"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
    output_dir: Path = typer.Option(
        Path("data/lectures"), "--output", "-o", help="讲义输出目录"
    ),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        Path("config/sciforge.toml"), "--llm-config"
    ),
) -> None:
    """生成 markdown 讲义（DocAssemblyAgent lite）"""
    from sciforge.agents import DocAssemblyAgent, DocInput

    discipline_enum = _parse_discipline(discipline)
    topics = [t.strip() for t in topic.split(",") if t.strip()]
    if not topics:
        error("至少提供一个 topic")
        raise typer.Exit(1)

    llm = _build_llm_client("generate", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)

    try:
        agent = DocAssemblyAgent(
            llm=llm, storage=storage, discipline=discipline_enum
        )

        async def run_once() -> None:
            result = await agent.run(
                DocInput(
                    topic_tags=topics,
                    title=title,
                    target_audience=audience,
                    output_format="markdown",
                )
            )

            output_dir.mkdir(parents=True, exist_ok=True)
            md_path = output_dir / f"{result.lecture.id}.md"
            json_path = output_dir / f"{result.lecture.id}.json"
            md_path.write_text(result.markdown_text, encoding="utf-8")
            json_path.write_text(
                result.lecture.model_dump_json(indent=2), encoding="utf-8"
            )

            console.print(f"[bold]讲义标题:[/] {result.lecture.title}")
            console.print(f"[bold]章节数:[/] {len(result.lecture.sections)}")
            success(f"已生成 {md_path}")
            success(f"已生成 {json_path}")

        asyncio.run(run_once())
    finally:
        storage.close()


# ── Post-MVP 占位 ─────────────────────────────────────


@generate_app.command("variant")
def generate_variant(
    source_id: str = typer.Argument(..., help="源题 ID"),
    strategy: str = typer.Option("A1", "--strategy"),
    difficulty: int | None = typer.Option(None, "-d", "--difficulty"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
) -> None:
    """基于已有题目生成变式 (Post-MVP)"""
    _not_implemented("variant", "是 Post-MVP 特性；MVP 请用 `generate free` 从头生成题目")


@generate_app.command("evolve")
def generate_evolve(
    subject: str = typer.Option("", "-s", "--subject"),
    topic: str = typer.Option(..., "-t", "--topic"),
    difficulty: int = typer.Option(5, "-d", "--difficulty"),
    question_type: str = typer.Option("proof", "--type"),
    max_improve: int = typer.Option(2, "--max-improve"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
) -> None:
    """进化模式（失败后迭代改进） (Post-MVP)"""
    _not_implemented("evolve", "是 Post-MVP 特性；MVP 的 QGenAgent 已内建多轮重试")


@generate_app.command("solve-deep")
def generate_solve_deep(
    question_id: str = typer.Argument(..., help="题目 ID"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
) -> None:
    """深度求解（多轮推理 + 代码沙箱） (Post-MVP)"""
    _not_implemented(
        "solve-deep",
        "是 Post-MVP 特性（依赖 sandbox + tool calling）；MVP 请用 `generate solve`",
    )


# ── 输出辅助 ──────────────────────────────────────────


def _print_generated(gq: object) -> None:
    """打印 GeneratedQuestion 结果"""
    from rich.table import Table

    from sciforge.core.models import GeneratedQuestion

    if not isinstance(gq, GeneratedQuestion):
        console.print(str(gq))
        return

    table = Table(title=f"生成题目 [{gq.question.discipline.value}]", show_lines=True)
    table.add_column("字段", style="cyan", no_wrap=True)
    table.add_column("内容", style="white")

    table.add_row("id", gq.question.id[:12])
    table.add_row("题型", str(gq.question.question_type))
    table.add_row("难度", f"{gq.difficulty_score.overall}/10")
    table.add_row("内容", gq.question.content[:400])
    table.add_row("解答", (gq.solution.solution_latex or "")[:400])
    table.add_row(
        "验证",
        "通过" if gq.verification.passed else "未通过",
    )

    console.print(table)

    if gq.difficulty_score.reasoning:
        console.print(f"[dim]难度理由: {gq.difficulty_score.reasoning}[/]")
