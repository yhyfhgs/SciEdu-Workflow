"""MathForge CLI 入口"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from mathforge import __version__
from mathforge.cli.generate import generate_app
from mathforge.cli.parse import parse_app
from mathforge.config.logging import setup_logging

app = typer.Typer(
    name="mathforge",
    help="MathForge - AI 驱动的大学数学教育平台",
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"MathForge v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: Optional[bool] = typer.Option(
        None, "--version", "-v", callback=version_callback, is_eager=True,
        help="显示版本号",
    ),
    log_level: str = typer.Option("INFO", "--log-level", help="日志级别"),
    log_format: str = typer.Option("console", "--log-format", help="日志格式 (console|json)"),
) -> None:
    """MathForge CLI"""
    setup_logging(level=log_level, format=log_format)


app.add_typer(parse_app)
app.add_typer(generate_app)


@app.command("validate")
def validate(
    data_dir: Path = typer.Argument(Path("data"), help="数据目录"),
) -> None:
    """对已处理的数据运行完整性验证"""
    import json

    from mathforge.core.models import MdBlock
    from mathforge.pipeline.content_validator import validate_blocks

    knowledge_dir = data_dir / "knowledge"
    if not knowledge_dir.exists():
        console.print("[red]知识库目录不存在[/red]")
        raise typer.Exit(1)

    # 收集所有知识点 JSON 并转为 MdBlock 验证
    all_blocks: list[MdBlock] = []
    json_files = list(knowledge_dir.rglob("*.json"))
    json_files = [f for f in json_files if f.name != "index.json"]

    for jf in json_files:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            block = MdBlock(
                block_type=data.get("kp_type", "text"),
                number=data.get("id", ""),
                content=data.get("content_latex", ""),
                source_file=str(jf),
            )
            all_blocks.append(block)
        except Exception:
            pass

    if not all_blocks:
        console.print("[yellow]未找到知识点数据[/yellow]")
        raise typer.Exit(0)

    report = validate_blocks(all_blocks, str(data_dir))

    console.print(f"\n验证 {report.total_blocks} 个块:")
    console.print(f"  括号错误: {report.bracket_errors}")
    console.print(f"  语义警告: {report.semantic_warnings}")
    console.print(f"  悬空引用: {report.dangling_references}")
    console.print(f"  编号跳跃: {report.numbering_gaps}")
    console.print(f"  总错误数: {report.error_count}")
    console.print(f"  总警告数: {report.warning_count}")

    if report.has_errors:
        console.print("\n[red]发现错误，详情如下:[/red]")
        for issue in report.issues:
            if issue.level == "error":
                console.print(f"  [red]ERROR[/red] [{issue.category}] {issue.message}")
    else:
        console.print("\n[green]验证通过，无错误[/green]")
