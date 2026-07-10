"""QGen 轻量预筛单测"""

from __future__ import annotations

import pytest

from sciforge.agents.qgen.prefilter import (
    MAX_NEGATIVE_SIMILARITY,
    check_novelty,
    check_question_form,
    check_structural_validity,
    cosine_similarity,
    prefilter_candidate,
)
from sciforge.core.models import Discipline, Question


def _q(content: str) -> Question:
    return Question(
        discipline=Discipline.MATH,
        content=content,
    )


def test_structural_too_short() -> None:
    ok, reason = check_structural_validity("short")
    assert not ok
    assert "content_too_short" in reason


def test_structural_empty() -> None:
    ok, reason = check_structural_validity("")
    assert not ok
    assert "content_too_short" in reason


def test_structural_unbalanced_braces() -> None:
    ok, reason = check_structural_validity(
        "A reasonable-length problem statement with {f(x) but no closing brace here."
    )
    assert not ok
    assert "unbalanced_braces" in reason


def test_structural_unbalanced_dollars() -> None:
    ok, reason = check_structural_validity(
        "Consider the function $f(x) = x^2 which is smooth at every point in R."
    )
    assert not ok
    assert "unbalanced_dollar" in reason


def test_structural_valid_inline_math() -> None:
    ok, reason = check_structural_validity(
        "Let $f(x)=x^2$. Compute $f'(1)$ using the definition of the derivative."
    )
    assert ok, reason
    assert reason == ""


def test_structural_valid_display_math() -> None:
    """$$ ... $$ 成对不触发 unbalanced_dollar"""
    ok, _ = check_structural_validity(
        "Compute the integral $$\\int_0^1 x^2 \\, dx$$ by the fundamental theorem of calculus."
    )
    assert ok


def test_cosine_identical() -> None:
    from collections import Counter

    v = Counter({"a": 2, "b": 3})
    assert abs(cosine_similarity(v, v) - 1.0) < 1e-9


def test_cosine_disjoint() -> None:
    from collections import Counter

    a = Counter({"x": 1})
    b = Counter({"y": 1})
    assert cosine_similarity(a, b) == 0.0


def test_novelty_empty_negatives() -> None:
    ok, _ = check_novelty("some long math problem content here for testing", [])
    assert ok


def test_novelty_rejects_near_duplicate() -> None:
    """与 negative 几乎相同时被拒"""
    text = "证明对任意正整数 n 有 n plus one greater than zero using mathematical induction."
    neg = _q(text)  # 完全相同
    ok, reason = check_novelty(text, [neg])
    assert not ok
    assert "too_similar" in reason


def test_novelty_accepts_distinct() -> None:
    cand_text = "Evaluate the limit of sin(x)/x as x approaches zero."
    neg_text = "Find the eigenvalues of the matrix [[2,0],[0,3]]."
    ok, _ = check_novelty(cand_text, [_q(neg_text)])
    assert ok


def test_prefilter_combined_structural_fail() -> None:
    cand = _q("tiny")
    ok, reason = prefilter_candidate(cand, [])
    assert not ok
    assert "content_too_short" in reason


def test_prefilter_combined_novelty_fail() -> None:
    text = "Compute the determinant of a 2x2 matrix and argue why it equals ad-bc using row ops."
    cand = _q(text)
    neg = _q(text)
    ok, reason = prefilter_candidate(cand, [neg])
    assert not ok
    assert "too_similar" in reason


def test_prefilter_combined_passes_valid_novel() -> None:
    cand = _q(
        "Let $f(x) = \\sin(x)$. Compute the second derivative $f''(\\pi/4)$ by direct calculation."
    )
    neg = _q("Find the inverse of the matrix $\\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}$.")
    ok, reason = prefilter_candidate(cand, [neg])
    assert ok, reason


@pytest.mark.parametrize(
    ("candidate", "reason"),
    [
        (
            Question(
                question_form="choice",
                content="请选择下列关于连续函数与紧致区间关系的唯一正确陈述。",
                options=["A. 仅有一个选项"],
                correct_option="A",
            ),
            "at_least_two_options",
        ),
        (
            Question(
                question_form="multiple_choice",
                content="请选择所有满足给定代数恒等式与定义域约束的正确表达式。",
                options=["A. 表达式一", "B. 表达式二"],
            ),
            "requires_correct_option",
        ),
        (
            Question(
                question_form="fill_blank",
                content="根据微积分基本定理完成下列积分计算并填入唯一数值答案。",
                blanks=0,
            ),
            "positive_blanks",
        ),
        (
            Question(
                question_form="true_false",
                content="判断下列关于完备度量空间中压缩映射不动点的陈述是否正确。",
            ),
            "requires_correct_option",
        ),
    ],
)
def test_question_form_contract_rejects_incomplete_structured_fields(
    candidate: Question,
    reason: str,
) -> None:
    ok, detail = check_question_form(candidate)
    assert not ok
    assert reason in detail


def test_question_form_contract_accepts_complete_choice() -> None:
    candidate = Question(
        question_form="choice",
        content="请选择下列关于连续函数与紧致区间关系的唯一正确陈述。",
        options=["A. 正确陈述", "B. 错误陈述"],
        correct_option="A",
    )
    assert check_question_form(candidate) == (True, "")


def test_threshold_constants_reasonable() -> None:
    """防止误改常量：相似度阈值应在 (0, 1) 之间"""
    assert 0.0 < MAX_NEGATIVE_SIMILARITY < 1.0
