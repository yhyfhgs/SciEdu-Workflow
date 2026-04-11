"""parse 命令组 — Pipeline CLI"""

from __future__ import annotations

from pathlib import Path

import typer

from mathforge.cli.display import console, error, success

parse_app = typer.Typer(name="parse", help="数据解析命令")


@parse_app.command("run")
def parse_run(
    path: Path = typer.Argument(..., help="文件或目录路径", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    db: Path = typer.Option(
        Path("data/mathforge.db"), "--db", help="SQLite 数据库路径"
    ),
    backend: str = typer.Option(
        "vlm", "--backend", "-b", help="文档识别后端 (vlm/pymupdf)"
    ),
    pages_per_batch: int = typer.Option(
        5, "--pages-per-batch", help="Phase A 每批页数"
    ),
    extract_window_lines: int = typer.Option(
        200, "--extract-window-lines", help="Task1 滑动窗口行数"
    ),
    max_retries: int = typer.Option(
        3, "--max-retries", help="LLM/VLM 调用重试次数"
    ),
    max_files: int = typer.Option(
        4, "--max-files", "-j", help="目录模式下最大并发 logical_document 数"
    ),
    skip_llm: bool = typer.Option(
        False, "--skip-llm", help="跳过 LLM 调用（仅做识别）"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅处理，不写入 DB"),
    restart: bool = typer.Option(
        False, "--restart", help="忽略 checkpoint，从头开始"
    ),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM 提供者"),
    llm_model: str | None = typer.Option(None, "--llm-model", help="LLM 模型"),
    llm_config_file: Path | None = typer.Option(
        Path("config/mathforge.toml"), "--llm-config", help="LLM TOML 配置文件路径"
    ),
) -> None:
    """运行 Pipeline 处理文件"""
    import asyncio

    from mathforge.config.settings import AppConfig

    if llm_config_file and not llm_config_file.exists():
        llm_config_file = None

    config = AppConfig(
        data_dir=output,
        llm_config_file=llm_config_file,
        pages_per_batch=pages_per_batch,
        extract_window_lines=extract_window_lines,
        max_extract_retries=max_retries,
        vlm_max_retries=max_retries,
        max_concurrent_files=max_files,
    )

    # 构建 LLM 客户端
    from mathforge.llm.client import LLMClient
    from mathforge.llm.config import LLMConfig

    llm_client: LLMClient | None = None
    llm_clients: dict[str, LLMClient] = {}
    if not skip_llm:
        try:
            cli_overrides: dict[str, str] = {}
            if llm_provider:
                cli_overrides["provider"] = llm_provider
            if llm_model:
                cli_overrides["model"] = llm_model

            task_names = ["extract_and_classify"]
            if backend == "vlm":
                task_names.append("vlm_convert")
            for task_name in task_names:
                task_cfg = LLMConfig.from_app_config(config, task_name, **cli_overrides)
                llm_clients[task_name] = LLMClient(task_cfg)

            llm_client = llm_clients["extract_and_classify"]
        except Exception as exc:
            error(f"LLM 客户端初始化失败: {exc}")
            error("使用 --skip-llm 跳过 LLM 调用")
            raise typer.Exit(1) from exc

    # 构建 Pipeline：dry-run 下 storage=None, db_path=None；否则把 db_path 交给
    # orchestrator，由 process_file 内部按 logical_document 独立构造 backend
    from mathforge.pipeline.orchestrator import PipelineOrchestrator

    orchestrator = PipelineOrchestrator(
        config=config,
        llm_client=llm_client,
        storage=None,
        db_path=None if dry_run else db,
        llm_clients=llm_clients if llm_clients else None,
        pdf_backend=backend,
    )

    if restart:
        if path.is_file():
            orchestrator.purge_checkpoint(path)
        else:
            orchestrator.purge_directory_checkpoints(path)

    async def run() -> None:
        if path.is_file():
            result = await orchestrator.process_file(path)
            _display_result(result)
        else:
            results = await orchestrator.process_directory(
                path, max_concurrent_files=max_files
            )
            total_items = sum(r.items_saved for r in results)
            total_errors = sum(len(r.errors) for r in results)
            total_failed_blocks = sum(len(r.failed_blocks) for r in results)
            total_time = sum(r.elapsed_sec for r in results)
            console.print()
            success(
                f"Pipeline 完成: {len(results)} 文件, "
                f"{total_items} 条目入库, "
                f"{total_failed_blocks} 失败 block, "
                f"{total_errors} 错误, "
                f"{total_time:.1f}s"
            )

    asyncio.run(run())


def _display_result(result: object) -> None:
    """显示单个文件的处理结果"""
    from rich.table import Table

    from mathforge.pipeline.orchestrator import PipelineResult

    assert isinstance(result, PipelineResult)
    if result.errors:
        for e in result.errors:
            error(f"  错误: {e}")

    table = Table(title=f"Pipeline 结果: {Path(result.file_path).name}")
    table.add_column("指标", style="cyan")
    table.add_column("值", style="green")
    table.add_row("文件 hash", result.file_hash)
    table.add_row("识别页数 (max)", str(result.pages_recognized))
    table.add_row("推入 mdblock 数", str(result.blocks_pushed))
    table.add_row("入库条目数", str(result.items_saved))
    table.add_row("失败 batch", str(len(result.failed_batches)))
    table.add_row("失败 block", str(len(result.failed_blocks)))
    table.add_row("总耗时", f"{result.elapsed_sec:.2f}s")
    console.print(table)


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
