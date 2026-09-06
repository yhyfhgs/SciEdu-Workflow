"""VerificationPipeline G4 数值验证（generate_numerical_test + sandbox 集成）

mock LLM 只负责返回验证代码；沙箱返回记录式执行结果，验证解析逻辑；不执行模型生成的代码。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from sciforge.agents.qgen.verification_sandbox import (
    IsolationLevel,
    SandboxStatus,
    TrustPolicy,
    VerificationExecutionResult,
    VerificationSandbox,
)
from sciforge.agents.qgen.verifier import VerificationPipeline
from sciforge.core.models import (
    Discipline,
    Question,
    Solution,
    VerificationEvidenceStatus,
)


class _LLM:
    """按 prompt 区分 review / relevance / numerical 响应"""

    def __init__(self, numerical: dict[str, Any]) -> None:
        self._numerical = numerical

    async def generate_json(self, prompt: str, **kw: Any) -> Any:
        if "验证代码" in prompt or "sympy" in prompt:
            return self._numerical
        if "尝试回答" in prompt:
            return {"relevant": True}
        return {"verdict": "PASS"}

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


def _sandbox(code: str) -> VerificationSandbox:
    stdout = {
        'print("PASS")\nprint("PASS")\nprint("PASS")\nprint("ALL_PASSED")': (
            "PASS\nPASS\nPASS\nALL_PASSED"
        ),
        'print("FAIL: counterexample x=2")\nprint("SOME_FAILED: 1/3")': (
            "FAIL: counterexample x=2\nSOME_FAILED: 1/3"
        ),
        'print("ALL_PASSED")': "ALL_PASSED",
    }.get(code, "")
    container = MagicMock()
    container.run.return_value = VerificationExecutionResult(
        status=SandboxStatus.SUCCEEDED if stdout else SandboxStatus.FAILED,
        backend="fixture:container",
        isolation=IsolationLevel.RESTRICTED_CONTAINER,
        trust_policy=TrustPolicy.CONTAINER_REQUIRED,
        stdout=stdout,
        exit_code=0 if stdout else 1,
    )
    local = MagicMock()
    local.run.side_effect = AssertionError("Generated code must never run locally")
    return VerificationSandbox(container_python=container, local_python=local)


def _q() -> Question:
    return Question(
        discipline=Discipline.MATH,
        content="Compute the derivative of $f(x)=x^2$ at $x=3$ and show it equals 6.",
    )


def _sol() -> Solution:
    return Solution(solution_latex="f'(x)=2x, so f'(3)=6.", confidence="high")


@pytest.mark.asyncio
async def test_g4_all_passed() -> None:
    code = 'print("PASS")\nprint("PASS")\nprint("PASS")\nprint("ALL_PASSED")'
    pipe = VerificationPipeline(
        review_llm=_LLM({"verification_code": code}),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
        enable_numerical_verification=True,
        sandbox=_sandbox(code),
        sandbox_trust_policy=TrustPolicy.TRUSTED_LOCAL,
    )
    result = await pipe.verify(_q(), _sol())
    assert result.checks["numerical_valid"] is True
    assert result.numerical_verification is not None
    assert result.numerical_verification.all_passed is True
    assert result.numerical_verification.execution is not None
    assert result.numerical_verification.execution.success is True


@pytest.mark.asyncio
async def test_g4_some_failed_rejects() -> None:
    code = 'print("FAIL: counterexample x=2")\nprint("SOME_FAILED: 1/3")'
    pipe = VerificationPipeline(
        review_llm=_LLM({"verification_code": code}),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
        enable_numerical_verification=True,
        sandbox=_sandbox(code),
        sandbox_trust_policy=TrustPolicy.TRUSTED_LOCAL,
    )
    result = await pipe.verify(_q(), _sol())
    assert result.checks["numerical_valid"] is False
    assert result.passed is False
    assert result.numerical_verification is not None
    assert any("counterexample" in d for d in result.numerical_verification.discrepancies)


@pytest.mark.asyncio
async def test_g4_exec_error_is_explicit_optional_evidence_not_fake_pass() -> None:
    """验证器崩溃记 error；可选检查不误杀，但绝不伪装成 numerical_valid。"""
    code = 'raise ValueError("boom in verification code")'
    pipe = VerificationPipeline(
        review_llm=_LLM({"verification_code": code}),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
        enable_numerical_verification=True,
        sandbox=_sandbox(code),
        sandbox_trust_policy=TrustPolicy.TRUSTED_LOCAL,
    )
    result = await pipe.verify(_q(), _sol())
    assert "numerical_valid" not in result.checks
    assert result.passed is True
    assert result.numerical_verification is not None
    assert result.numerical_verification.execution is not None
    assert result.numerical_verification.execution.success is False
    assert result.evidence[-1].status is VerificationEvidenceStatus.ERROR


@pytest.mark.asyncio
async def test_g4_required_exec_error_blocks_publication() -> None:
    pipe = VerificationPipeline(
        review_llm=_LLM({"verification_code": "raise RuntimeError('boom')"}),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
        enable_numerical_verification=True,
        require_numerical_verification=True,
        sandbox=_sandbox("raise RuntimeError('boom')"),
        sandbox_trust_policy=TrustPolicy.TRUSTED_LOCAL,
    )

    result = await pipe.verify(_q(), _sol())

    assert result.checks["numerical_valid"] is False
    assert result.passed is False


@pytest.mark.asyncio
async def test_g4_disabled_by_default() -> None:
    pipe = VerificationPipeline(
        review_llm=_LLM({"verification_code": "x"}),  # type: ignore[arg-type]
        discipline=Discipline.MATH,
    )
    result = await pipe.verify(_q(), _sol())
    assert "numerical_valid" not in result.checks
    assert result.numerical_verification is None


@pytest.mark.asyncio
async def test_g4_required_plain_only_solution_is_not_skipped() -> None:
    code = 'print("ALL_PASSED")'
    pipe = VerificationPipeline(
        review_llm=_LLM({"verification_code": code}),  # type: ignore[arg-type]
        enable_numerical_verification=True,
        require_numerical_verification=True,
        sandbox=_sandbox(code),
        sandbox_trust_policy=TrustPolicy.TRUSTED_LOCAL,
    )
    solution = Solution(solution_plain="The derivative at 3 equals 6.", confidence="high")

    result = await pipe.verify(_q(), solution)

    assert result.checks["numerical_valid"] is True


@pytest.mark.asyncio
async def test_g4_required_missing_solution_creates_error_and_blocks() -> None:
    pipe = VerificationPipeline(
        review_llm=_LLM({"verification_code": 'print("ALL_PASSED")'}),  # type: ignore[arg-type]
        enable_solution_relevance=False,
        enable_numerical_verification=True,
        require_numerical_verification=True,
    )

    result = await pipe.verify(_q(), Solution(confidence="high"))

    assert result.checks["numerical_valid"] is False
    evidence = next(item for item in result.evidence if item.kind == "numerical")
    assert evidence.status is VerificationEvidenceStatus.ERROR


def test_g4_required_cannot_be_disabled() -> None:
    with pytest.raises(ValueError, match="requires enable_numerical"):
        VerificationPipeline(  # type: ignore[arg-type]
            review_llm=_LLM({}),
            require_numerical_verification=True,
        )
