"""User evaluation focus augments review without replacing hard checks."""

from __future__ import annotations

from typing import Any

import pytest

from sciforge.agents.qgen.verifier import VerificationPipeline
from sciforge.core.models import Discipline, Question, Solution


class _CapturingReviewLLM:
    model = "review-test"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate_json(self, prompt: str, **_kwargs: Any) -> dict[str, object]:
        self.prompts.append(prompt)
        return {"verdict": "PASS", "issues": [], "suggestions": ""}


@pytest.mark.asyncio
async def test_review_instruction_is_bounded_dynamic_context() -> None:
    llm = _CapturingReviewLLM()
    verifier = VerificationPipeline(
        llm,  # type: ignore[arg-type]
        Discipline.MATH,
        enable_solution_relevance=False,
    )
    question = Question(
        content="设函数在闭区间连续，请从开覆盖与有限子覆盖出发，完整证明它在该区间上一致连续。"
    )
    solution = Solution(
        solution_latex="由紧致性取得有限子覆盖，再取其中半径的最小值即可完成证明。",
        confidence="high",
    )
    instruction = "重点检查有限子覆盖到统一 delta 的量词顺序。"

    result = await verifier.verify(
        question,
        solution,
        review_instruction=instruction,
    )

    assert result.passed is True
    assert instruction in llm.prompts[0]
    assert "不得放宽既有正确性与证据门禁" in llm.prompts[0]
