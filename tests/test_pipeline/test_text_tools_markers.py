"""T4 find_item_markers（WP-12）——行首精确匹配、kind/编号/强弱分类。"""

from __future__ import annotations

from sciforge.pipeline.text_tools import find_item_markers


def _kinds(lines: list[str]) -> list[str]:
    return [m.kind for m in find_item_markers(lines)]


def test_chinese_keyword_markers() -> None:
    lines = [
        "**定理 3.1**（中值定理）设 $f$ 连续。",
        "定义2．1．5．【初等变换】域 $F$ 上……",
        "例 2.2.1．实矩阵可逆。",
        "习题 3．2 求下列极限。",
    ]
    markers = find_item_markers(lines)
    assert [m.kind for m in markers] == ["theorem", "definition", "example", "exercise"]
    assert markers[0].number_hint == "3.1"
    assert markers[1].number_hint == "2.1.5"
    assert all(m.strength == "strong" for m in markers)


def test_latex_env_markers_with_optional_number() -> None:
    markers = find_item_markers(["\\begin{problem}[第1题]", "\\begin{solution}", "\\begin{proof}"])
    assert [m.kind for m in markers] == ["problem", "solution", "proof"]
    assert markers[0].number_hint == "1"


def test_mid_line_reference_not_matched() -> None:
    """精度导向：正文引用不进真值清单（与召回导向的 fast-skip 相反）。"""
    assert find_item_markers(["由定理 3.1 可知结论成立。"]) == []
    assert find_item_markers(["应用定义 2.1.5 得到。"]) == []


def test_bare_numbered_line_is_weak_problem() -> None:
    markers = find_item_markers(["12. 设 $G$ 是群，证明其中心是正规子群。"])
    assert len(markers) == 1
    assert markers[0].kind == "problem"
    assert markers[0].number_hint == "12"
    assert markers[0].strength == "weak"


def test_keyword_without_number_is_weak() -> None:
    """条目类关键字无编号（如"例 如下"）误命中概率高 → 降为 weak。"""
    markers = find_item_markers(["例 如下所示的矩阵。"])
    assert len(markers) == 1
    assert markers[0].strength == "weak"


def test_ordinal_problem_marker() -> None:
    markers = find_item_markers(["第 3 题 已知数列……"])
    assert markers[0].kind == "problem"
    assert markers[0].number_hint == "3"


def test_section_markers() -> None:
    assert _kinds(["## 2.2 矩阵基本概念"]) == ["section"]
    assert _kinds(["\\section{线性空间}"]) == ["section"]
    assert _kinds(["第 三 章"]) == ["section"]


def test_global_line_uses_line_base() -> None:
    markers = find_item_markers(["正文", "定理 5.1 陈述"], line_base=101)
    assert len(markers) == 1
    assert markers[0].global_line == 102


def test_plain_text_yields_nothing() -> None:
    assert find_item_markers(["这是一段普通叙述。", "", "  另一段。"]) == []


def test_cross_discipline_markers_are_typed_and_strength_aware() -> None:
    markers = find_item_markers(
        [
            "反应 2.1 酯化反应",
            "实验 4.2 验证酶活性",
            "过程 3.1 有丝分裂",
            "功能 说明如下。",
        ]
    )

    assert [marker.kind for marker in markers] == [
        "reaction",
        "experiment",
        "process",
        "function",
    ]
    assert [marker.strength for marker in markers] == ["strong", "strong", "strong", "weak"]
