"""parse 命令组 — Pipeline CLI"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import typer

from sciforge.cli.display import console, error, success
from sciforge.core.models import Discipline

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient
    from sciforge.pipeline.orchestrator import PipelineOrchestrator

parse_app = typer.Typer(name="parse", help="数据解析命令")

# 允许的识别后端（显式枚举校验，避免 silent fallthrough）
_ALLOWED_BACKENDS = ("vlm", "pymupdf")
_DEFAULT_LLM_CONFIG = Path("config/sciforge.toml")


def _validate_backend(backend: str) -> None:
    """校验 --backend 值，非法值立即退出"""
    if backend not in _ALLOWED_BACKENDS:
        allowed = "/".join(_ALLOWED_BACKENDS)
        raise typer.BadParameter(f"--backend 必须是 {allowed}；当前: {backend!r}")


def _resolve_llm_config(p: Path | None) -> Path | None:
    """统一解析 --llm-config：

    - p 不为 None：若路径不存在，立即 fail-fast（用户显式指定的错误路径）
    - p 为 None：fallback 到 `config/sciforge.toml` 若存在，否则 None
    """
    if p is not None:
        if not p.exists():
            error(f"--llm-config 路径不存在: {p}")
            raise typer.Exit(1)
        return p
    return _DEFAULT_LLM_CONFIG if _DEFAULT_LLM_CONFIG.exists() else None


def _resolve_provider_override(llm_provider: str | None) -> dict[str, str]:
    """把 CLI --llm-provider 智能分派为 provider_ref 或 raw provider type

    - 若值匹配已知的 LLMProvider 枚举（anthropic/openai/gemini/openai_compatible/
      anthropic_compatible）→ 当作 raw type（适合 --skip-llm 或无 TOML 裸跑）
    - 否则当作 provider_ref 别名（v2 TOML 路径）
    """
    from sciforge.llm.config import LLMProvider

    if not llm_provider:
        return {}
    known_types = {e.value for e in LLMProvider}
    if llm_provider in known_types:
        return {"provider": llm_provider}
    return {"provider_ref": llm_provider}


def _build_llm_clients(
    config: AppConfig,
    *,
    skip_llm: bool,
    backend: str,
    llm_provider: str | None,
    llm_model: str | None,
) -> tuple[LLMClient | None, dict[str, LLMClient]]:
    """构建 per-task LLM 客户端字典

    并发限流两层：
    - 全局 semaphore（max_concurrent_llm_calls）：跨所有 model 的总在途请求上限
    - per-model semaphore（llm_model_concurrency[model_id]）：按 LLM model id 限流，
      防止 Anthropic Token Plan 等 per-model quota 触发 429
    """
    from sciforge.llm.client import LLMClient
    from sciforge.llm.config import LLMConfig

    if skip_llm:
        return None, {}

    cli_overrides: dict[str, str] = _resolve_provider_override(llm_provider)
    if llm_model:
        cli_overrides["model"] = llm_model

    task_names = ["extract_and_classify", "review", "tag", "annotate_review"]
    if backend == "vlm":
        task_names.append("vlm_convert")

    llm_clients: dict[str, LLMClient] = {}
    request_semaphore = asyncio.Semaphore(max(1, int(config.max_concurrent_llm_calls)))

    # 同 model id 共享同一个 per-model semaphore（跨 task）
    model_semaphores: dict[str, asyncio.Semaphore] = {}

    def _get_model_sem(model_id: str) -> asyncio.Semaphore | None:
        limit = config.llm_model_concurrency.get(model_id)
        if not limit or int(limit) <= 0:
            return None
        if model_id not in model_semaphores:
            model_semaphores[model_id] = asyncio.Semaphore(int(limit))
        return model_semaphores[model_id]

    for task_name in task_names:
        task_cfg = LLMConfig.from_app_config(config, task_name, **cli_overrides)
        client = LLMClient(task_cfg)
        client.set_request_semaphore(request_semaphore)
        msem = _get_model_sem(task_cfg.model)
        if msem is not None:
            client.set_model_semaphore(msem)
        llm_clients[task_name] = client
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
        raise typer.BadParameter(f"未知学科 {value!r}；允许值: {allowed}") from exc


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
    pages_per_batch: int | None = typer.Option(
        None, "--pages-per-batch", help="Task0 每批页数（默认读 TOML，fallback 5）"
    ),
    extract_window_lines: int | None = typer.Option(
        None,
        "--extract-window-lines",
        help="Stage1Extract 初始窗口行数（默认读 TOML，fallback 500）",
    ),
    extract_max_window_lines: int | None = typer.Option(
        None,
        "--extract-max-window-lines",
        help="Stage1Extract 最大窗口行数（默认读 TOML，fallback 3000）",
    ),
    extract_expand_step: int | None = typer.Option(
        None,
        "--extract-expand-step",
        help="Stage1Extract 每次扩窗口的步长（默认读 TOML，fallback 500）",
    ),
    max_retries: int | None = typer.Option(
        None, "--max-retries", help="LLM/VLM 调用重试次数（默认读 TOML，fallback 3）"
    ),
    max_files: int | None = typer.Option(
        None,
        "--max-files",
        "-j",
        help=(
            "目录模式下最大并发 logical_document 数（默认读 TOML，fallback 8）。"
            "建议：≤16GB RAM 用 4-6；32GB 用 8-12；64GB+ 用 16-24"
        ),
    ),
    skip_llm: bool = typer.Option(False, "--skip-llm", help="跳过 LLM 调用（仅做识别）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅处理，不写入 DB"),
    restart: bool = typer.Option(False, "--restart", help="忽略 checkpoint，从头开始"),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM 提供者"),
    llm_model: str | None = typer.Option(None, "--llm-model", help="LLM 模型"),
    llm_config_file: Path | None = typer.Option(
        None,
        "--llm-config",
        help="LLM TOML 配置文件路径（未指定时默认读 config/sciforge.toml）",
    ),
    discipline: str = typer.Option(
        "math",
        "--discipline",
        "-D",
        help="文档学科 (math/physics/chemistry/biology/other)",
    ),
    parallel_extract: bool = typer.Option(
        False,
        "--parallel-extract",
        help=(
            "单文档内并行提取：按 section 边界切段、段间并发（段内 carry-aware 滑窗不切断条目）。"
            "适合**单个超大文件**提速；目录多文件已自带跨文件并行。"
        ),
    ),
    segment_concurrency: int | None = typer.Option(
        None,
        "--segment-concurrency",
        help="--parallel-extract 的段间并发度（默认读 TOML，fallback 4）",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Debug 模式：打印每轮窗口、LLM 回复和条目连续性",
    ),
) -> None:
    """运行 Pipeline 处理文件"""
    from sciforge.config.settings import AppConfig

    _validate_backend(backend)
    discipline_enum = _parse_discipline(discipline)
    llm_config_file = _resolve_llm_config(llm_config_file)

    # CLI 仅覆盖显式传入（非 None）的字段，其余走 TOML → AppConfig 默认
    overrides: dict[str, object] = {}
    if pages_per_batch is not None:
        overrides["pages_per_batch"] = pages_per_batch
    if extract_window_lines is not None:
        overrides["extract_window_lines"] = extract_window_lines
    if extract_max_window_lines is not None:
        overrides["extract_max_window_lines"] = extract_max_window_lines
    if extract_expand_step is not None:
        overrides["extract_window_expand_step"] = extract_expand_step
    if max_retries is not None:
        overrides["max_extract_retries"] = max_retries
        overrides["stage1_extract_max_retries"] = max_retries
        overrides["vlm_max_retries"] = max_retries
    if max_files is not None:
        overrides["max_concurrent_files"] = max_files
    if parallel_extract:
        overrides["intra_doc_parallel_extract"] = True
    if segment_concurrency is not None:
        overrides["intra_doc_segment_concurrency"] = segment_concurrency
    if debug:
        overrides["pipeline_debug"] = True

    config_kwargs: dict[str, Any] = {
        "data_dir": output,
        "llm_config_file": llm_config_file,
        **overrides,
    }
    config = AppConfig(**cast(Any, config_kwargs))

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
            results = await orchestrator.process_directory(
                path, max_concurrent_files=config.max_concurrent_files
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


@parse_app.command("run-book")
def parse_run_book(
    files: list[Path] = typer.Argument(None, help="按顺序的章节文件（md/tex），作为一本书处理"),
    manifest: Path | None = typer.Option(None, "--manifest", "-m", help="YAML/JSON 清单文件"),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="SQLite 数据库路径"),
    extract_window_lines: int | None = typer.Option(
        None,
        "--extract-window-lines",
        help="Stage1Extract 初始窗口行数（默认读 TOML，fallback 500）",
    ),
    extract_max_window_lines: int | None = typer.Option(
        None,
        "--extract-max-window-lines",
        help="Stage1Extract 最大窗口行数（默认读 TOML，fallback 3000）",
    ),
    extract_expand_step: int | None = typer.Option(
        None,
        "--extract-expand-step",
        help="Stage1Extract 每次扩窗口的步长（默认读 TOML，fallback 500）",
    ),
    max_retries: int | None = typer.Option(
        None, "--max-retries", help="LLM 调用重试次数（默认读 TOML，fallback 3）"
    ),
    skip_llm: bool = typer.Option(False, "--skip-llm", help="跳过 LLM 调用（仅做识别）"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅处理，不写入 DB"),
    restart: bool = typer.Option(False, "--restart", help="忽略 checkpoint，从头开始"),
    llm_provider: str | None = typer.Option(None, "--llm-provider", help="LLM 提供者"),
    llm_model: str | None = typer.Option(None, "--llm-model", help="LLM 模型"),
    llm_config_file: Path | None = typer.Option(
        None,
        "--llm-config",
        help="LLM TOML 配置文件路径（未指定时默认读 config/sciforge.toml）",
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
    from sciforge.config.settings import AppConfig

    discipline_enum = _parse_discipline(discipline)
    llm_config_file = _resolve_llm_config(llm_config_file)

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

    overrides: dict[str, object] = {}
    if extract_window_lines is not None:
        overrides["extract_window_lines"] = extract_window_lines
    if extract_max_window_lines is not None:
        overrides["extract_max_window_lines"] = extract_max_window_lines
    if extract_expand_step is not None:
        overrides["extract_window_expand_step"] = extract_expand_step
    if max_retries is not None:
        overrides["max_extract_retries"] = max_retries
        overrides["stage1_extract_max_retries"] = max_retries
        overrides["vlm_max_retries"] = max_retries

    config_kwargs: dict[str, Any] = {
        "data_dir": output,
        "llm_config_file": llm_config_file,
        **overrides,
    }
    config = AppConfig(**cast(Any, config_kwargs))

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


@parse_app.command("audit-tags")
def parse_audit_tags(
    db: Path = typer.Option(Path("data/sciforge.db"), "--db", help="SQLite 数据库路径"),
    max_english_distance: int = typer.Option(
        2, "--max-english-distance", help="english 部分最大编辑距离"
    ),
    max_chinese_distance: int = typer.Option(
        1, "--max-chinese-distance", help="中文部分最大编辑距离"
    ),
) -> None:
    """扫描入库 tag 段，输出疑似同义异名的段对（人工合并参考）。"""
    from rich.console import Console

    from sciforge.pipeline.audit import audit_tag_similarity, print_tag_similarity_report

    if not db.exists():
        error(f"Database file not found: {db}")
        raise typer.Exit(1)
    console = Console()
    pairs = audit_tag_similarity(
        db,
        max_english_distance=max_english_distance,
        max_chinese_distance=max_chinese_distance,
    )
    print_tag_similarity_report(pairs, console)


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


@parse_app.command("validate")
def validate(
    db: Path = typer.Argument(..., help="SQLite 数据库路径", exists=True),
) -> None:
    """入库后只读体检：核对各 phase 的提取效果（#2/#3/#4/#5/#6/#8）。

    跑完 `parse run` 后执行本命令，得到提取总量/类型双维度分布/题答绑定率/
    编号规范化/去重/编号缺口/知识图谱边的结构化报告与逐项判定。不修改数据。
    """
    from rich.table import Table

    from sciforge.pipeline.numbering_audit import audit_numbering
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

    backend = SQLiteStorageBackend(db)
    rep = backend.validation_report()

    # 编号缺口：逐 source_file 跑 numbering_audit 汇总
    gap_groups = 0
    gap_samples: list[dict[str, Any]] = []
    for sf in rep["source_files"]:
        if sf == "(空)":
            continue
        audit = audit_numbering(backend.numbering_rows(sf))
        gap_groups += len(audit["suspected_gaps"])
        gap_samples.extend(audit["suspected_gaps"][:2])
    backend.close()

    kp_t, q_t = rep["kp_total"], rep["q_total"]
    total = kp_t + q_t

    def _dist_str(d: dict[str, int], top: int = 8) -> str:
        items = list(d.items())[:top]
        more = f"  …(+{len(d) - top})" if len(d) > top else ""
        return "  ".join(f"{k}:{v}" for k, v in items) + more if items else "(无)"

    # ── 概览 ──
    overview = Table(title=f"体检报告: {db.name}", show_header=True)
    overview.add_column("维度", style="cyan", no_wrap=True)
    overview.add_column("结果", style="white")
    overview.add_row("条目总数", f"知识点 {kp_t} + 题目 {q_t} = {total}")
    overview.add_row("来源文件数", str(len([s for s in rep["source_files"] if s != "(空)"])))
    overview.add_row("[#5] 知识点类型", _dist_str(rep["kp_by_type"]))
    overview.add_row("[#5] 题目认知类别", _dist_str(rep["q_by_type"]))
    overview.add_row("[#5] 题目形式", _dist_str(rep["q_by_form"]))
    rel_detail = _dist_str(rep["relations_by_type"])
    overview.add_row("[#6] 知识图谱边", f"{rep['relations_total']}  ({rel_detail})")
    console.print(overview)

    # ── 逐项判定 ──
    checks = Table(title="逐项判定", show_header=True)
    checks.add_column("检查项", style="cyan", no_wrap=True)
    checks.add_column("判定", no_wrap=True)
    checks.add_column("明细", style="white")

    def _verdict(ok: bool, warn: bool = False) -> str:
        if ok:
            return "[green]✅ PASS[/green]"
        return "[yellow]⚠ WARN[/yellow]" if warn else "[red]❌ FAIL[/red]"

    # #8 去重（UNIQUE 不变量，硬性）
    checks.add_row(
        "[#8] content_hash 去重",
        _verdict(rep["dup_hash_groups"] == 0),
        f"表内重复组 {rep['dup_hash_groups']}（应为 0）",
    )
    # #4 编号规范化格式
    checks.add_row(
        "[#4] 题号规范化",
        _verdict(rep["dirty_canonical"] == 0, warn=True),
        f"含非[0-9.]字符的 number_canonical: {rep['dirty_canonical']}；"
        f"覆盖 KP {rep['kp_canonical_cov']}/{kp_t} · Q {rep['q_canonical_cov']}/{q_t}",
    )
    # #3 题答绑定
    miss = rep["q_missing_solution"]
    bind_pct = (rep["q_with_solution"] / q_t * 100) if q_t else 100.0
    checks.add_row(
        "[#3] 题答绑定率",
        _verdict(miss == 0, warn=True),
        f"{rep['q_with_solution']}/{q_t} 题有解答 ({bind_pct:.0f}%)；"
        f"缺解答 {miss}（部分习题原文确无解答属正常，需人工抽查）",
    )
    # #2 编号连续性
    checks.add_row(
        "[#2] 编号连续性",
        _verdict(gap_groups == 0, warn=True),
        f"疑似缺号分组 {gap_groups}"
        + (f"；样例 {gap_samples[:2]}" if gap_samples else ""),
    )
    console.print(checks)

    if rep["dup_hash_groups"] == 0 and rep["dirty_canonical"] == 0 and gap_groups == 0:
        success("核心不变量全部通过（去重/编号格式/编号连续性）")
    else:
        console.print(
            "[yellow]存在 WARN 项：编号缺口/缺解答多为原文特性，"
            "请结合 items/persisted/ 镜像与原文人工抽查少量样本确认。[/yellow]"
        )
