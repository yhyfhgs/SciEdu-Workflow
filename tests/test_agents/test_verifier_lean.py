from __future__ import annotations

from typing import Any

import pytest

from sciforge.agents.qgen.verification_sandbox import (
    IsolationLevel,
    SandboxStatus,
    TrustPolicy,
    VerificationExecutionResult,
)
from sciforge.agents.qgen.verifier import VerificationPipeline
from sciforge.core.models import (
    Discipline,
    Question,
    Solution,
    VerificationEvidenceStatus,
)


class _LLM:
    async def generate_json(self, prompt: str, **_kwargs: Any) -> dict[str, Any]:
        if "Lean 4" in prompt:
            return {
                "lean_source": "import Mathlib\ntheorem identity (n : Nat) : n = n := rfl",
                "translation_assumptions": [],
            }
        if "尝试回答" in prompt:
            return {"relevant": True}
        return {"verdict": "PASS", "issues": [], "suggestions": ""}


class _Sandbox:
    def __init__(self, result: VerificationExecutionResult) -> None:
        self.result = result
        self.sources: list[str] = []

    async def averify_lean(
        self, source: str, *, trust_policy: TrustPolicy | str
    ) -> VerificationExecutionResult:
        self.sources.append(source)
        return self.result


def _question() -> Question:
    return Question(
        discipline=Discipline.MATH,
        question_type="proof",
        content=(
            "Prove that every natural number is equal to itself, with all quantifiers explicit."
        ),
    )


def _solution() -> Solution:
    return Solution(solution_latex="For arbitrary $n$, reflexivity gives $n=n$.", confidence="high")


def _execution(status: SandboxStatus) -> VerificationExecutionResult:
    return VerificationExecutionResult(
        status=status,
        backend="container:docker",
        isolation=(
            IsolationLevel.NONE
            if status is SandboxStatus.UNAVAILABLE
            else IsolationLevel.RESTRICTED_CONTAINER
        ),
        trust_policy=TrustPolicy.CONTAINER_REQUIRED,
        exit_code=0 if status is SandboxStatus.SUCCEEDED else 1,
        reason="runtime missing" if status is SandboxStatus.UNAVAILABLE else "",
    )


@pytest.mark.asyncio
async def test_required_lean_success_is_structured_evidence() -> None:
    sandbox = _Sandbox(_execution(SandboxStatus.SUCCEEDED))
    pipeline = VerificationPipeline(
        review_llm=_LLM(),  # type: ignore[arg-type]
        enable_lean_verification=True,
        require_lean_verification=True,
        sandbox=sandbox,  # type: ignore[arg-type]
    )

    result = await pipeline.verify(_question(), _solution())

    assert result.checks["lean_valid"] is True
    assert result.passed is True
    lean = next(evidence for evidence in result.evidence if evidence.kind == "lean")
    assert lean.status is VerificationEvidenceStatus.PASSED
    assert lean.required is True
    assert "theorem identity" in lean.artifact
    assert sandbox.sources == [lean.artifact]


@pytest.mark.asyncio
async def test_required_lean_unavailable_blocks_but_optional_does_not_fake_pass() -> None:
    required = VerificationPipeline(
        review_llm=_LLM(),  # type: ignore[arg-type]
        enable_lean_verification=True,
        require_lean_verification=True,
        sandbox=_Sandbox(_execution(SandboxStatus.UNAVAILABLE)),  # type: ignore[arg-type]
    )
    optional = VerificationPipeline(
        review_llm=_LLM(),  # type: ignore[arg-type]
        enable_lean_verification=True,
        require_lean_verification=False,
        sandbox=_Sandbox(_execution(SandboxStatus.UNAVAILABLE)),  # type: ignore[arg-type]
    )

    required_result = await required.verify(_question(), _solution())
    optional_result = await optional.verify(_question(), _solution())

    assert required_result.checks["lean_valid"] is False
    assert required_result.passed is False
    assert "lean_valid" not in optional_result.checks
    assert optional_result.passed is True
    optional_evidence = next(
        evidence for evidence in optional_result.evidence if evidence.kind == "lean"
    )
    assert optional_evidence.status is VerificationEvidenceStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_required_lean_plain_only_solution_is_not_skipped() -> None:
    sandbox = _Sandbox(_execution(SandboxStatus.SUCCEEDED))
    pipeline = VerificationPipeline(
        review_llm=_LLM(),  # type: ignore[arg-type]
        enable_lean_verification=True,
        require_lean_verification=True,
        sandbox=sandbox,  # type: ignore[arg-type]
    )
    solution = Solution(solution_plain="For every n, reflexivity gives n = n.", confidence="high")

    result = await pipeline.verify(_question(), solution)

    assert result.checks["lean_valid"] is True
    assert sandbox.sources


@pytest.mark.asyncio
async def test_required_lean_missing_solution_creates_error_and_blocks() -> None:
    pipeline = VerificationPipeline(
        review_llm=_LLM(),  # type: ignore[arg-type]
        enable_solution_relevance=False,
        enable_lean_verification=True,
        require_lean_verification=True,
        sandbox=_Sandbox(_execution(SandboxStatus.SUCCEEDED)),  # type: ignore[arg-type]
    )

    result = await pipeline.verify(_question(), Solution(confidence="high"))

    assert result.checks["lean_valid"] is False
    evidence = next(item for item in result.evidence if item.kind == "lean")
    assert evidence.status is VerificationEvidenceStatus.ERROR


def test_required_lean_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="requires enable_lean"):
        VerificationPipeline(  # type: ignore[arg-type]
            review_llm=_LLM(),
            require_lean_verification=True,
        )
