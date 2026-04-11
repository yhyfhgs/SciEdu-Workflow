"""parse 命令组 — Pipeline CLI"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from sciforge.cli.display import console, error, success
from sciforge.core.models import Discipline

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient
    from sciforge.pipeline.orchestrator import PipelineOrchestrator

parse_app = typer.Typer(name="parse", help="数据解析命令")


def _build_llm_clients(
    config: AppConfig,
    *,
    skip_llm: bool,
    backend: str,
    llm_provider: str | None,
    llm_model: str | None,
) -> tuple[LLMClient | None, dict[str, LLMClient]]:
    """构建 per-task LLM 客户端字典"""
    from sciforge.llm.client import LLMClient
    from sciforge.llm.config import LLMConfig

    if skip_llm:
        return None, {}

    cli_overrides: dict[str, str] = {}
    if llm_provider:
        cli_overrides["provider"] = llm_provider
    if llm_model:
        cli_overrides["model"] = llm_model

    task_names = ["extract_and_classify"]
    if backend == "vlm":
        task_names.append("vlm_convert")

    llm_clients: dict[str, LLMClient] = {}
    for task_name in task_names:
        task_cfg = LLMConfig.from_app_config(config, task_name, **cli_overrides)
        llm_clients[task_name] = LLMClient(task_cfg)
    return llm_clients["extract_and_classify"], llm_clients


def _build_orchestrator(
    config: AppConfig,
    *,
    db: Path | None,
    dry_run: bool,
    skip_llm: bool,
    backend: str,
    llm_provider: str | None,
    llm_model: str | None,
    discipline: Discipline = Discipline.MATH,
) -> PipelineOrchestrator:
    """构建 PipelineOrchestrator 实例"""
    from sciforge.pipeline.orchestrator import PipelineOrchestrator

    try:
        llm_client, llm_clients = _build_llm_clients(
            config,
            skip_llm=skip_llm,
            backend=backend,
            llm_provider=llm_provider,
            llm_model=llm_model,
        )
    except Exception as exc:
        error(f"LLM 客户端初始化失败: {exc}")
        error("使用 --skip-llm 跳过 LLM 调用")
        raise typer.Exit(1) from exc

    return PipelineOrchestrator(
        config=config,
        llm_client=llm_client,
        storage=None,
        db_path=None if dry_run else db,
        llm_clients=llm_clients if llm_clients else None,
        pdf_backend=backend,
        discipline=discipline,
    )


def _parse_discipline(value: str) -> Discipline:
    """CLI 参数 → Discipline 枚举，校验失败时给友好错误"""
    try:
        return Discipline(value)
    except ValueError as exc:
        allowed = ", ".join(d.value for d in Discipline)
        raise typer.BadParameter(
            f"未知学科 {value!r}；允许值: {allowed}"
        ) from exc


def _load_book_manifest(path: Path) -> list[Path]:
    """解析 book manifest（YAML / JSON），返回按顺序的章节文件列表

    接受三种格式：
    1. 列表顶层：`["a.md", "b.md"]`
    2. 对象含 `files:` 键：`{files: ["a.md", "b.md"], name: "..."}`
    3. 对象含 `chapters:` 键：`{chapters: [{path: "a.md"}, ...]}`

    相对路径按 manifest 所在目录解析。
    """
    import json

    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        data = yaml.safe_load(text)
    except ImportError:
        data = json.loads(text)

    if isinstance(data, dict):
        files_raw = data.get("files") or data.get("chapters") or []
    elif isinstance(data, list):
        files_raw = data
    else:
        raise typer.BadParameter(f"manifest 格式不识别：{path}")

    if not isinstance(files_raw, list):
        raise typer.BadParameter(f"manifest 中 files/chapters 必须是列表：{path}")

    base = path.parent.resolve()
    out: list[Path] = []
    for entry in files_raw:
        if isinstance(entry, dict):
            raw = entry.get("path") or entry.get("file") or ""
        else:
            raw = str(entry)
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = (base / p).resolve()
        out.append(p)
    return out


@parse_app.command("run")
def parse_run(
    path: Path = typer.Argument(..., help="文件或目录路径", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="SQLite 数据库路径"),
    backend: str = typer.Option("vlm", "--backend", "-b", help="文档识别后端 (vlm/pymupdf)"),
    pages_per_batch: int = typer.Option(5, "--pages-per-batch", help="Phase A 每批页数"),
    extract_window_lines: int = typer.Option(
        200, "--extract-window-lines", help="Task1 滑动窗口行数"
    ),
    max_retries: int = typer.Option(3, "--max-retries", help="LLM/VLM 调用重试次数"),
    max_files: int = typer.Option(
        4, "--max-files", "-j", help="目录模式下最大并发 logical_document 数"
    ),
    skip_llm: bool = typer.Option(False, "--skip-llm", help="跳过 LLM 调用（仅做识别）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅处理，不写入 DB"),
    restart: bool = typer.Option(False, "--restart", help="忽略 checkpoint，从头开始"),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM 提供者"),
    llm_model: str | None = typer.Option(None, "--llm-model", help="LLM 模型"),
    llm_config_file: Path | None = typer.Option(
        Path("config/sciforge.toml"), "--llm-config", help="LLM TOML 配置文件路径"
    ),
    discipline: str = typer.Option(
        "math",
        "--discipline",
        "-D",
        help="文档学科 (math/physics/chemistry/biology/other)",
    ),
) -> None:
    """运行 Pipeline 处理文件"""
    import asyncio

    from sciforge.config.settings import AppConfig

    discipline_enum = _parse_discipline(discipline)

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

    orchestrator = _build_orchestrator(
        config,
        db=db,
        dry_run=dry_run,
        skip_llm=skip_llm,
        backend=backend,
        llm_provider=llm_provider,
        llm_model=llm_model,
        discipline=discipline_enum,
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
            results = await orchestrator.process_directory(path, max_concurrent_files=max_files)
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


@parse_app.command("run-book")
def parse_run_book(
    files: list[Path] = typer.Argument(None, help="按顺序的章节文件（md/tex），作为一本书处理"),
    manifest: Path | None = typer.Option(None, "--manifest", "-m", help="YAML/JSON 清单文件"),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="SQLite 数据库路径"),
    extract_window_lines: int = typer.Option(
        200, "--extract-window-lines", help="Task1 滑动窗口行数"
    ),
    max_retries: int = typer.Option(3, "--max-retries", help="LLM 调用重试次数"),
    skip_llm: bool = typer.Option(False, "--skip-llm", help="跳过 LLM 调用（仅做识别）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅处理，不写入 DB"),
    restart: bool = typer.Option(False, "--restart", help="忽略 checkpoint，从头开始"),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM 提供者"),
    llm_model: str | None = typer.Option(None, "--llm-model", help="LLM 模型"),
    llm_config_file: Path | None = typer.Option(
        Path("config/sciforge.toml"), "--llm-config", help="LLM TOML 配置文件路径"
    ),
    discipline: str = typer.Option(
        "math",
        "--discipline",
        "-D",
        help="文档学科 (math/physics/chemistry/biology/other)",
    ),
) -> None:
    """把多个 md/tex 文件作为一本书处理，共享 order_index、tag_tree 与断点状态

    三种输入方式：
    1. 位置参数列表：`run-book a.md b.md c.md`
    2. 清单文件：`run-book --manifest book.yaml`
    3. 两者合并（先 manifest 再位置参数）
    """
    import asyncio

    from sciforge.config.settings import AppConfig

    discipline_enum = _parse_discipline(discipline)

    # 合并文件列表
    book_files: list[Path] = []
    if manifest is not None:
        if not manifest.exists():
            error(f"manifest 不存在：{manifest}")
            raise typer.Exit(1)
        book_files.extend(_load_book_manifest(manifest))
    if files:
        book_files.extend(files)

    if not book_files:
        error("必须通过位置参数或 --manifest 提供文件列表")
        raise typer.Exit(1)

    # 校验每个文件
    allowed_suffixes = {".md", ".markdown", ".tex"}
    for f in book_files:
        if f.suffix.lower() not in allowed_suffixes:
            error(f"run-book 仅支持 md/tex 文件：{f}")
            raise typer.Exit(1)
        if not f.exists():
            error(f"文件不存在：{f}")
            raise typer.Exit(1)

    console.print(f"[bold cyan]run-book[/]: {len(book_files)} 个章节作为单个 logical_document")
    for i, f in enumerate(book_files, 1):
        console.print(f"  {i}. {f.name}")

    if llm_config_file and not llm_config_file.exists():
        llm_config_file = None

    config = AppConfig(
        data_dir=output,
        llm_config_file=llm_config_file,
        extract_window_lines=extract_window_lines,
        max_extract_retries=max_retries,
        vlm_max_retries=max_retries,
    )

    orchestrator = _build_orchestrator(
        config,
        db=db,
        dry_run=dry_run,
        skip_llm=skip_llm,
        backend="vlm",  # run-book 仅处理 md/tex，backend 未使用 VLM
        llm_provider=llm_provider,
        llm_model=llm_model,
        discipline=discipline_enum,
    )

    if restart:
        orchestrator.purge_checkpoint(book_files)

    async def run() -> None:
        result = await orchestrator.process_file(book_files)
        _display_result(result)

    asyncio.run(run())


@parse_app.command("audit")
def parse_audit(
    raw_data_dir: Path = typer.Argument(Path("Raw Data"), help="Raw Data 根目录"),
    checkpoint_dir: Path = typer.Option(
        Path("data/checkpoint"), "--checkpoint-dir", help="Checkpoint 根目录"
    ),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="SQLite 数据库路径"),
    output: Path = typer.Option(
        Path("data/pipeline_progress_report.md"),
        "--output",
        "-o",
        help="报告落盘路径",
    ),
    pages_per_batch: int = typer.Option(5, "--pages-per-batch", help="预估 PDF 批次数的每批页数"),
) -> None:
    """盘点 Raw Data 下所有 logical_document 的 pipeline 处理进度"""
    from sciforge.pipeline.audit import (
        audit_directory,
        format_audit_report,
        print_audit_table,
    )

    if not raw_data_dir.exists():
        error(f"Raw Data 目录不存在：{raw_data_dir}")
        raise typer.Exit(1)

    entries = audit_directory(
        raw_data_dir=raw_data_dir,
        checkpoint_dir=checkpoint_dir,
        db_path=db,
        pages_per_batch=pages_per_batch,
    )

    print_audit_table(entries, console)

    report = format_audit_report(entries, raw_data_dir, checkpoint_dir, db)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    success(f"Report written to {output}")


@parse_app.command("migrate-legacy")
def parse_migrate_legacy(
    raw_data_dir: Path = typer.Argument(Path("Raw Data"), help="Raw Data 根目录"),
    checkpoint_dir: Path = typer.Option(
        Path("data/checkpoint"), "--checkpoint-dir", help="Checkpoint 根目录"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅展示将要做的事，不落盘"),
    delete_legacy: bool = typer.Option(False, "--delete-legacy", help="迁移成功后删除旧 vlm/ 目录"),
    force: bool = typer.Option(
        False, "--force", help="已迁移或有 Task1 进度的 checkpoint 也强制重跑"
    ),
) -> None:
    """把 legacy checkpoint (<hash>/vlm/page_*.md) 迁移到新架构

    旧格式：`<hash>/vlm/page_NNN-NNN.md`（0-based 页码）
    新格式：`<hash>/recognize/file_000_<stem>/page_NNN-NNN.md`（1-based 页码）

    迁移同时重建 state.json，设置 `task0_completed=True`；Task1 字段清零，
    需要用户重新跑 `parse run` 触发直通执行条目提取。
    """
    from rich.table import Table

    from sciforge.pipeline.migrate import migrate_legacy_checkpoint

    if not raw_data_dir.exists():
        error(f"Raw Data 目录不存在：{raw_data_dir}")
        raise typer.Exit(1)
    if not checkpoint_dir.exists():
        error(f"Checkpoint 目录不存在：{checkpoint_dir}")
        raise typer.Exit(1)

    results = migrate_legacy_checkpoint(
        checkpoint_dir=checkpoint_dir,
        raw_data_dir=raw_data_dir,
        dry_run=dry_run,
        delete_legacy=delete_legacy,
        force=force,
    )

    table = Table(title="Legacy Migration Results", expand=True)
    table.add_column("Hash", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Matched File", ratio=2)
    table.add_column("Base", justify="right", no_wrap=True)
    table.add_column("Batches", justify="right", no_wrap=True)
    table.add_column("Note", ratio=2)

    status_color = {
        "migrated": "green",
        "would_migrate": "yellow",
        "orphan": "red",
        "skipped": "dim",
        "error": "red",
    }

    for r in results:
        color = status_color.get(r.status, "white")
        matched_name = Path(r.matched_file).name if r.matched_file else "—"
        base_label = "0-based" if r.base_offset == 1 else "1-based"
        table.add_row(
            r.file_hash[:10],
            f"[{color}]{r.status}[/]",
            matched_name,
            base_label if r.matched_file else "—",
            str(r.batches_migrated),
            r.error or "",
        )
    console.print(table)

    migrated = sum(1 for r in results if r.status == "migrated")
    would = sum(1 for r in results if r.status == "would_migrate")
    orphans = sum(1 for r in results if r.status == "orphan")
    errors_count = sum(1 for r in results if r.status == "error")

    if dry_run:
        console.print(
            f"[yellow]Dry-run[/]: {would} 个 checkpoint 将被迁移，"
            f"{orphans} 个 orphan，{errors_count} 个 error"
        )
    else:
        success(
            f"迁移完成: {migrated}/{len(results)} checkpoints migrated "
            f"({orphans} orphans, {errors_count} errors)"
        )


def _display_result(result: object) -> None:
    """显示单个文件的处理结果"""
    from rich.table import Table

    from sciforge.pipeline.orchestrator import PipelineResult

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

    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

    backend = SQLiteStorageBackend(db)
    stats = backend.stats()
    backend.close()

    table = Table(title=f"数据库统计: {db.name}")
    table.add_column("类型", style="cyan")
    table.add_column("数量", style="green")
    for key, value in stats.items():
        table.add_row(key, str(value))
    console.print(table)
