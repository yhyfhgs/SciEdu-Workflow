"""Startup recovery and explicit paused-run resume API contracts."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from sciforge.agents import QGenOutput, SolveOutput
from sciforge.agents.runtime import (
    BudgetExceededError,
    RunBudget,
    RunStatus,
    SQLiteRunStore,
    StepStatus,
)
from sciforge.api.app import create_app
from sciforge.api.conversations import ConversationStore
from sciforge.core.models import (
    Discipline,
    GenerationRequest,
    Question,
    Solution,
    VariantStrategy,
    VerificationResult,
)
from sciforge.storage.saas import SaasStore


class _RecoverableQGenFacade:
    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.inputs: list[Any] = []

    async def run(self, input: Any) -> QGenOutput:
        self.inputs.append(input)
        if self.delay:
            await asyncio.sleep(self.delay)
        return QGenOutput(
            rounds_used=1,
            candidates_tried=0,
            reason="recovered without a publishable candidate",
        )


class _RecoverableSolveFacade:
    async def run(self, input: Any) -> SolveOutput:
        question = input.question
        return SolveOutput(
            solution=Solution(
                question_id=question.id,
                solution_plain="recovered solution",
                confidence="high",
            ),
            confidence="high",
        )


def _wait_for_status(
    runs: SQLiteRunStore,
    run_id: str,
    status: RunStatus,
    *,
    timeout: float = 2.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if runs.require_run(run_id).status is status:
            return
        time.sleep(0.01)
    observed = runs.require_run(run_id).status
    raise AssertionError(f"run {run_id} stayed {observed.value}; expected {status.value}")


def _generation_request() -> GenerationRequest:
    return GenerationRequest(
        discipline=Discipline.MATH,
        subject="analysis:分析",
        topics=["compactness:紧致性"],
        difficulty_target=7,
        question_type="proof",
        strategy=VariantStrategy.TOPIC_GENERATION,
    )


def _metadata(tenant_id: str, conversation_id: str, *, mode: str) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "storage_tenant_id": "" if tenant_id == "local" else tenant_id,
        "conversation_id": conversation_id,
        "mode": mode,
        "output_type": "question_solution",
        "verification_policy": "balanced",
    }


def test_startup_recovers_pending_qgen_from_durable_input(tmp_path: Path) -> None:
    conversations = ConversationStore(tmp_path / "conversations.db")
    runs = SQLiteRunStore(tmp_path / "runs.db")
    conversation = conversations.create_conversation("local", discipline="math")
    request = _generation_request()
    run = runs.create_run(
        "qgen",
        request.model_dump(mode="json"),
        metadata=_metadata("local", conversation.conversation_id, mode="evolve"),
    )
    conversations.link_run(conversation.conversation_id, "local", run.run_id)
    facade = _RecoverableQGenFacade()
    app = create_app(
        lambda _discipline: facade,
        lambda _discipline: _RecoverableSolveFacade(),
        conversation_store=conversations,
        run_store=runs,
        serve_console=False,
    )

    with TestClient(app):
        _wait_for_status(runs, run.run_id, RunStatus.SUCCEEDED)

    assert facade.inputs[0].request == request
    event_types = [event.event_type for event in runs.list_events(run.run_id)]
    assert event_types.count("run.recovery_scheduled") == 1
    assert event_types.count("run.started") == 1
    assert event_types.count("assistant.final") == 1
    messages = conversations.list_messages(conversation.conversation_id, "local")
    assert [message.role for message in messages] == ["assistant"]


def test_startup_reconciles_succeeded_product_once_and_ignores_unrelated_runs(
    tmp_path: Path,
) -> None:
    conversations = ConversationStore(tmp_path / "conversations.db")
    runs = SQLiteRunStore(tmp_path / "runs.db")
    conversation = conversations.create_conversation("local", discipline="math")
    request = _generation_request()
    output = QGenOutput(
        rounds_used=2,
        candidates_tried=3,
        reason="completed before product delivery",
    ).model_dump(mode="json")
    run = runs.create_run(
        "qgen",
        request.model_dump(mode="json"),
        metadata=_metadata("local", conversation.conversation_id, mode="new"),
    )
    conversations.link_run(conversation.conversation_id, "local", run.run_id)
    runs.begin_run(run.run_id)
    runs.complete_run(run.run_id, output)

    # A successful durable run without a conversation link is not a product run.
    unrelated = runs.create_run(
        "qgen",
        request.model_dump(mode="json"),
        metadata=_metadata("local", conversation.conversation_id, mode="new"),
    )
    runs.begin_run(unrelated.run_id)
    runs.complete_run(unrelated.run_id, output)

    # Even a linked run is ignored when it is not a supported conversation kind.
    unsupported = runs.create_run(
        "maintenance",
        {"task": "vacuum"},
        metadata=_metadata("local", conversation.conversation_id, mode="new"),
    )
    conversations.link_run(conversation.conversation_id, "local", unsupported.run_id)
    runs.begin_run(unsupported.run_id)
    runs.complete_run(unsupported.run_id, {"ok": True})

    facade = _RecoverableQGenFacade()
    app = create_app(
        lambda _discipline: facade,
        lambda _discipline: _RecoverableSolveFacade(),
        conversation_store=conversations,
        run_store=runs,
        serve_console=False,
    )

    with TestClient(app):
        pass

    messages_after_first = conversations.list_messages(conversation.conversation_id, "local")
    artifacts_after_first = conversations.list_artifacts(conversation.conversation_id, "local")
    final_events_after_first = [
        event for event in runs.list_events(run.run_id) if event.event_type == "assistant.final"
    ]
    assert len(messages_after_first) == 1
    assert "completed before product delivery" in messages_after_first[0].content
    assert len(artifacts_after_first) == 1
    assert artifacts_after_first[0].payload == output
    assert len(final_events_after_first) == 1
    assert facade.inputs == []
    assert not any(
        event.event_type == "assistant.final" for event in runs.list_events(unrelated.run_id)
    )
    assert not any(
        event.event_type == "assistant.final" for event in runs.list_events(unsupported.run_id)
    )

    # A second process startup observes both halves of the stable product pair
    # and the terminal event, so it performs no duplicate writes.
    with TestClient(app):
        pass

    assert conversations.list_messages(conversation.conversation_id, "local") == (
        messages_after_first
    )
    assert conversations.list_artifacts(conversation.conversation_id, "local") == (
        artifacts_after_first
    )
    assert sum(event.event_type == "assistant.final" for event in runs.list_events(run.run_id)) == 1


def test_startup_rebuilds_solve_and_evaluate_products_without_agents(tmp_path: Path) -> None:
    conversations = ConversationStore(tmp_path / "conversations.db")
    runs = SQLiteRunStore(tmp_path / "runs.db")
    conversation = conversations.create_conversation("local", discipline="math")
    question = Question(
        id="delivery-question",
        discipline=Discipline.MATH,
        subject="arithmetic:算术",
        content="计算 6 × 7。",
        difficulty=2,
    )
    solution = Solution(
        question_id=question.id,
        solution_plain="6 × 7 = 42。",
        confidence="high",
    )
    solve_output = SolveOutput(
        solution=solution,
        confidence="high",
    ).model_dump(mode="json")
    solve_run = runs.create_run(
        "solve",
        {"question": question.model_dump(mode="json")},
        metadata=_metadata("local", conversation.conversation_id, mode="solve"),
    )
    conversations.link_run(conversation.conversation_id, "local", solve_run.run_id)
    runs.begin_run(solve_run.run_id)
    runs.complete_run(solve_run.run_id, solve_output)

    verification = VerificationResult(
        passed=True,
        checks={"review_passed": True},
        suggestions="步骤与答案一致。",
    )
    evaluation_output = {
        "question": question.model_dump(mode="json"),
        "solution": solution.model_dump(mode="json"),
        "difficulty": question.difficulty,
        "verification": verification.model_dump(mode="json"),
        "evaluation_instruction": "检查乘法结果。",
    }
    evaluate_run = runs.create_run(
        "evaluate",
        {
            "question": question.model_dump(mode="json"),
            "evaluation_instruction": "检查乘法结果。",
        },
        metadata=_metadata("local", conversation.conversation_id, mode="evaluate"),
    )
    conversations.link_run(conversation.conversation_id, "local", evaluate_run.run_id)
    runs.begin_run(evaluate_run.run_id)
    runs.complete_run(evaluate_run.run_id, evaluation_output)

    qgen_facade = _RecoverableQGenFacade()
    app = create_app(
        lambda _discipline: qgen_facade,
        lambda _discipline: _RecoverableSolveFacade(),
        conversation_store=conversations,
        run_store=runs,
        serve_console=False,
    )
    with TestClient(app):
        pass

    artifacts = {
        artifact.run_id: artifact
        for artifact in conversations.list_artifacts(conversation.conversation_id, "local")
    }
    assert artifacts[solve_run.run_id].kind == "solution"
    assert artifacts[solve_run.run_id].payload["question"]["content"] == "计算 6 × 7。"
    assert artifacts[evaluate_run.run_id].kind == "evaluation"
    assert artifacts[evaluate_run.run_id].payload["verification"]["passed"] is True
    messages = {
        message.run_id: message.content
        for message in conversations.list_messages(conversation.conversation_id, "local")
    }
    assert "6 × 7 = 42" in messages[solve_run.run_id]
    assert "评估结果：通过" in messages[evaluate_run.run_id]
    assert qgen_facade.inputs == []


def test_startup_interrupts_stale_solve_step_and_uses_next_attempt(tmp_path: Path) -> None:
    conversations = ConversationStore(tmp_path / "conversations.db")
    runs = SQLiteRunStore(tmp_path / "runs.db")
    conversation = conversations.create_conversation("local", discipline="math")
    question = Question(
        id="stale-solve-question",
        discipline=Discipline.MATH,
        content="求一个整数。",
    )
    run = runs.create_run(
        "solve",
        {"question": question.model_dump(mode="json")},
        metadata=_metadata("local", conversation.conversation_id, mode="solve"),
    )
    conversations.link_run(conversation.conversation_id, "local", run.run_id)
    runs.begin_run(run.run_id)
    stale = runs.begin_step(run.run_id, "solve", attempt=1)
    app = create_app(
        lambda _discipline: _RecoverableQGenFacade(),
        lambda _discipline: _RecoverableSolveFacade(),
        conversation_store=conversations,
        run_store=runs,
        serve_console=False,
    )

    with TestClient(app):
        _wait_for_status(runs, run.run_id, RunStatus.SUCCEEDED)

    steps = [step for step in runs.list_steps(run.run_id) if step.name == "solve"]
    assert [step.attempt for step in steps] == [1, 2]
    assert [step.status for step in steps] == [StepStatus.INTERRUPTED, StepStatus.SUCCEEDED]
    assert runs.get_step(stale.step_id).error == "service startup recovery"


@pytest.mark.parametrize(
    ("verification_policy", "question_type", "error_fragment"),
    [
        ("invented", "proof", "persisted verification policy is invalid"),
        ("formal", "calculation", "formal 验证只适用于数学证明"),
    ],
)
def test_startup_recovery_fails_closed_for_invalid_verification_contract(
    tmp_path: Path,
    verification_policy: str,
    question_type: str,
    error_fragment: str,
) -> None:
    conversations = ConversationStore(tmp_path / "conversations.db")
    runs = SQLiteRunStore(tmp_path / "runs.db")
    conversation = conversations.create_conversation("local", discipline="math")
    request = _generation_request().model_copy(update={"question_type": question_type})
    metadata = _metadata("local", conversation.conversation_id, mode="new")
    metadata["verification_policy"] = verification_policy
    run = runs.create_run(
        "qgen",
        request.model_dump(mode="json"),
        metadata=metadata,
    )
    conversations.link_run(conversation.conversation_id, "local", run.run_id)
    facade = _RecoverableQGenFacade()
    app = create_app(
        lambda _discipline: facade,
        lambda _discipline: _RecoverableSolveFacade(),
        conversation_store=conversations,
        run_store=runs,
        serve_console=False,
    )

    with TestClient(app):
        pass

    assert facade.inputs == []
    assert runs.require_run(run.run_id).status is RunStatus.PENDING
    failure = next(
        event for event in runs.list_events(run.run_id) if event.event_type == "run.recovery_failed"
    )
    assert error_fragment in str(failure.payload["error"])


def test_paused_resume_is_tenant_scoped_incremental_and_idempotent(tmp_path: Path) -> None:
    conversations = ConversationStore(tmp_path / "conversations.db")
    runs = SQLiteRunStore(tmp_path / "runs.db")
    facade = _RecoverableQGenFacade(delay=0.15)
    with SaasStore(tmp_path / "saas.db") as saas:
        tenant_a = saas.create_tenant("tenant-a")
        tenant_b = saas.create_tenant("tenant-b")
        key_a = saas.create_api_key(tenant_a, scopes=["generate"])
        key_b = saas.create_api_key(tenant_b, scopes=["generate"])
        conversation = conversations.create_conversation(tenant_a, discipline="math")
        request = _generation_request()
        run = runs.create_run(
            "qgen",
            request.model_dump(mode="json"),
            budget=RunBudget(max_steps=10, max_llm_calls=1, max_tokens=1_000),
            metadata=_metadata(tenant_a, conversation.conversation_id, mode="new"),
        )
        conversations.link_run(conversation.conversation_id, tenant_a, run.run_id)
        runs.begin_run(run.run_id)
        runs.begin_step(run.run_id, "qgen_facade", attempt=1)
        with pytest.raises(BudgetExceededError):
            runs.record_usage(run.run_id, llm_calls=2)
        assert runs.require_run(run.run_id).status is RunStatus.PAUSED

        app = create_app(
            lambda _discipline: facade,
            lambda _discipline: _RecoverableSolveFacade(),
            saas_store=saas,
            conversation_store=conversations,
            run_store=runs,
            serve_console=False,
        )
        with TestClient(app) as client:
            # Startup deliberately leaves operator-paused work untouched.
            time.sleep(0.03)
            assert runs.require_run(run.run_id).status is RunStatus.PAUSED
            assert facade.inputs == []

            denied = client.post(
                f"/v1/runs/{run.run_id}/resume",
                json={"budget_increment": {"max_llm_calls": 2}},
                headers={"X-API-Key": key_b},
            )
            first = client.post(
                f"/v1/runs/{run.run_id}/resume",
                json={"budget_increment": {"max_llm_calls": 2}},
                headers={"X-API-Key": key_a},
            )
            duplicate = client.post(
                f"/v1/runs/{run.run_id}/resume",
                json={"budget_increment": {"max_llm_calls": 2}},
                headers={"X-API-Key": key_a},
            )
            _wait_for_status(runs, run.run_id, RunStatus.SUCCEEDED)

    assert denied.status_code == 404
    assert first.status_code == 202
    assert duplicate.status_code == 202
    assert first.json()["resume_scheduled"] is True
    assert duplicate.json()["resume_scheduled"] is True
    assert runs.require_run(run.run_id).budget.max_llm_calls == 3
    assert len(facade.inputs) == 1
    events = runs.list_events(run.run_id)
    assert sum(event.event_type == "run.resume_requested" for event in events) == 1
    assert sum(event.event_type == "run.recovery_scheduled" for event in events) == 1
    attempts = [step.attempt for step in runs.list_steps(run.run_id) if step.name == "qgen_facade"]
    assert attempts == [1, 2]
