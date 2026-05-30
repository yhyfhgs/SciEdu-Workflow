"""detect_section_boundaries — Phase 2 结构化预分块"""

from __future__ import annotations

from sciforge.pipeline.recognizers import detect_section_boundaries


def test_markdown_heading_levels() -> None:
    lines = [
        "# Chapter 1",
        "intro",
        "## Section 1.1",
        "text",
        "### Subsection",
        "more",
        "#### Subsubsection",
    ]
    bounds = detect_section_boundaries(lines)
    assert bounds == [
        (0, 1, "Chapter 1"),
        (2, 2, "Section 1.1"),
        (4, 3, "Subsection"),
        (6, 4, "Subsubsection"),
    ]


def test_latex_sectioning() -> None:
    lines = [
        r"\chapter{Intro}",
        "body",
        r"\section{Analysis}",
        r"\subsection{Limits}",
        r"\subsubsection{Details}",
    ]
    bounds = detect_section_boundaries(lines)
    levels = [b[1] for b in bounds]
    titles = [b[2] for b in bounds]
    assert levels == [1, 2, 3, 4]
    assert titles == ["Intro", "Analysis", "Limits", "Details"]


def test_latex_starred_variants() -> None:
    """\\section*{}, \\chapter*{} 也应被识别"""
    lines = [r"\section*{Unnumbered Section}", r"\chapter*{Preface}"]
    bounds = detect_section_boundaries(lines)
    assert len(bounds) == 2
    assert bounds[0][2] == "Unnumbered Section"
    assert bounds[1][2] == "Preface"


def test_chinese_chapter_bold() -> None:
    """中文粗体章节标题"""
    lines = [
        "**第一章 极限**",
        "text",
        "**第 1.2 节 连续性**",
        "more text",
    ]
    bounds = detect_section_boundaries(lines)
    assert len(bounds) >= 1
    # 第一行应为章（level=1）
    first_line, first_level, first_title = bounds[0]
    assert first_line == 0
    assert first_level == 1


def test_no_false_positive_on_plain_text() -> None:
    """普通段落 / 题干 / 公式不应被误识别"""
    lines = [
        "This is regular text.",
        "定理 3.1 证明 a+b>0",
        "$$f(x) = x^2$$",
        "Example: the derivative is 2x.",
        "```python\nprint('hi')\n```",
    ]
    bounds = detect_section_boundaries(lines)
    assert bounds == []


def test_empty_input() -> None:
    assert detect_section_boundaries([]) == []


def test_mixed_formats_preserves_order() -> None:
    """混合 md / latex / 中文 按源顺序返回"""
    lines = [
        "# Top Chapter",
        r"\section{Sub}",
        "regular",
        "**第二章 后继**",
    ]
    bounds = detect_section_boundaries(lines)
    line_nos = [b[0] for b in bounds]
    assert line_nos == sorted(line_nos)
    assert len(bounds) >= 3
