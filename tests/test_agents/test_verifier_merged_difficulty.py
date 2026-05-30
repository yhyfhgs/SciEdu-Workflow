"""VerificationPipeline — Phase 6c 合并 difficulty 到 verify"""

from __future__ import annotations

from typing import Any

import pytest

from sciforge.agents.qgen.verifier import VerificationPipeline
from sciforge.core.models import Discipline, Question, Solution


class _MockReviewLLM:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    async def generate_json(self, prompt: str, **kw: Any) -> Any:
        return self._response

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


def _q() -> Question:
    return Question(
        discipline=Discipline.MATH,
        content=(
            "Let $f(x) = x^2 + 2x + 1$. Prove that $f$ is differentiable "
            "on $\\mathbb{R}$ and compute $f'(x)$ using the definition."
        ),
    )


def _sol() -> Solution:
    return Solution(
        content="Proof: By definition, f'(x) = lim ... = 2x+2.",
        confidence="high",
    )


@pytest.mark.asyncio
async def test_pass_with_difficulty_scores_populates_score() -> None:
    review_response = {
        "verdict": "PASS",
        "issues": [],
        "suggestions": "无",
        "difficulty_scores": {
            "concept_count": 4,
            "step_count": 5,
            "abstraction_level": 3,
            "technique_novelty": 4,
            "condition_parsing": 3,
            "computation_load": 4,
            "proof_structure": 5,
            "prerequisite_depth": 4,
            "multi_representation": 3,
            "metacognition": 4,
        },
        "difficulty_reasoning": "Standard differentiability proof with limit definition.",
    }
    pipeline = VerificationPipeline(
        review_llm=_MockReviewLLM(review_response),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
    )
    result = await pipeline.verify(_q(), _sol())

    assert result.passed is True
    assert result.difficulty_score is not None
    assert result.difficulty_score.concept_count == 4
    assert result.difficulty_score.step_count == 5
    assert result.difficulty_score.overall > 0.0
    assert "differentiability" in result.difficulty_score.reasoning.lower()


@pytest.mark.asyncio
async def test_pass_without_scores_no_difficulty() -> None:
    """PASS 但 prompt 回应没有 difficulty_scores 字段（旧模板回退路径）"""
    review_response = {
        "verdict": "PASS",
        "issues": [],
        "suggestions": "无",
    }
    pipeline = VerificationPipeline(
        review_llm=_MockReviewLLM(review_response),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
    )
    result = await pipeline.verify(_q(), _sol())

    assert result.passed is True
    assert result.difficulty_score is None  # 无 scores → 走原路径调 annotator


@pytest.mark.asyncio
async def test_fail_ignores_difficulty_scores() -> None:
    """FAIL 时即使有 difficulty_scores 也不填（因为题目不通过无意义）"""
    review_response = {
        "verdict": "FAIL",
        "issues": ["矛盾条件"],
        "suggestions": "修改条件",
        "difficulty_scores": {"concept_count": 5},
    }
    pipeline = VerificationPipeline(
        review_llm=_MockReviewLLM(review_response),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
    )
    result = await pipeline.verify(_q(), _sol())

    assert result.passed is False
    assert result.difficulty_score is None


@pytest.mark.asyncio
async def test_malformed_difficulty_scores_graceful_fallback() -> None:
    """difficulty_scores 字段类型错误时不崩溃，填 None"""
    review_response = {
        "verdict": "PASS",
        "issues": [],
        "suggestions": "无",
        "difficulty_scores": {"concept_count": "not an int"},
    }
    pipeline = VerificationPipeline(
        review_llm=_MockReviewLLM(review_response),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
    )
    result = await pipeline.verify(_q(), _sol())

    assert result.passed is True
    assert result.difficulty_score is None


@pytest.mark.asyncio
async def test_partial_scores_uses_defaults() -> None:
    """只提供部分维度时，其他维度用默认值 5"""
    review_response = {
        "verdict": "PASS",
        "issues": [],
        "suggestions": "无",
        "difficulty_scores": {
            "concept_count": 7,
            "step_count": 8,
        },
    }
    pipeline = VerificationPipeline(
        review_llm=_MockReviewLLM(review_response),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
    )
    result = await pipeline.verify(_q(), _sol())

    assert result.difficulty_score is not None
    assert result.difficulty_score.concept_count == 7
    assert result.difficulty_score.step_count == 8
    # 未指定的维度默认 5
    assert result.difficulty_score.abstraction_level == 5
    assert result.difficulty_score.metacognition == 5
