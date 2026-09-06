"""T1 normalize_text + T2 strip_running_headers + T3 detect_structure + T9
extract_subinterval（WP-31）。"""

from __future__ import annotations

from sciforge.pipeline.text_tools import (
    detect_structure,
    extract_subinterval,
    normalize_text,
    strip_running_headers,
)

# ── T1 normalize_text ────────────────────────────────────


def test_normalize_fullwidth_to_halfwidth() -> None:
    result = normalize_text("Ａ１２３")
    assert result.text == "A123"


def test_normalize_strips_zero_width_chars() -> None:
    result = normalize_text("a​b")
    assert result.text == "ab"


def test_normalize_unifies_quotes_and_hyphens() -> None:
    result = normalize_text("“hello”—world")
    assert result.text == '"hello"-world'


def test_normalize_offset_map_length_matches_output() -> None:
    result = normalize_text("Ａ１ｂ")
    assert len(result.offset_map) == len(result.text)


def test_normalize_offset_map_points_to_original_positions() -> None:
    raw = "x​Ａ"  # x, zero-width(removed), fullwidth-A
    result = normalize_text(raw)
    assert result.text == "xA"
    assert result.offset_map == [0, 2]  # 'x'原0, 'A'来自原文位置2


def test_normalize_empty_string() -> None:
    result = normalize_text("")
    assert result.text == ""
    assert result.offset_map == []


# ── T2 strip_running_headers ─────────────────────────────


def test_strip_headers_detects_repeated_footer() -> None:
    pages = [
        ["第一页正文第一行", "第一页正文第二行", "SciForge 讲义"],
        ["第二页正文第一行", "第二页正文第二行", "SciForge 讲义"],
        ["第三页正文第一行", "第三页正文第二行", "SciForge 讲义"],
    ]
    result = strip_running_headers(pages)
    assert "SciForge 讲义" in result.detected_patterns
    assert result.lines[2] == ""  # 第一页页脚被剥离
    assert result.lines[0] == "第一页正文第一行"  # 正文不受影响


def test_strip_headers_single_page_noop() -> None:
    result = strip_running_headers([["只有一页", "正文"]])
    assert result.lines == ["只有一页", "正文"]
    assert result.detected_patterns == []


def test_strip_headers_no_repetition_untouched() -> None:
    pages = [["独特内容 A"], ["独特内容 B"], ["独特内容 C"]]
    result = strip_running_headers(pages)
    assert result.detected_patterns == []
    assert result.lines == ["独特内容 A", "独特内容 B", "独特内容 C"]


def test_strip_headers_ignores_long_lines() -> None:
    # 超长重复行不太可能是页眉页脚（可能是恰好雷同的正文）
    long_line = "这是一段" * 20
    pages = [[long_line, "短正文1"], [long_line, "短正文2"]]
    result = strip_running_headers(pages)
    assert long_line not in result.detected_patterns


# ── T3 detect_structure ──────────────────────────────────


def test_detect_structure_heading_levels() -> None:
    nodes = detect_structure(["# 第一章", "## 1.1 节"])
    assert [(n.kind, n.level, n.text) for n in nodes] == [
        ("heading", 1, "第一章"),
        ("heading", 2, "1.1 节"),
    ]


def test_detect_structure_table_block_merged() -> None:
    lines = ["正文", "| a | b |", "| - | - |", "| 1 | 2 |", "正文"]
    nodes = detect_structure(lines, line_base=1)
    tables = [n for n in nodes if n.kind == "table_row"]
    assert len(tables) == 1
    assert (tables[0].start_line, tables[0].end_line) == (2, 4)


def test_detect_structure_math_block() -> None:
    lines = ["文字", "$$", "x^2 + y^2 = 1", "$$", "文字"]
    nodes = detect_structure(lines, line_base=1)
    math = [n for n in nodes if n.kind == "math_block"]
    assert len(math) == 1
    assert (math[0].start_line, math[0].end_line) == (2, 4)


def test_detect_structure_latex_env_block() -> None:
    lines = ["\\begin{theorem}", "内容", "\\end{theorem}"]
    nodes = detect_structure(lines, line_base=1)
    envs = [n for n in nodes if n.kind == "env_block"]
    assert len(envs) == 1
    assert envs[0].text == "theorem"
    assert (envs[0].start_line, envs[0].end_line) == (1, 3)


def test_detect_structure_list_items() -> None:
    nodes = detect_structure(["- 第一项", "* 第二项", "1. 第三项"])
    assert [n.kind for n in nodes] == ["list_item", "list_item", "list_item"]


def test_detect_structure_plain_text_no_nodes() -> None:
    assert detect_structure(["普通段落", "没有任何结构标记"]) == []


# ── T9 extract_subinterval ───────────────────────────────


def _annotated(lines: list[str], base: int) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


def test_extract_subinterval_basic() -> None:
    window = _annotated(["行A", "行B", "行C", "行D"], 101)
    result = extract_subinterval(window, 102, 103, line_base=101)
    assert result == "行B\n行C"


def test_extract_subinterval_with_context() -> None:
    window = _annotated(["行A", "行B", "行C", "行D"], 101)
    result = extract_subinterval(window, 102, 102, line_base=101, context_lines=1)
    assert result == "行A\n行B\n行C"


def test_extract_subinterval_clamps_to_window_bounds() -> None:
    window = _annotated(["行A", "行B"], 101)
    result = extract_subinterval(window, 100, 105, line_base=101, context_lines=3)
    assert result == "行A\n行B"


def test_extract_subinterval_out_of_range_returns_empty() -> None:
    window = _annotated(["行A", "行B"], 101)
    assert extract_subinterval(window, 200, 201, line_base=101) == ""


def test_extract_subinterval_empty_window() -> None:
    assert extract_subinterval([], 1, 5, line_base=1) == ""
