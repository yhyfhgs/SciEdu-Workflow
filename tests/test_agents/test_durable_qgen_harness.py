from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sciforge.agents.graph import END, StateGraph
from sciforge.agents.qgen.agent import QGenAgent, QGenInput, QGenOutput
from sciforge.agents.qgen.durable_harness import DurableQGenHarness, QGenGraphSnapshot
from sciforge.agents.runtime import (
    BudgetExceededError,
    InvalidRunTransitionError,
    RunBudget,
    RunStatus,
    SQLiteRunStore,
)
from sciforge.core.models import Discipline, GenerationRequest, VariantStrategy
from sciforge.core.trace import GenerationTrace


class _UsageClient:
    call_count = 0
    total_tokens = 0


class _FakeQGen:
    def __init__(self) -> None:
        self.client = _UsageClient()
        self.visited: list[str] = []

    def create_trace(self, request: GenerationRequest) -> GenerationTrace:
        return GenerationTrace(request_summary={"subject": request.subject})

    def validate_request(self, request: GenerationRequest) -> None:
        if request.question_type == "invalid":
            raise ValueError("invalid question contract")

    def create_graph_state(self, request: GenerationRequest) -> dict[str, Any]:
        return QGenAgent.create_graph_state(request)

    def finalize_graph_state(self, final: dict[str, Any], trace: GenerationTrace) -> QGenOutput:
        return QGenAgent.finalize_graph_state(final, trace)

    def llm_clients(self) -> tuple[_UsageClient, ...]:
        return (self.client,)

    def _build_graph(self) -> StateGraph:
        async def retrieve(state: dict[str, Any]) -> None:
            self.visited.append("retrieve")
            state["route"] = "retrieved"

        async def generate(state: dict[str, Any]) -> None:
            self.visited.append("generate")
            self.client.call_count += 1
            self.client.total_tokens += 40
            state["round_num"] += 1
            state["route"] = "generated"

        async def finish(state: dict[str, Any]) -> None:
            self.visited.append("finish")
            state["route"] = "end"
            state["output"] = QGenOutput(
                rounds_used=state["round_num"],
                candidates_tried=0,
                reason="workflow completed without a selected candidate",
            )

        graph = StateGraph()
        graph.add_node("retrieve", retrieve)
        graph.add_node("generate", generate)
        graph.add_node("finish", finish)
        graph.set_entry_point("retrieve")
        graph.add_edge("retrieve", "generate")
        graph.add_edge("generate", "finish")
        graph.add_edge("finish", END)
        return graph


def _input() -> QGenInput:
    return QGenInput(
        request=GenerationRequest(
            discipline=Discipline.MATH,
            subject="analysis",
            topics=["continuity"],
            difficulty_target=6,
            strategy=VariantStrategy.TOPIC_GENERATION,
        )
    )


@pytest.mark.asyncio
async def test_invalid_request_is_rejected_before_durable_row_creation(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "runtime.db")
    harness = DurableQGenHarness(_FakeQGen(), store)  # type: ignore[arg-type]
    invalid = _input().model_copy(
        update={
            "request": _input().request.model_copy(update={"question_type": "invalid"})
        }
    )

    with pytest.raises(ValueError, match="invalid question contract"):
        await harness.run(invalid, run_id="invalid-run")

    assert store.get_run("invalid-run") is None


@pytest.mark.asyncio
async def test_durable_harness_persists_steps_usage_checkpoints_and_output(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "runtime.db")
    agent = _FakeQGen()
    harness = DurableQGenHarness(agent, store)  # type: ignore[arg-type]

    run_id, output = await harness.run(
        _input(),
        run_id="qgen-1",
        budget=RunBudget(max_steps=5, max_llm_calls=2, max_tokens=100),
    )

    assert run_id == "qgen-1"
    assert output.reason.startswith("workflow completed")
    record = store.require_run(run_id)
    assert record.status is RunStatus.SUCCEEDED
    assert record.usage.steps == 3
    assert record.usage.llm_calls == 1
    assert record.usage.tokens == 40
    assert [step.name for step in store.list_steps(run_id)] == [
        "retrieve",
        "generate",
        "finish",
    ]
    checkpoints = store.list_checkpoints(run_id)
    assert [checkpoint.cursor["next_node"] for checkpoint in checkpoints] == [
        "generate",
        "finish",
        END,
    ]
    assert QGenGraphSnapshot.model_validate(checkpoints[-1].state["graph"]).output is not None
    progress = [
        event for event in store.list_events(run_id) if event.event_type == "qgen.progress"
    ]
    assert [event.payload["phase"] for event in progress] == [
        "retrieve",
        "generate",
        "finish",
    ]
    assert progress[1].payload["candidate_count"] == 0

    # completed run is idempotently readable and does not execute graph again.
    _, repeated = await harness.run(_input(), run_id=run_id, resume=True)
    assert repeated.reason == output.reason
    assert agent.visited == ["retrieve", "generate", "finish"]


@pytest.mark.asyncio
async def test_budget_pause_resumes_from_next_node_without_replaying_retrieve(
    tmp_path: Path,
) -> None:
    store = SQLiteRunStore(tmp_path / "runtime.db")
    agent = _FakeQGen()
    harness = DurableQGenHarness(agent, store)  # type: ignore[arg-type]

    with pytest.raises(BudgetExceededError):
        await harness.run(
            _input(),
            run_id="qgen-budget",
            budget=RunBudget(max_steps=5, max_llm_calls=0, max_tokens=100),
        )

    paused = store.require_run("qgen-budget")
    assert paused.status is RunStatus.PAUSED
    assert store.load_latest_checkpoint("qgen-budget").cursor["next_node"] == "generate"
    assert agent.visited == ["retrieve", "generate"]

    store.update_budget(
        "qgen-budget",
        RunBudget(max_steps=5, max_llm_calls=2, max_tokens=200),
    )
    _, output = await harness.run(_input(), run_id="qgen-budget", resume=True)

    assert output.reason.startswith("workflow completed")
    # retrieve 来自 checkpoint，恢复只重试未提交的 generate，然后 finish。
    assert agent.visited == ["retrieve", "generate", "generate", "finish"]
    assert store.require_run("qgen-budget").status is RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_resume_rejects_request_drift(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runtime.db")
    agent = _FakeQGen()
    harness = DurableQGenHarness(agent, store)  # type: ignore[arg-type]
    run_id, _ = await harness.run(_input(), run_id="immutable-input")
    changed = _input()
    changed.request.difficulty_target = 9

    with pytest.raises(InvalidRunTransitionError, match="different generation request"):
        await harness.run(changed, run_id=run_id, resume=True)


@pytest.mark.asyncio
async def test_precreated_pending_run_is_first_execution_not_resume(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "runtime.db")
    agent = _FakeQGen()
    harness = DurableQGenHarness(agent, store)  # type: ignore[arg-type]
    request = _input()
    store.create_run(
        "qgen",
        request.request.model_dump(mode="json"),
        run_id="api-precreated",
    )

    _, output = await harness.run(request, run_id="api-precreated", resume=True)

    assert output.reason.startswith("workflow completed")
    event_types = [event.event_type for event in store.list_events("api-precreated")]
    assert "run.resume_prepared" not in event_types
    assert event_types[:2] == ["run.created", "run.started"]
