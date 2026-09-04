"""T11 repair_ocr_noise + T13 parse_structured_q（WP-34）。"""

from __future__ import annotations

from sciforge.pipeline.text_tools import parse_structured_q, repair_ocr_noise

# ── T11 repair_ocr_noise ──────────────────────────────────


def test_word_break_merged_across_newline() -> None:
    result = repair_ocr_noise("这是一个 conti-\nnuous 函数。")
    assert "continuous" in result.repaired_text
    assert "conti-\nnuous" not in result.repaired_text
    assert result.confidence_spans[0].kind == "word_break"


def test_chinese_punctuation_normalized() -> None:
    result = repair_ocr_noise("函数连续.因此有界")
    assert result.repaired_text == "函数连续。因此有界"
    assert result.confidence_spans[0].kind == "punctuation"


def test_english_decimal_not_touched() -> None:
    """编号/小数（数字.数字）不应被误判为中文标点错误。"""
    result = repair_ocr_noise("参见定理 3.14 的证明")
    assert result.repaired_text == "参见定理 3.14 的证明"
    assert result.confidence_spans == []


def test_superscript_heuristic_applied() -> None:
    result = repair_ocr_noise("x2+y2=1")
    assert result.repaired_text == "x^2+y^2=1"
    kinds = [s.kind for s in result.confidence_spans]
    assert kinds.count("superscript") == 2


def test_superscript_not_applied_to_chapter_numbers() -> None:
    """ "第2章"类模式不应被误判为上下标（数字后是"章"字，不在触发字符集里，天然不匹配）。"""
    result = repair_ocr_noise("见第2章内容")
    assert result.repaired_text == "见第2章内容"


def test_rn_confusion_only_flagged_not_replaced() -> None:
    result = repair_ocr_noise("这是一段rn有噪声的文字")
    assert result.repaired_text == "这是一段rn有噪声的文字"  # 不自动替换
    spans = [s for s in result.confidence_spans if s.kind == "char_confusion"]
    assert len(spans) == 1
    assert spans[0].suggestion == ""  # 只标记不建议


def test_clean_text_no_spans() -> None:
    result = repair_ocr_noise("这是一段完全正常的中文陈述。")
    assert result.repaired_text == "这是一段完全正常的中文陈述。"
    assert result.confidence_spans == []


def test_spans_sorted_by_position() -> None:
    result = repair_ocr_noise("函数连续.因此x2有界")
    positions = [s.start for s in result.confidence_spans]
    assert positions == sorted(positions)


# ── T13 parse_structured_q ────────────────────────────────


def test_inline_options_same_line() -> None:
    result = parse_structured_q("下列正确的是 A. 甲选项 B. 乙选项 C. 丙选项 D. 丁选项")
    assert result.options == ["A. 甲选项", "B. 乙选项", "C. 丙选项", "D. 丁选项"]


def test_paren_style_options() -> None:
    result = parse_structured_q("选择：(A) 第一项 (B) 第二项 (C) 第三项")
    assert result.options == ["A. 第一项", "B. 第二项", "C. 第三项"]


def test_line_by_line_options() -> None:
    text = "下列哪个正确？\nA．选项一\nB．选项二\nC．选项三"
    result = parse_structured_q(text)
    assert result.options == ["A. 选项一", "B. 选项二", "C. 选项三"]


def test_no_options_returns_empty_not_fabricated() -> None:
    result = parse_structured_q("这是一道证明题，没有选项。")
    assert result.options == []


def test_blanks_counted_from_underscores() -> None:
    result = parse_structured_q("设 f(x) = ___，则 f'(x) = ____")
    assert result.blanks == 2


def test_blanks_counted_from_empty_parens() -> None:
    result = parse_structured_q("函数的定义域是（　），值域是（　）")
    assert result.blanks == 2


def test_no_blanks_returns_none() -> None:
    result = parse_structured_q("这是一道解答题。")
    assert result.blanks is None


def test_correct_option_extracted() -> None:
    result = parse_structured_q("A. 甲 B. 乙 C. 丙\n答案：B")
    assert result.correct_option == "B"


def test_correct_option_none_when_absent() -> None:
    result = parse_structured_q("A. 甲 B. 乙 C. 丙")
    assert result.correct_option is None


def test_table_subquestions_extracted() -> None:
    text = "| 甲 | 乙 |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
    result = parse_structured_q(text)
    assert result.table_subquestions == [["甲", "乙"], ["1", "2"], ["3", "4"]]


def test_no_table_returns_empty_list() -> None:
    result = parse_structured_q("普通题面，没有表格。")
    assert result.table_subquestions == []


def test_prefers_line_style_over_inline_when_both_could_match() -> None:
    """逐行排版优先：避免同行/括号正则误拆逐行文本。"""
    text = "A．第一行较长的文字说明\nB．第二行较长的文字说明"
    result = parse_structured_q(text)
    assert len(result.options) == 2
    assert result.options[0].startswith("A.")
