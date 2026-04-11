"""Rich 输出格式化助手

当前只服务 parse 命令。题目展示相关的 display_question / display_solution /
display_generation_result 等函数已随 generate 子系统一起清理，等待生成引擎
重建时按新 Question/Solution 字段重写。
"""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

console = Console()


def summary_table(title: str, rows: list[dict[str, str | int]], columns: list[str]) -> None:
    """显示汇总表格"""
    table = Table(title=title, show_lines=True)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*[str(row.get(col, "")) for col in columns])
    console.print(table)


def success(msg: str) -> None:
    console.print(f"[green]{msg}[/green]")


def warning(msg: str) -> None:
    console.print(f"[yellow]{msg}[/yellow]")


def error(msg: str) -> None:
    console.print(f"[red]{msg}[/red]")
