"""文档导出：题目集合 → markdown / DOCX（P2 组卷/讲义导出）

题目用通用 dict（含 content/solution/difficulty/question_type）渲染，与 API schema 解耦。
markdown 零依赖；DOCX 用 python-docx（docx extra，延迟 import）。PDF（LaTeX/reportlab）为后续。
"""

from __future__ import annotations

import io
from typing import Any


def render_markdown(
    title: str, items: list[dict[str, Any]], *, include_solutions: bool = True
) -> str:
    """渲染题目集合为 markdown 文本。"""
    lines: list[str] = [f"# {title}", ""]
    for i, item in enumerate(items, 1):
        diff = item.get("difficulty", "?")
        lines.append(f"## 第 {i} 题（难度 {diff}）")
        lines.append("")
        lines.append(str(item.get("content", "")))
        lines.append("")
        sol = item.get("solution")
        if include_solutions and sol:
            lines.append("**解答：**")
            lines.append("")
            lines.append(str(sol))
            lines.append("")
    return "\n".join(lines)


def render_docx(
    title: str, items: list[dict[str, Any]], *, include_solutions: bool = True
) -> bytes:
    """渲染题目集合为 DOCX 字节流。需 python-docx（docx extra），未装则抛 ImportError。"""
    from docx import Document  # 可选依赖（docx extra）

    doc = Document()
    doc.add_heading(title, level=0)
    for i, item in enumerate(items, 1):
        diff = item.get("difficulty", "?")
        doc.add_heading(f"第 {i} 题（难度 {diff}）", level=1)
        doc.add_paragraph(str(item.get("content", "")))
        sol = item.get("solution")
        if include_solutions and sol:
            para = doc.add_paragraph()
            para.add_run("解答：").bold = True
            doc.add_paragraph(str(sol))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
