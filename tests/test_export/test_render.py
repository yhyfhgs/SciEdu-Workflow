"""文档导出渲染测试（markdown / DOCX）"""

from __future__ import annotations

from sciforge.export import render_docx, render_markdown

_ITEMS = [
    {"id": "1", "content": "题目一内容", "difficulty": 5, "solution": "解答一"},
    {"id": "2", "content": "题目二内容", "difficulty": 7, "solution": "解答二"},
]


def test_render_markdown_includes_questions_and_solutions() -> None:
    md = render_markdown("测试卷", _ITEMS, include_solutions=True)
    assert "# 测试卷" in md
    assert "题目一内容" in md and "题目二内容" in md
    assert "解答一" in md
    assert "第 1 题" in md and "第 2 题" in md


def test_render_markdown_excludes_solutions() -> None:
    md = render_markdown("测试卷", _ITEMS, include_solutions=False)
    assert "题目一内容" in md
    assert "解答一" not in md


def test_render_docx_returns_valid_bytes() -> None:
    data = render_docx("测试卷", _ITEMS)
    assert isinstance(data, bytes)
    assert data[:2] == b"PK"  # DOCX 是 zip，magic bytes PK


def test_render_docx_empty_items() -> None:
    data = render_docx("空卷", [])
    assert data[:2] == b"PK"
