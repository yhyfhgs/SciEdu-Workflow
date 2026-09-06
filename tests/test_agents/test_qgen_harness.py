"""QGenAgent HarnessGraph 图驱动 + 进化闭环测试

用 stub 子组件精确控制每节点行为，验证状态机路由、进化回边（diagnose→evolve→solve）、
熔断与 trace 记录。不依赖真实 LLM。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from sciforge.agents.qgen.agent import QGenAgent, QGenInput
from sciforge.core.models import (
    CandidateRecord,
    ContextBundle,
    DifficultyScore,
    Discipline,
    GenerationRequest,
    Question,
    Solution,
    VariantStrategy,
    VerificationResult,
)


def _agent(*, enable_evolution: bool, max_improvement_rounds: int = 2) -> QGenAgent:
    agent = QGenAgent(
        llm=MagicMock(),
        storage=MagicMock(),
        discipline=Discipline.MATH,
        max_rounds=2,
        candidates_per_round=1,
        enable_evolution=enable_evolution,
        max_improvement_rounds=max_improvement_rounds,
    )
    # stub 检索（不触真实 storage）
    agent.retriever.build_context = lambda req, disc: ContextBundle()  # type: ignore[assignment,method-assign]
    return agent


def _request() -> GenerationRequest:
    return GenerationRequest(
        discipline=Discipline.MATH,
        topics=["math:数学"],
        difficulty_target=5,
        question_type="proof",
        strategy=VariantStrategy.TOPIC_GENERATION,
    )


def _question(content: str) -> Question:
    return Question(discipline=Discipline.MATH, question_type="proof", content=content)


async def _gen_one(req: Any, ctx: Any, n: int = 1) -> list[Question]:
    return [
        _question(
            "原始候选题目内容，用于测试图驱动状态机与进化闭环，这里需要足够长的中文以通过结构预筛的最小长度限制。"
        )
    ]


async def _solve_high(cand: Any, ctx: Any) -> Solution:
    return Solution(solution_latex="解答过程足够。", confidence="high")


@pytest.mark.asyncio
async def test_happy_path_success_with_trace() -> None:
    """首个候选直接验证通过 → 成功产出，trace 记录各节点，不触发 evolve"""
    agent = _agent(enable_evolution=True)
    agent.generator.generate_candidates = _gen_one  # type: ignore[assignment,method-assign]
    agent.solver.solve = _solve_high  # type: ignore[assignment,method-assign]

    async def _verify_pass(c: Any, s: Any, ctx: Any) -> VerificationResult:
        return VerificationResult(passed=True, checks={"review_passed": True})

    async def _annotate(c: Any, s: Any) -> DifficultyScore:
        return DifficultyScore()

    agent.verifier.verify = _verify_pass  # type: ignore[assignment,method-assign]
    agent.annotator.annotate = _annotate  # type: ignore[assignment,method-assign]
    agent.evolver.evolve = MagicMock()  # 不应被调用

    out = await agent.run(QGenInput(request=_request()))

    assert out.generated is not None
    assert out.generated.question.is_generated is True  # 生成元数据已标记
    assert out.trace is not None
    assert out.trace.outcome == "success"
    assert [nt.node for nt in out.trace.nodes][:3] == ["retrieve", "generate", "next"]
    assert not any(nt.node == "evolve" for nt in out.trace.nodes)
    agent.evolver.evolve.assert_not_called()


@pytest.mark.asyncio
async def test_evolution_loop_closes() -> None:
    """首验失败 → diagnose → evolve → solve → 复验通过：进化闭环真的闭合"""
    agent = _agent(enable_evolution=True)
    agent.generator.generate_candidates = _gen_one  # type: ignore[assignment,method-assign]
    agent.solver.solve = _solve_high  # type: ignore[assignment,method-assign]

    verify_calls = {"n": 0}

    async def _verify(c: Any, s: Any, ctx: Any) -> VerificationResult:
        verify_calls["n"] += 1
        if verify_calls["n"] == 1:
            return VerificationResult(
                passed=False, checks={"review_passed": False}, suggestions="补充条件"
            )
        return VerificationResult(passed=True, checks={"review_passed": True})

    async def _evolve(cand: Any, **kw: Any) -> CandidateRecord:
        return CandidateRecord(
            question=_question(
                "改进后的候选题目内容：补充必要条件并明确变量范围，这里同样需要足够长以通过结构预筛的最小长度限制。"
            ),
            parent_id=cand.id,
            generation=int(kw.get("parent_generation", 0)) + 1,
            improvement_strategy=str(kw.get("failure_kind", "")),
        )

    async def _annotate(c: Any, s: Any) -> DifficultyScore:
        return DifficultyScore()

    agent.verifier.verify = _verify  # type: ignore[assignment,method-assign]
    agent.evolver.evolve = _evolve  # type: ignore[assignment,method-assign]
    agent.annotator.annotate = _annotate  # type: ignore[assignment,method-assign]

    out = await agent.run(QGenInput(request=_request()))

    assert out.generated is not None  # 进化后通过
    assert verify_calls["n"] == 2  # 原始失败 + 进化后通过
    assert any(nt.node == "evolve" for nt in out.trace.nodes)  # 进化回边被走
    # 进化件应是改进后的内容
    assert "改进后" in out.generated.question.content


@pytest.mark.asyncio
async def test_evolution_disabled_no_evolve() -> None:
    """enable_evolution=False：验证失败不进化，退回原 for 循环语义（换候选/下一轮）"""
    agent = _agent(enable_evolution=False)
    agent.generator.generate_candidates = _gen_one  # type: ignore[assignment,method-assign]
    agent.solver.solve = _solve_high  # type: ignore[assignment,method-assign]

    async def _verify_fail(c: Any, s: Any, ctx: Any) -> VerificationResult:
        return VerificationResult(passed=False, checks={"review_passed": False}, suggestions="x")

    agent.verifier.verify = _verify_fail  # type: ignore[assignment,method-assign]
    agent.evolver.evolve = MagicMock()  # 不应被调用

    out = await agent.run(QGenInput(request=_request()))

    assert out.generated is None  # 始终失败
    assert out.trace is not None and out.trace.outcome == "fail"
    assert not any(nt.node == "evolve" for nt in out.trace.nodes)
    agent.evolver.evolve.assert_not_called()


@pytest.mark.asyncio
async def test_evolution_exhausts_then_gives_up() -> None:
    """验证始终失败 + 进化耗尽 max_improvement_rounds → 放弃该候选，最终 fail（不死循环）"""
    agent = _agent(enable_evolution=True, max_improvement_rounds=2)
    agent.generator.generate_candidates = _gen_one  # type: ignore[assignment,method-assign]
    agent.solver.solve = _solve_high  # type: ignore[assignment,method-assign]

    async def _verify_fail(c: Any, s: Any, ctx: Any) -> VerificationResult:
        return VerificationResult(passed=False, checks={"review_passed": False}, suggestions="x")

    evolve_calls = {"n": 0}

    async def _evolve(cand: Any, **kw: Any) -> CandidateRecord:
        evolve_calls["n"] += 1
        return CandidateRecord(
            question=_question(
                f"第{evolve_calls['n']}次改进后的候选题目内容，补充条件使其更严谨，"
                "这里需要足够长以通过结构预筛的最小长度限制要求。"
            ),
            parent_id=cand.id,
            generation=int(kw.get("parent_generation", 0)) + 1,
        )

    agent.verifier.verify = _verify_fail  # type: ignore[assignment,method-assign]
    agent.evolver.evolve = _evolve  # type: ignore[assignment,method-assign]

    out = await agent.run(QGenInput(request=_request()))

    assert out.generated is None
    # 每候选最多进化 max_improvement_rounds=2 次；2 轮 × 1 候选 → 至多 4 次，未死循环
    assert evolve_calls["n"] <= 4
    assert out.trace is not None and out.trace.outcome == "fail"
