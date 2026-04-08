"""parse 命令组 — 统一数据处理 Pipeline CLI"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn

from mathforge.cli.display import console, error, success

parse_app = typer.Typer(name="parse", help="数据解析命令")


@parse_app.command("run")
def parse_run(
    path: Path = typer.Argument(..., help="文件或目录路径", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    skip_llm: bool = typer.Option(False, "--skip-llm", help="跳过 LLM 调用，仅使用确定性处理"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="从 checkpoint 恢复"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅处理，不写入存储"),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM 提供者"),
    llm_model: str | None = typer.Option(None, "--llm-model", help="LLM 模型"),
    llm_config_file: Path | None = typer.Option(None, "--llm-config", help="LLM TOML 配置文件路径"),
    backend: str = typer.Option(
        "auto", "--backend", "-b", help="文档提取后端 (auto/pymupdf/marker/mineru)"
    ),
    db: Path | None = typer.Option(None, "--db", help="SQLite 数据库路径（启用 SQLite 存储）"),
) -> None:
    """运行统一流水线 Pipeline 处理文件"""
    import asyncio

    from rich.progress import BarColumn, MofNCompleteColumn, TimeElapsedColumn

    from mathforge.config.settings import AppConfig

    config = (
        AppConfig(data_dir=output, llm_config_file=llm_config_file)
        if llm_config_file
        else AppConfig(data_dir=output)
    )

    # 构建 LLM 客户端（per-task）
    from mathforge.llm.client import LLMClient
    from mathforge.llm.config import LLMConfig

    llm_client: LLMClient | None = None
    llm_clients: dict[str, LLMClient] = {}
    if not skip_llm:
        try:
            # CLI 全局覆写
            cli_overrides: dict[str, str] = {}
            if llm_provider:
                cli_overrides["provider"] = llm_provider
            if llm_model:
                cli_overrides["model"] = llm_model

            # 为每个 pipeline 任务创建独立 LLM client
            for task_name in ("process_item", "split", "infer_relations"):
                task_cfg = LLMConfig.from_app_config(config, task_name, **cli_overrides)
                llm_clients[task_name] = LLMClient(task_cfg)

            # 默认 client 向后兼容
            llm_client = llm_clients["process_item"]
        except Exception as e:
            error(f"LLM 客户端初始化失败: {e}")
            error("使用 --skip-llm 跳过 LLM 调用")
            raise typer.Exit(1)

    # 构建 Storage
    storage = None
    if not dry_run:
        if db:
            from mathforge.storage.sqlite_backend import SQLiteStorageBackend

            storage = SQLiteStorageBackend(db)
        else:
            from mathforge.storage.file_backend import FileStorageBackend

            storage = FileStorageBackend(output)

    from mathforge.pipeline.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(
        config=config,
        llm_client=llm_client,
        storage=storage,
        llm_clients=llm_clients if llm_clients else None,
        pdf_backend=backend,
    )

    async def run() -> None:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            if path.is_file():
                task = progress.add_task(f"处理 {path.name}...", total=1)
                result = await orchestrator.process_file(path)
                progress.update(task, advance=1)
                _display_result(result)
            else:
                results = await orchestrator.process_directory(path)
                total_kps = sum(len(r.knowledge_points) for r in results)
                total_qs = sum(len(r.questions) for r in results)
                total_errors = sum(len(r.errors) for r in results)
                total_time = sum(r.elapsed_sec for r in results)

                console.print()
                success(
                    f"Pipeline 完成: {len(results)} 文件, "
                    f"{total_kps} 知识点, {total_qs} 题目, "
                    f"{total_errors} 错误, {total_time:.1f}s"
                )

    asyncio.run(run())


def _display_result(result: PipelineResult) -> None:  # type: ignore[name-defined]  # noqa: F821
    """显示单个文件的处理结果"""
    from rich.table import Table

    if result.errors:
        for e in result.errors:
            error(f"  错误: {e}")
    else:
        table = Table(title=f"Pipeline 结果: {Path(result.file_path).name}")
        table.add_column("指标", style="cyan")
        table.add_column("值", style="green")
        table.add_row("Blocks", str(result.blocks_count))
        table.add_row("Items 提取", str(result.items_extracted))
        table.add_row("Items 处理", str(result.items_processed))
        table.add_row("知识点", str(len(result.knowledge_points)))
        table.add_row("题目", str(len(result.questions)))
        table.add_row("拆分耗时", f"{result.split_sec:.2f}s")
        table.add_row("提取+处理耗时", f"{result.extract_sec + result.process_sec:.2f}s")
        table.add_row("关系建立耗时", f"{result.relation_sec:.2f}s")
        table.add_row("总耗时", f"{result.elapsed_sec:.2f}s")
        console.print(table)


@parse_app.command("migrate-db")
def migrate_db(
    source: Path = typer.Argument(..., help="JSON 数据目录", exists=True),
    target: Path = typer.Option(
        Path("data/mathforge.db"), "--target", "-t", help="SQLite 数据库路径"
    ),
) -> None:
    """将 JSON 文件存储迁移到 SQLite"""
    from mathforge.storage.migrate import JsonToSqliteMigrator

    console.print("[bold]迁移 JSON → SQLite[/bold]")
    console.print(f"  源目录: {source}")
    console.print(f"  目标库: {target}")

    migrator = JsonToSqliteMigrator(source, target)
    try:
        stats = migrator.migrate()
        success(
            f"迁移完成: {stats['kp']} 知识点, {stats['questions']} 题目, "
            f"{stats['skipped']} 重复跳过, {stats['errors']} 错误"
        )
    finally:
        migrator.close()


@parse_app.command("db-stats")
def db_stats(
    db: Path = typer.Argument(..., help="SQLite 数据库路径", exists=True),
) -> None:
    """显示 SQLite 数据库统计信息"""
    from rich.table import Table

    from mathforge.storage.sqlite_backend import SQLiteStorageBackend

    backend = SQLiteStorageBackend(db)
    stats = backend.stats()
    backend.close()

    table = Table(title=f"数据库统计: {db.name}")
    table.add_column("类型", style="cyan")
    table.add_column("数量", style="green")
    for key, value in stats.items():
        table.add_row(key, str(value))
    console.print(table)
