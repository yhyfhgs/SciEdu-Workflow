"""DisciplineSpec.infer_branch（Phase 3 #6/#9，修复 _infer_branch 死代码）"""

from __future__ import annotations

import pytest

from sciforge.core.models import Discipline
from sciforge.disciplines import DISCIPLINE_REGISTRY


@pytest.mark.parametrize(
    ("texts", "expected"),
    [
        (("第三章 连续函数", "", ""), "analysis"),
        (("", "线性空间与矩阵", ""), "linear_algebra"),
        (("群论初步", "", ""), "abstract_algebra"),
        (("实分析", "", ""), "real_analysis"),  # 长关键词优先于 "分析"
        (("复变函数", "", ""), "complex_analysis"),
        (("数论基础", "", ""), "number_theory"),
        (("无关章节标题", "", ""), None),
        (("", "", ""), None),
    ],
)
def test_math_infer_branch(texts: tuple[str, ...], expected: str | None) -> None:
    spec = DISCIPLINE_REGISTRY.get(Discipline.MATH)
    assert spec.infer_branch(*texts) == expected


def test_inferred_branch_in_allowed() -> None:
    """推断出的分支必须在 allowed_branches 内（否则 taxonomy filter 会 fallback）。"""
    spec = DISCIPLINE_REGISTRY.get(Discipline.MATH)
    for branch in spec.branch_keywords.values():
        assert branch in spec.allowed_branches, f"{branch} 不在 allowed_branches"


def test_physics_infer_branch() -> None:
    spec = DISCIPLINE_REGISTRY.get(Discipline.PHYSICS)
    assert spec.infer_branch("第二章 牛顿力学", "", "") == "mechanics"
    assert spec.infer_branch("", "电磁感应", "") == "electromagnetism"
    assert spec.infer_branch("量子态", "", "") == "quantum_mechanics"
    for branch in spec.branch_keywords.values():
        assert branch in spec.allowed_branches


def test_longer_keyword_wins() -> None:
    """'实分析' 应映射到 real_analysis 而非 '分析'→analysis。"""
    spec = DISCIPLINE_REGISTRY.get(Discipline.MATH)
    assert spec.infer_branch("实分析引论", "", "") == "real_analysis"
