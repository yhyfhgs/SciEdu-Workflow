"""题号规范化（Phase 0 #4）回归测试"""

from __future__ import annotations

import pytest

from sciforge.core.models import Discipline, KnowledgePoint, Question
from sciforge.core.numbering import canonicalize_number, parse_number_components


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("例 3.2", "3.2"),
        ("定理3.1.2", "3.1.2"),
        ("第1题", "1"),
        ("1．2．3", "1.2.3"),  # 全角句号
        ("Exercise 5", "5"),
        ("(3.2)", "3.2"),
        ("习题 12", "12"),
        ("定义 2．7", "2.7"),
        ("引言", ""),  # 无数字
        ("", ""),
        ("３.２", "3.2"),  # 全角数字
    ],
)
def test_canonicalize(raw: str, expected: str) -> None:
    assert canonicalize_number(raw) == expected


def test_canonicalize_idempotent() -> None:
    for raw in ["例 3.2", "第1题", "1．2．3", "Exercise 5"]:
        once = canonicalize_number(raw)
        assert canonicalize_number(once) == once


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5", (None, None, 5)),
        ("3.2", (3, None, 2)),
        ("3.2.1", (3, 2, 1)),
        ("例 3.2", (3, None, 2)),
        ("引言", (None, None, None)),
    ],
)
def test_parse_components(raw: str, expected: tuple[int | None, int | None, int | None]) -> None:
    assert parse_number_components(raw) == expected


def test_question_auto_derives_numbering() -> None:
    q = Question(number="例 3.2", content="计算 x", discipline=Discipline.MATH)
    assert q.number_canonical == "3.2"
    assert (q.seq_chapter, q.seq_section, q.seq_index) == (3, None, 2)


def test_kp_auto_derives_numbering() -> None:
    kp = KnowledgePoint(number="定理 3.1.2", content="设 f 连续")
    assert kp.number_canonical == "3.1.2"
    assert (kp.seq_chapter, kp.seq_section, kp.seq_index) == (3, 1, 2)


def test_explicit_canonical_not_overwritten() -> None:
    """显式提供 number_canonical 时不被自动派生覆盖。"""
    q = Question(number="例 3.2", number_canonical="custom", content="x")
    assert q.number_canonical == "custom"


def test_no_number_no_seq() -> None:
    kp = KnowledgePoint(number="", content="无编号定义")
    assert kp.number_canonical == ""
    assert kp.seq_index is None
