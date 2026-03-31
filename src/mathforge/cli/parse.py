"""parse 命令组 — 数据处理 Pipeline CLI"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn

from mathforge.cli.display import console, error, success, summary_table

parse_app = typer.Typer(name="parse", help="数据解析命令")


@parse_app.command("tex")
def parse_tex(
    path: Path = typer.Argument(..., help="TeX 文件或目录", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅解析，不写入存储"),
) -> None:
    """解析 TeX 真题文件"""
    from mathforge.pipeline.question_builder import build_question
    from mathforge.pipeline.tex_parser import parse_directory, parse_file
    from mathforge.storage.file_backend import FileStorageBackend

    if path.is_file():
        all_results = [parse_file(path)]
    else:
        all_results = parse_directory(path)

    if not all_results:
        error("未找到可解析的 TeX 文件")
        raise typer.Exit(1)

    storage = FileStorageBackend(output) if not dry_run else None

    total_problems = 0
    total_exercises = 0
    total_saved = 0
    summary_rows: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("解析 TeX 文件...", total=len(all_results))

        exercise_index = 0
        for meta, problems in all_results:
            progress.update(task, description=f"解析 {Path(meta.source_file).name}")

            file_problems = 0
            file_exercises = 0

            for parsed in problems:
                if parsed.is_exercise:
                    exercise_index += 1
                    file_exercises += 1
                else:
                    file_problems += 1

                question, solution = build_question(parsed, exercise_index)

                if storage:
                    storage.save_question(question, solution)
                    total_saved += 1

            total_problems += file_problems
            total_exercises += file_exercises

            summary_rows.append({
                "文件": Path(meta.source_file).name,
                "类型": meta.exam_type.value,
                "年份": str(meta.year),
                "题目": str(file_problems),
                "练习": str(file_exercises),
            })
            progress.advance(task)

    summary_table(
        "TeX 解析结果",
        summary_rows,
        ["文件", "类型", "年份", "题目", "练习"],
    )

    if dry_run:
        success(f"[Dry Run] 共解析 {total_problems} 道题目, {total_exercises} 道练习")
    else:
        success(f"共保存 {total_saved} 道题目到 {output}")


@parse_app.command("md")
def parse_md(
    path: Path = typer.Argument(..., help="MD 文件或目录", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    source_type: str = typer.Option("auto", "--type", "-t", help="来源类型 (auto/lecture/textbook)"),
    repair: bool = typer.Option(True, "--repair/--no-repair", help="是否执行 OCR 修复"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅解析，不写入存储"),
) -> None:
    """解析 Markdown 讲义/教材文件"""
    from mathforge.pipeline.kp_builder import build_knowledge_points
    from mathforge.pipeline.md_parser import parse_directory as parse_md_directory
    from mathforge.pipeline.md_parser import parse_file as parse_md_file
    from mathforge.pipeline.ocr_repair import OcrRepairEngine
    from mathforge.storage.file_backend import FileStorageBackend

    ocr_engine = OcrRepairEngine() if repair else None
    storage = FileStorageBackend(output) if not dry_run else None

    if path.is_file():
        files = [path]
    else:
        files = sorted(path.rglob("*.md"))

    if not files:
        error("未找到可解析的 MD 文件")
        raise typer.Exit(1)

    total_blocks = 0
    total_repairs = 0
    total_saved = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("解析 MD 文件...", total=len(files))

        for filepath in files:
            progress.update(task, description=f"解析 {filepath.name}")

            # OCR 修复
            content = filepath.read_text(encoding="utf-8")
            if ocr_engine:
                content, changes = ocr_engine.repair_known_patterns(content)
                total_repairs += len(changes)

            # 解析
            blocks = parse_md_file(filepath, content=content, source_type=source_type)
            total_blocks += len(blocks)

            # 构建 KP 并存储
            kps = build_knowledge_points(blocks, filepath)
            for kp in kps:
                if storage:
                    storage.save_knowledge_point(kp)
                    total_saved += 1

            progress.advance(task)

    if repair:
        console.print(f"OCR 修复: {total_repairs} 处替换")
    if dry_run:
        success(f"[Dry Run] 共解析 {total_blocks} 个知识块")
    else:
        success(f"共保存 {total_saved} 个知识点到 {output}")


@parse_app.command("pdf")
def parse_pdf(
    path: Path = typer.Argument(..., help="PDF 文件或目录", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    backend: str = typer.Option("auto", "--backend", "-b", help="提取后端 (auto/pymupdf/marker)"),
    batch_size: int = typer.Option(0, "--batch-size", help="批次页数 (0=自动)"),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="是否从断点续传"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅解析，不写入存储"),
) -> None:
    """解析 PDF 文件（支持超长文档分批处理）"""
    from mathforge.pipeline.kp_builder import build_knowledge_points
    from mathforge.pipeline.pdf_processor import PdfProcessor
    from mathforge.storage.file_backend import FileStorageBackend

    processor = PdfProcessor(
        checkpoint_dir=output / "checkpoint" if resume else None,
    )

    if path.is_file():
        pdf_files = [path]
    else:
        pdf_files = sorted(path.rglob("*.pdf"))

    if not pdf_files:
        error("未找到可解析的 PDF 文件")
        raise typer.Exit(1)

    storage = FileStorageBackend(output) if not dry_run else None
    total_blocks = 0
    total_saved = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("解析 PDF 文件...", total=len(pdf_files))

        for pdf_file in pdf_files:
            progress.update(task, description=f"解析 {pdf_file.name}")
            try:
                blocks = processor.process_pdf(pdf_file, backend=backend)
                total_blocks += len(blocks)

                kps = build_knowledge_points(blocks, pdf_file)
                for kp in kps:
                    if storage:
                        storage.save_knowledge_point(kp)
                        total_saved += 1
            except Exception as e:
                error(f"处理 {pdf_file.name} 失败: {e}")

            progress.advance(task)

    if dry_run:
        success(f"[Dry Run] 共解析 {total_blocks} 个知识块")
    else:
        success(f"共保存 {total_saved} 个知识点到 {output}")


@parse_app.command("docx")
def parse_docx(
    path: Path = typer.Argument(..., help="Word 文件", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    backend: str = typer.Option("auto", "--backend", "-b", help="后端 (auto/pandoc/python-docx)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅解析，不写入存储"),
) -> None:
    """解析 Word (.docx) 文件"""
    from mathforge.pipeline.docx_processor import process_docx
    from mathforge.pipeline.kp_builder import build_knowledge_points
    from mathforge.storage.file_backend import FileStorageBackend

    if path.is_file():
        docx_files = [path]
    else:
        docx_files = sorted(path.rglob("*.docx"))

    if not docx_files:
        error("未找到可解析的 .docx 文件")
        raise typer.Exit(1)

    storage = FileStorageBackend(output) if not dry_run else None
    total_blocks = 0
    total_saved = 0

    for filepath in docx_files:
        try:
            blocks = process_docx(filepath, backend=backend)
            total_blocks += len(blocks)
            kps = build_knowledge_points(blocks, filepath)
            for kp in kps:
                if storage:
                    storage.save_knowledge_point(kp)
                    total_saved += 1
        except Exception as e:
            error(f"处理 {filepath.name} 失败: {e}")

    if dry_run:
        success(f"[Dry Run] 共解析 {total_blocks} 个知识块")
    else:
        success(f"共保存 {total_saved} 个知识点到 {output}")


@parse_app.command("csv")
def parse_csv_cmd(
    path: Path = typer.Argument(..., help="CSV 文件", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    format: str = typer.Option("auto", "--format", "-f", help="格式 (auto/flat/latex/structured)"),
    encoding: str = typer.Option("utf-8", "--encoding", help="文件编码"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅解析，不写入存储"),
) -> None:
    """解析 CSV 题目文件"""
    from mathforge.pipeline.csv_processor import process_csv
    from mathforge.storage.file_backend import FileStorageBackend

    if path.is_file():
        csv_files = [path]
    else:
        csv_files = sorted(path.rglob("*.csv"))

    if not csv_files:
        error("未找到可解析的 CSV 文件")
        raise typer.Exit(1)

    storage = FileStorageBackend(output) if not dry_run else None
    total_questions = 0
    total_saved = 0

    for filepath in csv_files:
        try:
            results = process_csv(filepath, format=format, encoding=encoding)
            total_questions += len(results)
            if storage:
                for question, solution in results:
                    storage.save_question(question, solution)
                    total_saved += 1
        except Exception as e:
            error(f"处理 {filepath.name} 失败: {e}")

    if dry_run:
        success(f"[Dry Run] 共解析 {total_questions} 道题目")
    else:
        success(f"共保存 {total_saved} 道题目到 {output}")


@parse_app.command("auto")
def parse_auto(
    path: Path = typer.Argument(..., help="文件或目录（自动检测格式）", exists=True),
    output: Path = typer.Option(Path("data"), "--output", "-o", help="输出目录"),
    repair: bool = typer.Option(True, "--repair/--no-repair", help="是否执行 OCR 修复"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅解析，不写入存储"),
) -> None:
    """自动检测格式并解析（支持目录递归扫描）"""
    from mathforge.core.models import DocumentFormat

    EXT_MAP = {
        ".tex": DocumentFormat.TEX,
        ".md": DocumentFormat.MARKDOWN,
        ".pdf": DocumentFormat.PDF,
        ".docx": DocumentFormat.DOCX,
        ".csv": DocumentFormat.CSV,
    }

    if path.is_file():
        files = [path]
    else:
        files = sorted(
            f for f in path.rglob("*")
            if f.suffix.lower() in EXT_MAP and f.is_file()
        )

    if not files:
        error("未找到可解析的文件")
        raise typer.Exit(1)

    # 按格式分组
    grouped: dict[str, list[Path]] = {}
    for f in files:
        fmt = EXT_MAP.get(f.suffix.lower())
        if fmt:
            grouped.setdefault(fmt.value, []).append(f)

    console.print(f"发现 {len(files)} 个文件:")
    for fmt, flist in grouped.items():
        console.print(f"  {fmt}: {len(flist)} 个")

    # 按格式调度
    dry_flag = ["--dry-run"] if dry_run else []
    out_flag = ["-o", str(output)]

    for fmt, flist in grouped.items():
        console.rule(f"[bold]{fmt.upper()}[/bold]")
        if fmt == "tex":
            # 找到共同父目录
            dirs = {f.parent for f in flist}
            for d in dirs:
                parse_tex(d, output, dry_run)
        elif fmt == "markdown":
            dirs = {f.parent for f in flist}
            for d in dirs:
                parse_md(d, output, "auto", repair, dry_run)
        elif fmt == "pdf":
            for f in flist:
                parse_pdf(f, output, "auto", 0, True, dry_run)
        elif fmt == "docx":
            for f in flist:
                parse_docx(f, output, "auto", dry_run)
        elif fmt == "csv":
            for f in flist:
                parse_csv_cmd(f, output, "auto", "utf-8", dry_run)

    success("自动解析完成")
