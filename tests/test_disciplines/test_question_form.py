"""题目类别 / 形式双维度（Phase 4 #5）回归测试"""

from __future__ import annotations

from sciforge.core.models import Discipline, Question
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.pipeline.llm_schemas import SingleItemExtractLLM


def test_spec_has_form_dimension() -> None:
    spec = DISCIPLINE_REGISTRY.get(Discipline.MATH)
    # 形式维度独立存在
    assert "choice" in spec.allowed_question_forms
    assert "fill_blank" in spec.allowed_question_forms
    assert "subjective" in spec.allowed_question_forms
    # 类别维度含细分证明类型
    assert "counterexample" in spec.allowed_question_types
    assert "constructive_proof" in spec.allowed_question_types


def test_question_form_or_validation() -> None:
    spec = DISCIPLINE_REGISTRY.get(Discipline.MATH)
    assert spec.question_form_or("choice") == "choice"
    assert spec.question_form_or("不存在") == ""  # 默认 fallback 空
    assert spec.question_form_or("不存在", "subjective") == "subjective"


def test_extract_item_accepts_question_form() -> None:
    item = SingleItemExtractLLM(
        category="question",
        item_type="exercise",
        question_form="choice",
        number="5",
        corrected_content="下列正确的是 A. ... B. ...",
        corrected_solution="解：选 A",
        solution_status="complete",
        start_line=1,
        end_line=5,
    )
    assert item.question_form == "choice"


def test_question_form_defaults_empty() -> None:
    item = SingleItemExtractLLM(
        category="knowledge",
        item_type="definition",
        number="1.1",
        corrected_content="定义...",
        solution_status="no_solution_in_source",
        start_line=1,
        end_line=2,
    )
    assert item.question_form == ""


def test_question_form_orthogonal_to_type() -> None:
    """选择题在形式上是 choice，认知类别上可以是 calculation（正交）。"""
    q = Question(
        question_type="calculation",
        question_form="choice",
        content="计算下列哪个等于 1: A. ...",
        discipline=Discipline.MATH,
    )
    assert q.question_type == "calculation"
    assert q.question_form == "choice"
