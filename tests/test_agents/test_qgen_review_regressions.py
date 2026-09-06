"""Regressions for PR #4: complete problem inputs and generated-code provenance."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from sciforge.agents.qgen.deep_solver import DeepSolveEngine
from sciforge.agents.qgen.evolver import Evolver
from sciforge.agents.qgen.solver import SolveEngine
from sciforge.agents.qgen.verification_sandbox import (
    IsolationLevel,
    SandboxStatus,
    TrustPolicy,
    VerificationExecutionResult,
    VerificationSandbox,
)
from sciforge.agents.qgen.verifier import VerificationPipeline
from sciforge.core.models import Question, Solution, ToolCall


def question():
    return Question(
        content="Select the single correct arithmetic statement from the following options.",
        question_form="choice",
        options=["A. 2+2=4", "B. 2+2=5"],
        correct_option="A",
    )


@pytest.mark.parametrize("deep", [False, True])
async def test_solver_sees_swapped_options_but_not_answer_key(deep):
    llm = MagicMock()
    llm.supports_tools = False
    llm.generate_json = AsyncMock(return_value={"solution_latex": "A", "confidence": "high"})
    engine = DeepSolveEngine(llm) if deep else SolveEngine(llm)
    solve = engine.solve_deep if deep else engine.solve
    first = question()
    second = first.model_copy(update={"options": ["A. 2+2=5", "B. 2+2=4"], "correct_option": "B"})
    await solve(first)
    await solve(second)
    prompts = [call.args[0] for call in llm.generate_json.call_args_list]
    assert prompts[0] != prompts[1]
    assert all(option in prompts[0] for option in first.options)
    assert all(option in prompts[1] for option in second.options)
    llm.generate_json.reset_mock()
    await solve(first.model_copy(update={"correct_option": "DO_NOT_LEAK_KEY"}))
    assert "DO_NOT_LEAK_KEY" not in llm.generate_json.call_args.args[0]


@pytest.mark.parametrize("answer_valid", [True, False, None, "true"])
async def test_answer_key_requires_explicit_independent_review(answer_valid):
    llm = MagicMock()
    llm.generate_json = AsyncMock(
        return_value={"verdict": "PASS", "answer_key_valid": answer_valid}
    )
    verifier = VerificationPipeline(llm, enable_solution_relevance=False)
    q = question()
    result = await verifier.verify(q, Solution(solution_latex="A", confidence="high"))
    assert result.passed is (answer_valid is True)
    prompt = llm.generate_json.call_args.args[0]
    assert all(option in prompt for option in q.options)
    assert "answer_key_valid" in prompt
    assert "候选答案键" in prompt


async def test_evolution_requires_fresh_structured_fields():
    llm = MagicMock()
    llm.generate_json = AsyncMock(return_value={"content": "Changed stem"})
    evolver = Evolver(llm)
    q = question()
    assert await evolver.evolve(q, failure_kind="solver", failure_detail="wrong") is None
    assert all(option in llm.generate_json.call_args.args[0] for option in q.options)
    llm.generate_json.return_value = {
        "content": "Changed stem",
        "options": ["A. False", "B. True"],
        "correct_option": "B",
    }
    evolved = await evolver.evolve(q, failure_kind="verification", failure_detail="wrong key")
    assert evolved.question.options == ["A. False", "B. True"]
    assert evolved.question.correct_option == "B"


def unavailable():
    return VerificationExecutionResult(
        status=SandboxStatus.UNAVAILABLE,
        backend="fixture:no-container",
        isolation=IsolationLevel.NONE,
        trust_policy=TrustPolicy.CONTAINER_REQUIRED,
        reason="Container unavailable",
    )


async def test_generated_python_and_lean_ignore_local_trust_override():
    local = MagicMock()
    local.run.side_effect = AssertionError("Unreviewed code reached host")
    container = MagicMock()
    container.run.return_value = unavailable()
    lean = MagicMock()
    lean.verify.return_value = unavailable()
    sandbox = VerificationSandbox(
        local_python=local, container_python=container, lean_verifier=lean
    )
    llm = MagicMock()
    llm.generate_json = AsyncMock(
        return_value={
            "verification_code": "print('generated')",
            "lean_source": "theorem generated : True := by trivial",
        }
    )
    verifier = VerificationPipeline(
        llm,
        sandbox=sandbox,
        sandbox_trust_policy="trusted_local",
        enable_numerical_verification=True,
        enable_lean_verification=True,
    )
    q, solution = question(), Solution(solution_latex="A", confidence="high")
    _, numerical = await verifier._check_numerical(q, solution, required=True)
    formal = await verifier._check_lean(q, solution, required=True)
    local.run.assert_not_called()
    container.run.assert_called_once_with("print('generated')")
    assert lean.verify.call_args.kwargs["trust_policy"] is TrustPolicy.CONTAINER_REQUIRED
    assert numerical.status.value == formal.status.value == "unavailable"

    async def run_tools(**kwargs):
        result = await kwargs["tool_executor"](
            ToolCall(tool_name="execute_python", arguments={"code": "tool output"})
        )
        assert result.is_error
        return []

    llm.supports_tools = True
    llm.generate_multiturn = AsyncMock(side_effect=run_tools)
    await DeepSolveEngine(llm, sandbox=sandbox, sandbox_trust_policy="trusted_local").solve_deep(q)
    local.run.assert_not_called()
    container.run.assert_called_with("tool output")


def test_fill_blank_render_retains_count_without_solution():
    q = Question(
        content="Fill in both missing values.",
        question_form="fill_blank",
        blanks=2,
        solution_content="DO_NOT_LEAK_SOLUTION",
    )
    assert "Number of blanks: 2" in q.render_problem()
    assert "DO_NOT_LEAK_SOLUTION" not in q.render_problem()


@pytest.mark.parametrize("check", ["relevance", "numerical", "lean"])
async def test_all_optional_verifiers_receive_complete_problem(check):
    llm = MagicMock()
    llm.generate_json = AsyncMock(return_value={"relevant": True})
    verifier = VerificationPipeline(
        llm,
        enable_numerical_verification=True,
        enable_lean_verification=True,
    )
    q, solution = question(), Solution(solution_latex="A", confidence="high")
    if check == "relevance":
        await verifier._check_solution_relevance(q, solution)
    elif check == "numerical":
        await verifier._check_numerical(q, solution, required=True)
    else:
        await verifier._check_lean(q, solution, required=True)
    assert all(option in llm.generate_json.call_args.args[0] for option in q.options)


@pytest.mark.parametrize("output_type", ["question_only", "markdown", "lecture_note"])
def test_conversation_delivery_keeps_options_without_leaking_key(output_type):
    from sciforge.agents.qgen.agent import QGenOutput
    from sciforge.api.conversation_routes import _render_qgen_result
    from sciforge.core.models import DifficultyScore, GeneratedQuestion, VerificationResult

    q = question().model_copy(update={"correct_option": "DO_NOT_LEAK_KEY"})
    result = QGenOutput(
        generated=GeneratedQuestion(
            question=q,
            solution=Solution(solution_latex="Independent solution"),
            difficulty_score=DifficultyScore(),
            verification=VerificationResult(passed=True),
        )
    )
    rendered = _render_qgen_result(result, output_type=output_type)
    assert all(option in rendered for option in q.options)
    assert "DO_NOT_LEAK_KEY" not in rendered
