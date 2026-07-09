"""VerificationPipeline G4 数值验证（generate_numerical_test + sandbox 集成）

mock LLM 只负责返回验证代码；沙箱真实执行（用纯 print 代码验证解析逻辑，不依赖 sympy）。
"""

from __future__ import annotations

from typing import Any

import pytest

from sciforge.agents.qgen.verification_sandbox import TrustPolicy, VerificationSandbox
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
        sandbox=VerificationSandbox(),
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
        sandbox=VerificationSandbox(),
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
        sandbox=VerificationSandbox(),
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
        sandbox=VerificationSandbox(),
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
