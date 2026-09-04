"""CLI generate 命令组 — 题目/讲义/解答生成

已实现：
- `generate free` — 自由模式生成题目（QGen 状态图 Harness 低延迟路径）
- `generate solve` — 为已有题目生成详细解答（SolveAgent）
- `generate batch` — 批量 free 模式
- `generate lecture` — 生成 markdown 讲义（DocAssemblyAgent lite）
- `generate variant` — 基于源题的 A1-A6/B1-B2 变式生成
- `generate evolve` — 进化模式（失败后 improve_* 定向改进迭代）
- `generate solve-deep` — 深度求解（多轮推理 + sandbox 工具调用）
- `generate durable` — 创建可恢复、带硬预算的长时出题任务
- `generate resume` / `cancel` — 恢复或取消持久化任务
"""

from __future__ import annotations

import asyncio
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
    from sciforge.core.models import GeneratedQuestion, Question
    from sciforge.llm.client import LLMClient
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

generate_app = typer.Typer(name="generate", help="AI 题目/讲义/解答生成")

_DEFAULT_LLM_CONFIG = Path("config/sciforge.toml")
_DEFAULT_QGEN_RUNTIME_DB = Path("data/qgen_runtime.db")


def _resolve_llm_config(p: Path | None) -> Path | None:
    """统一解析 --llm-config：显式错误路径 fail-fast，未指定时回退到默认路径"""
    if p is not None:
        if not p.exists():
            error(f"--llm-config 路径不存在: {p}")
            raise typer.Exit(1)
        return p
    return _DEFAULT_LLM_CONFIG if _DEFAULT_LLM_CONFIG.exists() else None


# ── 共用 helper ──────────────────────────────────────


def _parse_discipline(value: str) -> Discipline:
    try:
        return Discipline(value)
    except ValueError as exc:
        allowed = ", ".join(d.value for d in Discipline)
        raise typer.BadParameter(f"未知学科 {value!r}；允许值: {allowed}") from exc


def _parse_strategy(value: str) -> VariantStrategy:
    """把 'A1' / 'A1_parameter_shift' / 'parameter_shift' 解析为 VariantStrategy。"""
    v = value.strip().lower()
    for s in VariantStrategy:
        prefix, _, name = s.value.partition("_")  # 'A1', 'parameter_shift'
        if v in (s.value, prefix, name):
            return s
    allowed = ", ".join(s.value for s in VariantStrategy)
    raise typer.BadParameter(f"未知变式策略 {value!r}；允许值: {allowed}")


def _resolve_question_contract(
    discipline: Discipline,
    question_type: str,
    question_form: str,
) -> tuple[str, str]:
    """Resolve per-discipline defaults and reject drift before LLM setup."""

    from sciforge.disciplines import DISCIPLINE_REGISTRY

    spec = DISCIPLINE_REGISTRY.get(discipline)
    resolved_type = question_type.strip() or spec.allowed_question_types[0]
    resolved_form = question_form.strip()
    if resolved_type not in spec.allowed_question_types:
        raise typer.BadParameter(
            f"学科 {discipline.value} 不支持题型 {resolved_type!r}；"
            f"允许值: {', '.join(spec.allowed_question_types)}",
            param_hint="--type",
        )
    if resolved_form and resolved_form not in spec.allowed_question_forms:
        raise typer.BadParameter(
            f"学科 {discipline.value} 不支持题面形式 {resolved_form!r}；"
            f"允许值: {', '.join(spec.allowed_question_forms)}",
            param_hint="--form",
        )
    return resolved_type, resolved_form


def _resolve_provider_override(llm_provider: str | None) -> dict[str, str]:
    """把 CLI --llm-provider 智能分派为 provider_ref 或 raw provider type

    - 若值匹配已知 LLMProvider 枚举 → 当 raw type
    - 否则当 provider_ref 别名
    """
    from sciforge.llm.config import LLMProvider

    if not llm_provider:
        return {}
    known_types = {e.value for e in LLMProvider}
    if llm_provider in known_types:
        return {"provider": llm_provider}
    return {"provider_ref": llm_provider}


