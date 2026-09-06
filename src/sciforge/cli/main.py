"""SciForge CLI 入口"""

from pathlib import Path

import typer
from rich.console import Console

from sciforge import __version__
from sciforge.cli.config_cmd import config_app
from sciforge.cli.generate import generate_app
from sciforge.cli.parse import parse_app
from sciforge.cli.serve import serve_app
from sciforge.config.logging import setup_logging

app = typer.Typer(
    name="sciforge",
    help="SciForge - 理工科知识处理与生成平台",
    no_args_is_help=True,
)
console = Console()


def version_callback(value: bool) -> None:
    if value:
        console.print(f"SciForge v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool | None = typer.Option(
        None,
        "--version",
        "-v",
        callback=version_callback,
        is_eager=True,
        help="显示版本号",
    ),
    log_level: str = typer.Option("INFO", "--log-level", help="日志级别"),
    log_format: str = typer.Option("console", "--log-format", help="日志格式 (console|json)"),
    log_dir: Path = typer.Option(Path("data"), "--log-dir", help="日志文件目录"),
) -> None:
    """SciForge CLI"""
    from datetime import datetime

    log_file = log_dir / f"pipeline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    setup_logging(level=log_level, format=log_format, log_file=log_file)


app.add_typer(parse_app)
app.add_typer(generate_app)
app.add_typer(config_app)
app.add_typer(serve_app)
