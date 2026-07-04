"""T10 正则 DSL（WP-31）——compose 拼装 + 掐头去尾。"""

from __future__ import annotations

import pytest

from sciforge.pipeline.text_tools import compose, regex_dsl, trim_head, trim_item, trim_tail


def test_compose_unknown_fragment_raises() -> None:
    with pytest.raises(KeyError):
        compose("not_a_real_fragment")


def test_compose_textbook_head_matches_bold_theorem() -> None:
    expr = compose("marker_head", "number", optional_tail=("title_paren",))
    matches = regex_dsl(expr, "**定理 3.1**（中值定理）设 $f$ 连续。")
    assert len(matches) == 1
    assert matches[0].groups["kind"] == "定理"
    assert matches[0].groups["num"] == "3.1"
    assert matches[0].groups["title"] == "中值定理"


def test_compose_enum_paren_matches_choice_style() -> None:
    expr = compose("enum_paren")
    matches = regex_dsl(expr, "(1) 第一问 (2) 第二问")
    assert [m.groups["en"] for m in matches] == ["1", "2"]


def test_compose_qed_variants() -> None:
    expr = compose("qed")
    for text in ("证毕。", "$\\square$", "\\hfill $\\square$", "Q.E.D."):
        assert regex_dsl(expr, text), f"未匹配: {text!r}"


# ── trim_head ─────────────────────────────────────────────


def test_trim_head_textbook_strips_marker_and_number() -> None:
    text = "定理3.1．设 $f$ 在闭区间上连续，则有界。"
    offset = trim_head(text, marker_style="textbook")
    assert text[offset:].startswith("设 $f$")


def test_trim_head_bold_with_title() -> None:
    text = "**定理 3.1**（中值定理）设 $f$ 连续。"
    offset = trim_head(text, marker_style="textbook")
    assert text[offset:].startswith("设 $f$")


def test_trim_head_question_number_style() -> None:
    text = "(1) 求下列极限的值。"
    offset = trim_head(text, marker_style="question_number")
    assert text[offset:].startswith("求下列极限")


def test_trim_head_no_marker_returns_zero() -> None:
    assert trim_head("纯正文，没有任何条目标记。", marker_style="textbook") == 0


# ── trim_tail ─────────────────────────────────────────────


def test_trim_tail_cuts_at_qed() -> None:
    text = "证明：显然成立。证毕。这是下一段无关内容。"
    offset = trim_tail(text)
    assert text[:offset].endswith("证毕。")


def test_trim_tail_stops_before_next_marker() -> None:
    text = "定理 3.1 的证明省略。定理 3.2 是下一条内容。"
    offset = trim_tail(text)
    assert text[:offset] == "定理 3.1 的证明省略。"


def test_trim_tail_no_boundary_returns_full_length() -> None:
    text = "一段没有明确结尾标记的正文"
    assert trim_tail(text) == len(text)


def test_trim_tail_avoids_cutting_inside_open_math_env() -> None:
    # qed 出现在文本里，但其前有个未闭合的 begin{align}（模拟误判场景）
    text = "推导如下\\begin{align}\na &= b\n证毕。"
    offset = trim_tail(text)
    # 不应该在 "证毕。" 处切断导致 align 环境残缺——应回退到环境开启前
    assert offset <= text.index("\\begin{align}")


# ── trim_item（组合）────────────────────────────────────


def test_trim_item_full_pipeline() -> None:
    text = "**定理 3.1**（中值定理）设 $f$ 连续，则存在 $c$。证毕。定理 3.2 无关内容。"
    start, end = trim_item(text, marker_style="textbook")
    trimmed = text[start:end]
    assert trimmed.startswith("设 $f$")
    assert trimmed.endswith("证毕。")
    assert "定理 3.2" not in trimmed


def test_trim_item_conflict_falls_back_to_full_text() -> None:
    # 极端构造：让 head 越过 tail（几乎不会真实发生，验证防御分支）
    text = "普通短句"
    start, end = trim_item(text, marker_style="question_number")
    assert (start, end) == (0, len(text))
