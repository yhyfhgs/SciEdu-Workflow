"""VerificationPipeline G5 解答相关性检查（solution_relevance 接线）"""

from __future__ import annotations

from typing import Any

import pytest

from sciforge.agents.qgen.verifier import VerificationPipeline
from sciforge.core.models import Discipline, Question, Solution


class _PromptAwareLLM:
    """按 prompt 内容区分返回 review / relevance 响应，并计数 relevance 调用"""

    def __init__(self, review: dict[str, Any], relevance: dict[str, Any]) -> None:
        self._review = review
        self._relevance = relevance
        self.relevance_calls = 0
        self.prompts: list[str] = []

    async def generate_json(self, prompt: str, **kw: Any) -> Any:
        self.prompts.append(prompt)
        if "尝试回答" in prompt or '"relevant"' in prompt:
            self.relevance_calls += 1
            return self._relevance
        return self._review

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


_REVIEW_PASS = {"verdict": "PASS", "issues": [], "suggestions": "无"}


def _q() -> Question:
    return Question(
        discipline=Discipline.MATH,
        content="Let $f(x)=x^2$. Compute $f'(x)$ using the definition of the derivative.",
    )


def _sol(latex: str = "By definition f'(x) = 2x.") -> Solution:
    return Solution(solution_latex=latex, confidence="high")


@pytest.mark.asyncio
async def test_g5_relevant_passes() -> None:
    llm = _PromptAwareLLM(_REVIEW_PASS, {"relevant": True, "reason": "在解导数题"})
    pipe = VerificationPipeline(review_llm=llm, discipline=Discipline.MATH)  # 默认开 G5
    result = await pipe.verify(_q(), _sol())
    assert llm.relevance_calls == 1
    assert result.checks["solution_relevant"] is True
    assert result.passed is True


@pytest.mark.asyncio
async def test_independent_review_receives_question_and_full_solution() -> None:
    llm = _PromptAwareLLM(_REVIEW_PASS, {"relevant": True})
    solution = _sol("First derive the limit, then obtain $f'(x)=2x$.")
    await VerificationPipeline(review_llm=llm).verify(_q(), solution)  # type: ignore[arg-type]

    review_prompt = next(prompt for prompt in llm.prompts if "待审核解答" in prompt)
    assert _q().content in review_prompt
    assert solution.solution_latex in review_prompt


@pytest.mark.asyncio
async def test_g5_irrelevant_fails() -> None:
    """解答答非所问 → solution_relevant=False → 整体不通过"""
    llm = _PromptAwareLLM(_REVIEW_PASS, {"relevant": False, "reason": "答非所问"})
    pipe = VerificationPipeline(review_llm=llm, discipline=Discipline.MATH)
    result = await pipe.verify(_q(), _sol())
    assert result.checks["solution_relevant"] is False
    assert result.passed is False


@pytest.mark.asyncio
async def test_g5_disabled_skips_check() -> None:
    llm = _PromptAwareLLM(_REVIEW_PASS, {"relevant": False})
    pipe = VerificationPipeline(
        review_llm=llm, discipline=Discipline.MATH, enable_solution_relevance=False
    )
    result = await pipe.verify(_q(), _sol())
    assert llm.relevance_calls == 0
    assert "solution_relevant" not in result.checks
    assert result.passed is True


@pytest.mark.asyncio
async def test_g5_empty_solution_is_explicit_error() -> None:
    """无解答文本时不调相关性 LLM，但必须形成 error 门禁。"""
    llm = _PromptAwareLLM(_REVIEW_PASS, {"relevant": False})
    pipe = VerificationPipeline(review_llm=llm, discipline=Discipline.MATH)
    result = await pipe.verify(_q(), _sol(latex=""))
    assert llm.relevance_calls == 0
    assert result.checks["solution_relevant"] is False
    assert result.passed is False
    evidence = next(item for item in result.evidence if item.kind == "solution_relevance")
    assert evidence.status.value == "error"


@pytest.mark.asyncio
async def test_g5_missing_field_is_explicit_error() -> None:
    """relevance 响应缺 relevant 字段 → error，不能伪装为通过。"""
    llm = _PromptAwareLLM(_REVIEW_PASS, {"reason": "缺 relevant 键"})
    pipe = VerificationPipeline(review_llm=llm, discipline=Discipline.MATH)
    result = await pipe.verify(_q(), _sol())
    assert result.checks["solution_relevant"] is False
    assert result.passed is False
    evidence = next(item for item in result.evidence if item.kind == "solution_relevance")
    assert evidence.status.value == "error"


@pytest.mark.asyncio
async def test_g5_plain_only_solution_is_verified() -> None:
    llm = _PromptAwareLLM(_REVIEW_PASS, {"relevant": True})
    solution = Solution(solution_plain="Differentiate by the limit definition.", confidence="high")

    result = await VerificationPipeline(review_llm=llm).verify(  # type: ignore[arg-type]
        _q(), solution
    )

    assert result.checks["solution_relevant"] is True
    assert any(solution.solution_plain in prompt for prompt in llm.prompts)


@pytest.mark.asyncio
async def test_g5_infrastructure_error_is_explicit_and_blocks() -> None:
    class _FailingRelevanceLLM(_PromptAwareLLM):
        async def generate_json(self, prompt: str, **kw: Any) -> Any:
            if "尝试回答" in prompt or '"relevant"' in prompt:
                raise RuntimeError("relevance backend down")
            return await super().generate_json(prompt, **kw)

    llm = _FailingRelevanceLLM(_REVIEW_PASS, {"relevant": True})
    result = await VerificationPipeline(review_llm=llm).verify(  # type: ignore[arg-type]
        _q(), _sol()
    )

    assert result.checks["solution_relevant"] is False
    evidence = next(item for item in result.evidence if item.kind == "solution_relevance")
    assert evidence.status.value == "error"
    assert "backend down" in evidence.summary
