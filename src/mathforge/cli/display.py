"""Rich 输出格式化助手"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

if TYPE_CHECKING:
    from mathforge.core.models import GeneratedQuestion, Question, Solution

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


# ── 题目与生成结果显示 ────────────────────────────────


def display_question(q: Question, solution: Solution | None = None) -> None:
    """美化显示单道题目"""
    subtitle_parts = [
        f"难度: {q.difficulty}/10",
        f"类型: {q.question_type.value}",
        f"科目: {q.subject.value}",
    ]
    if q.difficulty_ai is not None:
        subtitle_parts.append(f"AI评分: {q.difficulty_ai}")

    console.print(
        Panel(
            q.content_latex,
            title=f"[bold]{q.id[:36]}[/bold]",
            subtitle=" | ".join(subtitle_parts),
            border_style="blue",
        )
    )

    if q.tags:
        console.print(f"[dim]标签:[/dim] {', '.join(q.tags)}")

    if q.source:
        parts = []
        if q.source.exam_name:
            parts.append(q.source.exam_name)
        if q.source.exam_type:
            parts.append(q.source.exam_type.value)
        if q.source.year:
            parts.append(str(q.source.year))
        if parts:
            console.print(f"[dim]来源:[/dim] {' '.join(parts)}")

    if solution:
        display_solution(q, solution)


def display_solution(q: Question, solution: Solution) -> None:
    """显示解答"""
    sol_text = solution.solution_latex
    if len(sol_text) > 800:
        sol_text = sol_text[:800] + "..."

    console.print(
        Panel(
            sol_text,
            title="[bold]解答[/bold]",
            subtitle=(
                f"置信度: {solution.confidence}"
                + (f" | 方法: {', '.join(solution.methods_used)}" if solution.methods_used else "")
            ),
            border_style="green",
        )
    )


def display_generation_result(result: GeneratedQuestion) -> None:
    """美化显示生成结果"""
    display_question(result.question, result.solution)

    # 难度评分表
    d = result.difficulty_score
    score_table = Table(title=f"难度评分: {d.overall}/10")
    score_table.add_column("维度", style="cyan")
    score_table.add_column("分数", justify="center")
    score_table.add_column("权重", justify="center", style="dim")

    dims = [
        ("concept_count", "知识点数", 0.15),
        ("step_count", "步骤数", 0.15),
        ("abstraction_level", "抽象度", 0.12),
        ("technique_novelty", "技巧新颖度", 0.12),
        ("condition_parsing", "条件解读", 0.10),
        ("computation_load", "计算量", 0.08),
        ("proof_structure", "证明结构", 0.10),
        ("prerequisite_depth", "前置知识", 0.08),
        ("multi_representation", "多表示切换", 0.05),
        ("metacognition", "策略选择", 0.05),
    ]
    for attr, label, weight in dims:
        score_table.add_row(label, f"{getattr(d, attr)}/10", f"{weight:.0%}")

    console.print(score_table)

    if d.reasoning:
        console.print(f"[dim]评分理由: {d.reasoning}[/dim]")

    # 验证结果
    console.print()
    v = result.verification
    for check, passed in v.checks.items():
        icon = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        console.print(f"  {icon} {check}")

    # 元数据
    meta = result.generation_meta
    console.print(
        f"\n[dim]策略: {meta.get('strategy', '?')} | "
        f"轮次: {meta.get('round', '?')} | "
        f"上下文知识点: {meta.get('context_kps', '?')}[/dim]"
    )