def _build_llm_client(
    task: str,
    llm_config_file: Path | None,
    llm_provider: str | None,
    llm_model: str | None,
) -> LLMClient:
    """按 task 构造一个 LLMClient 实例（失败直接 typer.Exit）

    调用方应已用 `_resolve_llm_config` 预处理 llm_config_file；这里不再做 silent fallback。
    """
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient
    from sciforge.llm.config import LLMConfig

    config = AppConfig(llm_config_file=llm_config_file)
    cli_overrides: dict[str, str] = _resolve_provider_override(llm_provider)
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


def _save_generated_question(
    storage: SQLiteStorageBackend,
    generated: GeneratedQuestion,
) -> Question:
    """Persist the reusable question and its public step-by-step solution together."""

    solution_text = (generated.solution.solution_latex or generated.solution.solution_plain).strip()
    question = generated.question.model_copy(update={"solution_content": solution_text or None})
    storage.save_batch([], [question])
    generated.question = question
    return question


# ── generate free ─────────────────────────────────────


@generate_app.command("free")
def generate_free(
    subject: str = typer.Option("", "-s", "--subject", help="科目 (如 real_analysis:实分析)"),
    topic: str = typer.Option(..., "-t", "--topic", help="专题标签（逗号分隔）"),
    difficulty: int = typer.Option(5, "-d", "--difficulty", min=1, max=10),
    question_type: str = typer.Option("", "--type", help="认知题型；留空使用该学科默认值"),
    question_form: str = typer.Option(
        "", "--form", help="题面形式 (choice/fill_blank/subjective/...)"
    ),
    count: int = typer.Option(1, "-n", "--count", help="生成数量"),
    discipline: str = typer.Option("math", "--discipline", "-D", help="学科 (math/physics/...)"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="SQLite 数据库路径"),
    save: bool = typer.Option(True, "--save/--no-save", help="是否保存生成结果到 DB"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        None,
        "--llm-config",
        help="LLM TOML 配置文件路径（未指定时默认读 config/sciforge.toml）",
    ),
) -> None:
    """自由模式生成新题目（QGen 状态图 Harness 低延迟路径）。"""
    from sciforge.agents import QGenAgent, QGenInput

    discipline_enum = _parse_discipline(discipline)
    question_type, question_form = _resolve_question_contract(
        discipline_enum, question_type, question_form
    )
    topics = [t.strip() for t in topic.split(",") if t.strip()]
    if not topics:
        error("至少提供一个 topic")
        raise typer.Exit(1)

    llm_config = _resolve_llm_config(llm_config)
    llm = _build_llm_client("generate", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)

    try:
        from sciforge.config.settings import AppConfig

        app_cfg = AppConfig()
        agent = QGenAgent.from_config(
            llm=llm,
            storage=storage,
            discipline=discipline_enum,
            config=app_cfg,
        )

        async def run_once() -> None:
            request = GenerationRequest(
                discipline=discipline_enum,
                subject=subject,
                topics=topics,
                difficulty_target=difficulty,
                question_type=question_type,
                question_form=question_form,
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
                    saved = _save_generated_question(storage, gq)
                    success(f"  ✓ 已保存题目 id={saved.id[:8]}")

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
        None,
        "--llm-config",
        help="LLM TOML 配置文件路径（未指定时默认读 config/sciforge.toml）",
    ),
) -> None:
    """为已有题目生成详细解答"""
    from sciforge.agents import SolveAgent, SolveInput

    discipline_enum = _parse_discipline(discipline)
    llm_config = _resolve_llm_config(llm_config)
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
                try:
                    q = storage.load_question(question_id)
                except FileNotFoundError:
                    error(f"题目不存在: {question_id}")
                    raise typer.Exit(1) from None
                if q is None:
                    error(f"题目不存在: {question_id}")
                    raise typer.Exit(1)
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
    question_type: str = typer.Option("", "--type"),
    question_form: str = typer.Option("", "--form"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
    save: bool = typer.Option(True, "--save/--no-save"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        None,
        "--llm-config",
        help="LLM TOML 配置文件路径（未指定时默认读 config/sciforge.toml）",
    ),
) -> None:
    """批量生成题目（等价于 `generate free --count N`，仅作为便捷别名）"""
    # 直接复用 free 的实现；_resolve_llm_config 会在 generate_free 内部调用
    generate_free(
        subject=subject,
        topic=topic,
        difficulty=difficulty,
        question_type=question_type,
        question_form=question_form,
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
    output_dir: Path = typer.Option(Path("data/lectures"), "--output", "-o", help="讲义输出目录"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        None,
        "--llm-config",
        help="LLM TOML 配置文件路径（未指定时默认读 config/sciforge.toml）",
    ),
) -> None:
    """生成 markdown 讲义（DocAssemblyAgent lite）"""
    from sciforge.agents import DocAssemblyAgent, DocInput

    discipline_enum = _parse_discipline(discipline)
    topics = [t.strip() for t in topic.split(",") if t.strip()]
    if not topics:
        error("至少提供一个 topic")
        raise typer.Exit(1)

    llm_config = _resolve_llm_config(llm_config)
    llm = _build_llm_client("generate", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)

    try:
        agent = DocAssemblyAgent(llm=llm, storage=storage, discipline=discipline_enum)

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
            json_path.write_text(result.lecture.model_dump_json(indent=2), encoding="utf-8")

            console.print(f"[bold]讲义标题:[/] {result.lecture.title}")
            console.print(f"[bold]章节数:[/] {len(result.lecture.sections)}")
            success(f"已生成 {md_path}")
            success(f"已生成 {json_path}")

        asyncio.run(run_once())
    finally:
        storage.close()


