"""CLI generate 命令组"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from mathforge.cli.display import (
    display_generation_result,
    display_solution,
    error,
    success,
)

generate_app = typer.Typer(name="generate", help="AI 题目生成")
console = Console()


def _resolve_strategy(strategy: str) -> str:
    """将简写 (A1, B2) 映射为完整策略名"""
    short_map = {
        "A1": "A1_parameter_shift",
        "A2": "A2_method_shift",
        "A3": "A3_type_shift",
        "A4": "A4_escalate",
        "A5": "A5_simplify",
        "A6": "A6_reverse",
        "B1": "B1_topic_generation",
        "B2": "B2_cross_topic",
    }
    return short_map.get(strategy.upper(), strategy)


def _build_engine(
    data_dir: Path,
    llm_provider: str | None = None,
    llm_model: str | None = None,
    llm_base_url: str | None = None,
    llm_temperature: float | None = None,
    llm_config: Path | None = None,
):
    """构建完整的生成引擎"""
    from mathforge.config.settings import AppConfig
    from mathforge.core.annotator import DifficultyAnnotator
    from mathforge.core.engine import GenerationEngine
    from mathforge.core.generator import QuestionGenerator
    from mathforge.core.retriever import ContextRetriever
    from mathforge.core.solver import SolveEngine
    from mathforge.core.verifier import VerificationPipeline
    from mathforge.llm import LLMClient, LLMConfig
    from mathforge.storage.file_backend import FileStorageBackend

    app_config = AppConfig(llm_config_file=llm_config) if llm_config else AppConfig()
    storage = FileStorageBackend(data_dir)

    # CLI 覆写参数
    overrides = {}
    if llm_provider:
        overrides["provider"] = llm_provider
    if llm_model:
        overrides["model"] = llm_model
    if llm_base_url:
        overrides["base_url"] = llm_base_url
    if llm_temperature is not None:
        overrides["temperature"] = llm_temperature

    # 各阶段使用不同的任务预设
    gen_llm = LLMClient(LLMConfig.from_app_config(app_config, "generate", **overrides))
    solve_llm = LLMClient(LLMConfig.from_app_config(app_config, "solve", **overrides))
    review_llm = LLMClient(LLMConfig.from_app_config(app_config, "review", **overrides))
    tag_llm = LLMClient(LLMConfig.from_app_config(app_config, "tag", **overrides))

    # 深度求解 LLM（多轮 + tool calling）
    deep_solve_llm = LLMClient(LLMConfig.from_app_config(app_config, "solve_deep", **overrides))

    from mathforge.core.sandbox import CodeSandbox

    sandbox = CodeSandbox(timeout_seconds=app_config.sandbox_timeout)

    retriever = ContextRetriever(storage)
    generator = QuestionGenerator(gen_llm)
    solver = SolveEngine(
        llm=solve_llm,
        deep_llm=deep_solve_llm,
        sandbox=sandbox,
        max_turns=app_config.solver_max_turns,
    )
    verifier = VerificationPipeline(
        llm=review_llm,
        sandbox=sandbox,
        enable_numerical=app_config.enable_numerical_verification,
    )
    annotator = DifficultyAnnotator(tag_llm)

    engine = GenerationEngine(
        retriever=retriever,
        generator=generator,
        solver=solver,
        verifier=verifier,
        annotator=annotator,
        storage=storage,
        max_rounds=app_config.generation_max_rounds,
        candidates_per_round=app_config.generation_candidates_per_round,
    )

    return engine, storage, app_config


def _output_result(result, fmt: str) -> None:
    """按格式输出生成结果"""
    if fmt == "json":
        console.print_json(result.model_dump_json(indent=2))
    elif fmt == "latex":
        console.print(result.question.content_latex)
        if result.solution:
            console.print("\n% --- Solution ---")
            console.print(result.solution.solution_latex)
    else:
        display_generation_result(result)


@generate_app.command("free")
def generate_free(
    subject: str = typer.Option(..., "-s", "--subject", help="科目 (数学分析/高等代数/群论)"),
    topic: str = typer.Option(..., "-t", "--topic", help="专题（逗号分隔）"),
    difficulty: int = typer.Option(5, "-d", "--difficulty", help="目标难度 1-10"),
    question_type: str = typer.Option("proof", "--type", help="题型"),
    count: int = typer.Option(1, "-n", "--count", help="生成数量"),
    output_format: str = typer.Option("rich", "--format", "-f", help="输出格式: rich/json/latex"),
    save: bool = typer.Option(True, "--save/--no-save", help="是否保存到题库"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir", help="数据目录"),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM 提供者"),
    llm_model: str | None = typer.Option(None, "--llm-model", help="模型名称"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url", help="自定义 API URL"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature", help="温度"),
    llm_config: Path | None = typer.Option(None, "--llm-config", help="LLM TOML 配置文件路径"),
) -> None:
    """自由模式生成新数学题"""
    from mathforge.core.models import (
        GenerationRequest,
        QuestionType,
        Subject,
        VariantStrategy,
    )

    engine, storage, app_config = _build_engine(
        data_dir, llm_provider, llm_model, llm_base_url, llm_temperature, llm_config
    )

    topics = [t.strip() for t in topic.split(",")]
    request = GenerationRequest(
        subject=Subject(subject),
        topics=topics,
        difficulty_target=difficulty,
        question_type=QuestionType(question_type),
        strategy=VariantStrategy.TOPIC_GENERATION,
    )

    if count == 1:
        with console.status("[bold cyan]生成中...[/]"):
            result = engine.generate_sync(request)
        if result:
            _output_result(result, output_format)
            if save:
                storage.save_question(result.question, result.solution)
                success(f"已保存: {result.question.id}")
        else:
            error("生成失败: 多轮尝试后仍未通过验证")
    else:
        with console.status(f"[bold cyan]批量生成 {count} 道题...[/]"):
            results = engine.generate_batch(request, count)
        for i, result in enumerate(results, 1):
            console.rule(f"[bold]第 {i}/{len(results)} 道[/bold]")
            _output_result(result, output_format)
            if save:
                storage.save_question(result.question, result.solution)
        success(f"批量生成完成: {len(results)}/{count} 成功")


@generate_app.command("variant")
def generate_variant(
    source_id: str = typer.Argument(..., help="源题 ID"),
    strategy: str = typer.Option("A1", "--strategy", help="变式策略 (A1-A6, B1-B2)"),
    difficulty: int | None = typer.Option(None, "-d", "--difficulty", help="目标难度"),
    output_format: str = typer.Option("rich", "--format", "-f", help="输出格式"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config", help="LLM TOML 配置文件路径"),
) -> None:
    """基于已有题目生成变式"""
    from mathforge.core.models import GenerationRequest, VariantStrategy

    engine, storage, app_config = _build_engine(
        data_dir, llm_provider, llm_model, llm_base_url, llm_temperature, llm_config
    )

    try:
        source_q = storage.load_question(source_id)
    except FileNotFoundError:
        error(f"未找到题目: {source_id}")
        raise typer.Exit(1)

    target_d = difficulty or source_q.difficulty
    strategy_name = _resolve_strategy(strategy)

    request = GenerationRequest(
        subject=source_q.subject,
        topics=source_q.tags or ["通用"],
        difficulty_target=target_d,
        question_type=source_q.question_type,
        strategy=VariantStrategy(strategy_name),
        source_question=source_q,
    )

    with console.status("[bold cyan]变式生成中...[/]"):
        result = engine.generate_sync(request)

    if result:
        _output_result(result, output_format)
        if save:
            storage.save_question(result.question, result.solution)
            success(f"已保存: {result.question.id}")
    else:
        error("变式生成失败: 多轮尝试后仍未通过验证")


@generate_app.command("batch")
def generate_batch(
    subject: str = typer.Option(..., "-s", "--subject"),
    topic: str = typer.Option(..., "-t", "--topic"),
    difficulty: int = typer.Option(5, "-d", "--difficulty"),
    count: int = typer.Option(5, "-n", "--count", help="生成数量"),
    question_type: str = typer.Option("proof", "--type"),
    output_format: str = typer.Option("rich", "--format", "-f"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config", help="LLM TOML 配置文件路径"),
) -> None:
    """批量生成题目"""
    from mathforge.core.models import (
        GenerationRequest,
        QuestionType,
        Subject,
        VariantStrategy,
    )

    engine, storage, app_config = _build_engine(
        data_dir, llm_provider, llm_model, llm_base_url, llm_temperature, llm_config
    )

    topics = [t.strip() for t in topic.split(",")]
    request = GenerationRequest(
        subject=Subject(subject),
        topics=topics,
        difficulty_target=difficulty,
        question_type=QuestionType(question_type),
        strategy=VariantStrategy.TOPIC_GENERATION,
    )

    with console.status(f"[bold cyan]批量生成 {count} 道题...[/]"):
        results = engine.generate_batch(request, count)

    for i, result in enumerate(results, 1):
        console.rule(f"[bold]第 {i}/{len(results)} 道[/bold]")
        _output_result(result, output_format)
        if save:
            storage.save_question(result.question, result.solution)

    success(f"批量生成完成: {len(results)}/{count} 成功")


@generate_app.command("solve")
def generate_solve(
    question_id: str = typer.Argument(..., help="题目 ID"),
    output_format: str = typer.Option("rich", "--format", "-f", help="输出格式"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config", help="LLM TOML 配置文件路径"),
) -> None:
    """为指定题目生成详细解答"""
    from mathforge.config.settings import AppConfig
    from mathforge.core.models import ContextBundle
    from mathforge.core.solver import SolveEngine
    from mathforge.llm import LLMClient, LLMConfig
    from mathforge.storage.file_backend import FileStorageBackend

    app_config = AppConfig(llm_config_file=llm_config) if llm_config else AppConfig()
    storage = FileStorageBackend(data_dir)

    overrides = {}
    if llm_provider:
        overrides["provider"] = llm_provider
    if llm_model:
        overrides["model"] = llm_model
    if llm_base_url:
        overrides["base_url"] = llm_base_url
    if llm_temperature is not None:
        overrides["temperature"] = llm_temperature

    try:
        question = storage.load_question(question_id)
    except FileNotFoundError:
        error(f"未找到题目: {question_id}")
        raise typer.Exit(1)

    solve_llm = LLMClient(LLMConfig.from_app_config(app_config, "solve", **overrides))
    solver = SolveEngine(solve_llm)
    context = ContextBundle()

    with console.status("[bold cyan]求解中...[/]"):
        solution = solver.solve_sync(question, context)

    if solution:
        if output_format == "json":
            console.print_json(solution.model_dump_json(indent=2))
        elif output_format == "latex":
            console.print(solution.solution_latex)
        else:
            display_solution(question, solution)
        if save:
            storage.save_solution(solution)
            success(f"解答已保存: {question_id}")
    else:
        error("求解失败")


@generate_app.command("evolve")
def generate_evolve(
    subject: str = typer.Option(..., "-s", "--subject", help="科目"),
    topic: str = typer.Option(..., "-t", "--topic", help="专题（逗号分隔）"),
    difficulty: int = typer.Option(5, "-d", "--difficulty", help="目标难度 1-10"),
    question_type: str = typer.Option("proof", "--type", help="题型"),
    max_improve: int = typer.Option(2, "--max-improve", help="最大改进轮数"),
    output_format: str = typer.Option("rich", "--format", "-f", help="输出格式"),
    save: bool = typer.Option(True, "--save/--no-save"),
    data_dir: Path = typer.Option(Path("data"), "--data-dir"),
    llm_provider: str | None = typer.Option(None, "--llm-provider"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
    llm_base_url: str | None = typer.Option(None, "--llm-base-url"),
    llm_temperature: float | None = typer.Option(None, "--llm-temperature"),
    llm_config: Path | None = typer.Option(None, "--llm-config", help="LLM TOML 配置文件路径"),
) -> None:
    """进化模式生成: 迭代改进直到通过验证"""
    from mathforge.core.evolution_engine import EvolutionEngine
    from mathforge.core.models import (
        GenerationRequest,
        QuestionType,
        Subject,
        VariantStrategy,
    )

    engine, storage, app_config = _build_engine(
        data_dir, llm_provider, llm_model, llm_base_url, llm_temperature, llm_config
    )

    topics = [t.strip() for t in topic.split(",")]
    request = GenerationRequest(
        subject=Subject(subject),
        topics=topics,
        difficulty_target=difficulty,
        question_type=QuestionType(question_type),
        strategy=VariantStrategy.TOPIC_GENERATION,
        enable_evolution=True,
        max_improvement_rounds=max_improve,
    )

    evo_engine = EvolutionEngine(
        generation_engine=engine,
        max_improvement_rounds=max_improve,
        checkpoint_dir=app_config.checkpoint_dir / "evolution",
    )

    with console.status("[bold cyan]进化式生成中...[/]"):
        result = evo_engine.evolve_sync(request)

    if result:
        _output_result(result, output_format)
        gen = result.generation_meta.get("generation", 0)
        if gen > 0:
            console.print(f"[dim]经过 {gen} 代改进[/dim]")
        if save:
            storage.save_question(result.question, result.solution)
            success(f"已保存: {result.question.id}")
    else:
        error("进化式生成失败: 所有候选均未通过验证")


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
    llm_config: Path | None = typer.Option(None, "--llm-config", help="LLM TOML 配置文件路径"),
) -> None:
    """深度求解: 多轮推理 + 代码计算辅助"""
    from mathforge.config.settings import AppConfig
    from mathforge.core.models import ContextBundle
    from mathforge.core.sandbox import CodeSandbox
    from mathforge.core.solver import SolveEngine
    from mathforge.llm import LLMClient, LLMConfig
    from mathforge.storage.file_backend import FileStorageBackend

    app_config = AppConfig(llm_config_file=llm_config) if llm_config else AppConfig()
    storage = FileStorageBackend(data_dir)

    overrides = {}
    if llm_provider:
        overrides["provider"] = llm_provider
    if llm_model:
        overrides["model"] = llm_model
    if llm_base_url:
        overrides["base_url"] = llm_base_url
    if llm_temperature is not None:
        overrides["temperature"] = llm_temperature

    try:
        question = storage.load_question(question_id)
    except FileNotFoundError:
        error(f"未找到题目: {question_id}")
        raise typer.Exit(1)

    deep_llm = LLMClient(LLMConfig.from_app_config(app_config, "solve_deep", **overrides))
    sandbox = CodeSandbox(timeout_seconds=app_config.sandbox_timeout)
    solver = SolveEngine(
        deep_llm=deep_llm,
        sandbox=sandbox,
        max_turns=app_config.solver_max_turns,
    )
    context = ContextBundle()

    with console.status("[bold cyan]深度求解中（多轮 + 代码辅助）...[/]"):
        solution = solver.solve_deep_sync(question, context)

    if solution:
        if output_format == "json":
            console.print_json(solution.model_dump_json(indent=2))
        elif output_format == "latex":
            console.print(solution.solution_latex)
        else:
            display_solution(question, solution)
        if solution.tool_calls_made > 0:
            console.print(f"[dim]使用了 {solution.tool_calls_made} 次代码计算辅助[/dim]")
        if save:
            storage.save_solution(solution)
            success(f"解答已保存: {question_id}")
    else:
        error("深度求解失败")