# ── 变式 / 进化 / 长时运行 ─────────────────────────────


@generate_app.command("variant")
def generate_variant(
    source_id: str = typer.Argument(..., help="源题 ID"),
    strategy: str = typer.Option("A1", "--strategy", help="变式策略 A1-A6/B1-B2（或全名）"),
    difficulty: int | None = typer.Option(None, "-d", "--difficulty", min=1, max=10),
    question_form: str = typer.Option("", "--form", help="覆盖源题题面形式；留空保持源题形式"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
    save: bool = typer.Option(True, "--save/--no-save"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        None, "--llm-config", help="LLM TOML 配置文件路径（默认 config/sciforge.toml）"
    ),
) -> None:
    """基于已有题目按 A1-A6/B1-B2 策略生成变式"""
    from sciforge.agents import QGenAgent, QGenInput
    from sciforge.config.settings import AppConfig

    discipline_enum = _parse_discipline(discipline)
    strat = _parse_strategy(strategy)
    storage = _open_storage(db)

    try:
        try:
            source_q = storage.load_question(source_id)
        except FileNotFoundError:
            error(f"源题不存在: {source_id}")
            raise typer.Exit(1) from None
        if source_q.discipline is not discipline_enum:
            raise typer.BadParameter(
                f"源题学科为 {source_q.discipline.value}，与 --discipline "
                f"{discipline_enum.value} 不一致",
                param_hint="--discipline",
            )
        resolved_type, resolved_form = _resolve_question_contract(
            discipline_enum,
            source_q.question_type,
            question_form or source_q.question_form,
        )
        llm_config = _resolve_llm_config(llm_config)
        llm = _build_llm_client("generate", llm_config, llm_provider, llm_model)

        app_cfg = AppConfig()
        agent = QGenAgent.from_config(
            llm=llm,
            storage=storage,
            discipline=discipline_enum,
            config=app_cfg,
        )

        async def run_once() -> None:
            request = GenerationRequest(
                discipline=discipline_enum,
                subject=source_q.subject,
                topics=list(source_q.tags),
                difficulty_target=difficulty or source_q.difficulty,
                question_type=resolved_type,
                question_form=resolved_form,
                strategy=strat,
                source_question=source_q,
            )
            console.print(f"[bold cyan]── 变式生成（策略 {strat.value}）──[/]")
            result = await agent.run(QGenInput(request=request))
            if result.generated is None:
                error(f"  ✗ 未生成合格变式: {result.reason}")
                raise typer.Exit(1)
            _print_generated(result.generated)
            if save:
                saved = _save_generated_question(storage, result.generated)
                success(f"  ✓ 已保存变式题 id={saved.id[:8]}")

        asyncio.run(run_once())
    finally:
        storage.close()


@generate_app.command("evolve")
def generate_evolve(
    subject: str = typer.Option("", "-s", "--subject"),
    topic: str = typer.Option(..., "-t", "--topic"),
    difficulty: int = typer.Option(5, "-d", "--difficulty", min=1, max=10),
    question_type: str = typer.Option("", "--type"),
    question_form: str = typer.Option("", "--form"),
    max_improve: int = typer.Option(2, "--max-improve", help="每候选最多进化改进轮数"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
    save: bool = typer.Option(True, "--save/--no-save"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        None, "--llm-config", help="LLM TOML 配置文件路径（默认 config/sciforge.toml）"
    ),
) -> None:
    """进化模式：生成失败后用 improve_* prompt 定向改进迭代（QGenAgent 进化闭环）"""
    from sciforge.agents import QGenAgent, QGenInput
    from sciforge.config.settings import AppConfig

    discipline_enum = _parse_discipline(discipline)
    question_type, question_form = _resolve_question_contract(
        discipline_enum, question_type, question_form
    )
    topics = [t.strip() for t in topic.split(",") if t.strip()]
    if not topics:
        error("至少提供一个 topic")
        raise typer.Exit(1)

    llm_config = _resolve_llm_config(llm_config)
    llm = _build_llm_client("generate", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)

    try:
        app_cfg = AppConfig()
        agent = QGenAgent.from_config(
            llm=llm,
            storage=storage,
            discipline=discipline_enum,
            config=app_cfg,
            enable_evolution=True,
            max_improvement_rounds=max_improve,
            population_selection_enabled=True,
        )

        async def run_once() -> None:
            request = GenerationRequest(
                discipline=discipline_enum,
                subject=subject,
                topics=topics,
                difficulty_target=difficulty,
                question_type=question_type,
                question_form=question_form,
                strategy=VariantStrategy.TOPIC_GENERATION,
                enable_evolution=True,
                max_improvement_rounds=max_improve,
            )
            console.print("[bold cyan]── 进化模式生成 ──[/]")
            result = await agent.run(QGenInput(request=request))
            if result.generated is None:
                error(f"  ✗ 未生成合格题目: {result.reason}")
                raise typer.Exit(1)
            _print_generated(result.generated)
            if result.trace is not None:
                console.print(f"[dim]进化改进轮数: {result.trace.improve_rounds_used}[/]")
            if save:
                saved = _save_generated_question(storage, result.generated)
                success(f"  ✓ 已保存题目 id={saved.id[:8]}")

        asyncio.run(run_once())
    finally:
        storage.close()


@generate_app.command("durable")
def generate_durable(
    topic: str = typer.Option(..., "-t", "--topic", help="专题标签（逗号分隔）"),
    subject: str = typer.Option("", "-s", "--subject"),
    difficulty: int = typer.Option(5, "-d", "--difficulty", min=1, max=10),
    question_type: str = typer.Option("", "--type"),
    question_form: str = typer.Option("", "--form"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    evolve: bool = typer.Option(False, "--evolve/--no-evolve", help="启用进化候选池"),
    deep_solve: bool = typer.Option(False, "--deep-solve/--standard-solve"),
    max_improve: int = typer.Option(2, "--max-improve", min=0),
    run_id: str | None = typer.Option(None, "--run-id", help="可选稳定任务 ID"),
    runtime_db: Path = typer.Option(
        _DEFAULT_QGEN_RUNTIME_DB,
        "--runtime-db",
        help="长任务状态库（默认 data/qgen_runtime.db）",
    ),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="知识库 SQLite"),
    save: bool = typer.Option(True, "--save/--no-save"),
    max_steps: int = typer.Option(200, "--max-steps", min=1),
    max_llm_calls: int = typer.Option(64, "--max-llm-calls", min=1),
    max_tokens: int = typer.Option(500_000, "--max-tokens", min=1),
    max_wall_seconds: float = typer.Option(1800.0, "--max-wall-seconds", min=1.0),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(None, "--llm-config"),
) -> None:
    """创建可从 graph 节点边界恢复的 QGen 长时任务。

    默认状态库：data/qgen_runtime.db。
    """
    import uuid

    from sciforge.agents import QGenAgent, QGenInput
    from sciforge.agents.qgen.durable_harness import DurableQGenHarness
    from sciforge.agents.runtime import BudgetExceededError, RunBudget, SQLiteRunStore
    from sciforge.config.settings import AppConfig

    discipline_enum = _parse_discipline(discipline)
    question_type, question_form = _resolve_question_contract(
        discipline_enum, question_type, question_form
    )
    topics = [value.strip() for value in topic.split(",") if value.strip()]
    if not topics:
        error("至少提供一个 topic")
        raise typer.Exit(1)
    llm_config = _resolve_llm_config(llm_config)
    llm = _build_llm_client("generate", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)
    effective_run_id = run_id or str(uuid.uuid4())
    try:
        agent = QGenAgent.from_config(
            llm=llm,
            storage=storage,
            discipline=discipline_enum,
            config=AppConfig(),
            enable_evolution=evolve,
            max_improvement_rounds=max_improve,
            population_selection_enabled=evolve,
            deep_solve_enabled=deep_solve,
        )
        request = GenerationRequest(
            discipline=discipline_enum,
            subject=subject,
            topics=topics,
            difficulty_target=difficulty,
            question_type=question_type,
            question_form=question_form,
            strategy=VariantStrategy.TOPIC_GENERATION,
            enable_evolution=evolve,
            max_improvement_rounds=max_improve,
        )
        store = SQLiteRunStore(runtime_db)
        harness = DurableQGenHarness(agent, store)

        async def run_once() -> None:
            try:
                _, output = await harness.run(
                    QGenInput(request=request),
                    run_id=effective_run_id,
                    budget=RunBudget(
                        max_steps=max_steps,
                        max_llm_calls=max_llm_calls,
                        max_tokens=max_tokens,
                        max_wall_time_seconds=max_wall_seconds,
                    ),
                    metadata={
                        "source": "cli",
                        "evolution": evolve,
                        "deep_solve": deep_solve,
                    },
                )
            except BudgetExceededError as exc:
                error(f"任务因预算暂停 ({exc.dimension})；run_id={effective_run_id}")
                raise typer.Exit(2) from exc
            console.print(f"[bold cyan]run_id:[/] {effective_run_id}")
            if output.generated is None:
                error(output.reason or "未生成合格题目")
                return
            _print_generated(output.generated)
            if save:
                saved = _save_generated_question(storage, output.generated)
                success(f"已保存题目 id={saved.id[:8]}")

        asyncio.run(run_once())
    finally:
        storage.close()


@generate_app.command("resume")
def resume_durable(
    run_id: str = typer.Argument(..., help="要恢复的持久化任务 ID"),
    runtime_db: Path = typer.Option(
        _DEFAULT_QGEN_RUNTIME_DB,
        "--runtime-db",
        help="长任务状态库（默认 data/qgen_runtime.db）",
    ),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
    save: bool = typer.Option(True, "--save/--no-save"),
    max_steps: int | None = typer.Option(None, "--max-steps", min=1),
    max_llm_calls: int | None = typer.Option(None, "--max-llm-calls", min=1),
    max_tokens: int | None = typer.Option(None, "--max-tokens", min=1),
    max_wall_seconds: float | None = typer.Option(None, "--max-wall-seconds", min=1.0),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(None, "--llm-config"),
) -> None:
    """从最后一个已提交节点恢复 QGen 任务，可同时提高硬预算。

    默认状态库：data/qgen_runtime.db。
    """
    from sciforge.agents import QGenAgent, QGenInput
    from sciforge.agents.qgen.durable_harness import DurableQGenHarness
    from sciforge.agents.runtime import (
        BudgetExceededError,
        RunBudget,
        RunNotFoundError,
        SQLiteRunStore,
    )
    from sciforge.config.settings import AppConfig

    store = SQLiteRunStore(runtime_db)
    try:
        record = store.require_run(run_id)
    except RunNotFoundError as exc:
        error(f"任务不存在: {run_id}")
        raise typer.Exit(1) from exc
    if record.run_kind != "qgen":
        error(f"任务 {run_id} 不是 QGen graph run（kind={record.run_kind}）")
        raise typer.Exit(1)
    request = GenerationRequest.model_validate(record.input_payload)
    if any(value is not None for value in (max_steps, max_llm_calls, max_tokens, max_wall_seconds)):
        store.update_budget(
            run_id,
            RunBudget(
                max_steps=max_steps if max_steps is not None else record.budget.max_steps,
                max_llm_calls=(
                    max_llm_calls if max_llm_calls is not None else record.budget.max_llm_calls
                ),
                max_tokens=max_tokens if max_tokens is not None else record.budget.max_tokens,
                max_wall_time_seconds=(
                    max_wall_seconds
                    if max_wall_seconds is not None
                    else record.budget.max_wall_time_seconds
                ),
            ),
        )
    llm_config = _resolve_llm_config(llm_config)
    llm = _build_llm_client("generate", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)
    evolution = bool(record.metadata.get("evolution", request.enable_evolution))
    deep_solve = bool(record.metadata.get("deep_solve", False))
    try:
        agent = QGenAgent.from_config(
            llm=llm,
            storage=storage,
            discipline=request.discipline,
            config=AppConfig(),
            enable_evolution=evolution,
            max_improvement_rounds=request.max_improvement_rounds,
            population_selection_enabled=evolution,
            deep_solve_enabled=deep_solve,
        )
        harness = DurableQGenHarness(agent, store)

        async def run_once() -> None:
            try:
                _, output = await harness.run(
                    QGenInput(request=request),
                    run_id=run_id,
                    resume=True,
                )
            except BudgetExceededError as exc:
                error(f"任务仍受预算限制 ({exc.dimension})；run_id={run_id}")
                raise typer.Exit(2) from exc
            if output.generated is None:
                error(output.reason or "未生成合格题目")
                return
            _print_generated(output.generated)
            if save:
                saved = _save_generated_question(storage, output.generated)
                success(f"已保存题目 id={saved.id[:8]}")

        asyncio.run(run_once())
    finally:
        storage.close()


@generate_app.command("cancel")
def cancel_durable(
    run_id: str = typer.Argument(..., help="要取消的持久化任务 ID"),
    runtime_db: Path = typer.Option(
        _DEFAULT_QGEN_RUNTIME_DB,
        "--runtime-db",
        help="长任务状态库（默认 data/qgen_runtime.db）",
    ),
) -> None:
    """请求取消运行中任务；无人持有的 pending/paused 任务立即终止。

    默认状态库：data/qgen_runtime.db。
    """
    from sciforge.agents.runtime import RunNotFoundError, SQLiteRunStore

    store = SQLiteRunStore(runtime_db)
    try:
        record = store.request_cancel(run_id, reason="cancelled from CLI")
    except RunNotFoundError as exc:
        error(f"任务不存在: {run_id}")
        raise typer.Exit(1) from exc
    success(f"取消请求已记录: run_id={run_id}, status={record.status.value}")


@generate_app.command("solve-deep")
def generate_solve_deep(
    question_id: str = typer.Argument(..., help="题目 ID"),
    discipline: str = typer.Option("math", "--discipline", "-D"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db"),
    save: bool = typer.Option(
        False, "--save/--no-save", help="把解答写回 question.solution_content"
    ),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_config: Path | None = typer.Option(
        None, "--llm-config", help="LLM TOML 配置文件路径（默认 config/sciforge.toml）"
    ),
) -> None:
    """深度求解：多轮推理 + sandbox 工具调用（execute_python 交叉验证中间结果）"""
    from sciforge.agents.qgen.deep_solver import DeepSolveEngine
    from sciforge.agents.qgen.verification_sandbox import build_verification_sandbox
    from sciforge.config.settings import AppConfig

    discipline_enum = _parse_discipline(discipline)
    llm_config = _resolve_llm_config(llm_config)
    llm = _build_llm_client("solve_deep", llm_config, llm_provider, llm_model)
    storage = _open_storage(db)

    try:
        try:
            q = storage.load_question(question_id)
        except FileNotFoundError:
            error(f"题目不存在: {question_id}")
            raise typer.Exit(1) from None

        app_cfg = AppConfig()
        engine = DeepSolveEngine(
            llm,
            discipline_enum,
            sandbox_timeout=app_cfg.sandbox_timeout,
            max_turns=app_cfg.solver_max_turns,
            sandbox=build_verification_sandbox(app_cfg),
            sandbox_trust_policy=app_cfg.sandbox_trust_policy,
        )

        async def run_once() -> None:
            solution = await engine.solve_deep(q)
            if solution is None:
                error("深度求解失败")
                raise typer.Exit(1)
            console.print(f"[bold green]置信度:[/] {solution.confidence}")
            console.print("[bold]解答:[/]")
            console.print(solution.solution_latex or "(空)")
            if save:
                q.solution_content = solution.solution_latex
                storage.save_batch([], [q])
                success(f"已更新 question {question_id[:8]}.solution_content")

        asyncio.run(run_once())
    finally:
        storage.close()


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
