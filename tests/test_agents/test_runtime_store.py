"""Durable Agent run store: lifecycle, recovery, budgets and concurrency."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import BaseModel

from sciforge.agents.runtime import (
    BudgetExceededError,
    CheckpointConflictError,
    InvalidRunTransitionError,
    RunBudget,
    RunCancellationRequestedError,
    RunStatus,
    SQLiteRunStore,
    StepStatus,
)


class _Snapshot(BaseModel):
    candidate_id: str
    score: float


def _store(tmp_path: Path) -> SQLiteRunStore:
    return SQLiteRunStore(tmp_path / "agent_runtime.db")


def test_full_run_lifecycle_persists_usage_checkpoint_and_events(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run = store.create_run(
        "qgen",
        {"subject": "analysis"},
        budget=RunBudget(
            max_steps=4,
            max_llm_calls=3,
            max_tokens=2_000,
            max_wall_time_seconds=30,
        ),
        metadata={"tenant_id": "tenant-a"},
        run_id="run-lifecycle",
    )
    assert run.status is RunStatus.PENDING
    assert run.usage.steps == 0

    running = store.begin_run(run.run_id)
    step = store.begin_step(
        run.run_id,
        "generate",
        input_payload={"round": 1},
    )
    usage = store.record_usage(
        run.run_id,
        llm_calls=1,
        tokens=240,
        wall_time_seconds=0.75,
    )
    checkpoint = store.save_checkpoint(
        run.run_id,
        1,
        "generate",
        {"round": 1, "candidate": _Snapshot(candidate_id="q-1", score=0.8)},
        cursor={"next_node": "solve"},
    )
    completed_step = store.complete_step(step.step_id, {"candidate_id": "q-1"})
    completed_run = store.complete_run(run.run_id, {"question_id": "q-1"})

    assert running.status is RunStatus.RUNNING
    assert usage.steps == 1
    assert usage.llm_calls == 1
    assert usage.tokens == 240
    assert usage.wall_time_seconds == pytest.approx(0.75)
    assert checkpoint.state["candidate"] == {
        "candidate_id": "q-1",
        "score": 0.8,
    }
    assert checkpoint.cursor == {"next_node": "solve"}
    assert completed_step.status is StepStatus.SUCCEEDED
    assert completed_step.output_payload == {"candidate_id": "q-1"}
    assert completed_run.status is RunStatus.SUCCEEDED
    assert completed_run.output_payload == {"question_id": "q-1"}
    assert completed_run.current_step_id is None

    events = store.list_events(run.run_id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        "run.created",
        "run.started",
        "step.started",
        "usage.recorded",
        "checkpoint.saved",
        "step.succeeded",
        "run.succeeded",
    ]
    assert store.list_events(run.run_id, after_sequence=4)[0].event_type == "checkpoint.saved"


def test_restart_prepares_resume_from_latest_checkpoint(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    first_process = SQLiteRunStore(db_path)
    run = first_process.create_run(
        "qgen",
        {"topic": "groups"},
        budget=RunBudget(max_steps=5),
    )
    first_process.begin_run(run.run_id)
    stale_step = first_process.begin_step(run.run_id, "solve")
    first_process.save_checkpoint(
        run.run_id,
        1,
        "solve",
        {"round": 2, "candidate_id": "q-old"},
        cursor={"next_node": "verify"},
    )

    # A new store instance simulates a worker process restart.
    second_process = SQLiteRunStore(db_path)
    restored = second_process.prepare_resume(run.run_id)
    assert restored is not None
    assert restored.state == {"candidate_id": "q-old", "round": 2}
    assert restored.cursor == {"next_node": "verify"}
    assert second_process.require_run(run.run_id).status is RunStatus.PAUSED
    assert second_process.get_step(stale_step.step_id).status is StepStatus.INTERRUPTED

    second_process.begin_run(run.run_id)
    retry = second_process.begin_step(run.run_id, "solve", attempt=2)
    second_process.save_checkpoint(
        run.run_id,
        2,
        "verify",
        {"round": 2, "candidate_id": "q-new"},
        cursor={"next_node": "annotate"},
    )
    second_process.complete_step(retry.step_id)

    assert second_process.load_latest_checkpoint(run.run_id).step_number == 2
    assert [step.attempt for step in second_process.list_steps(run.run_id)] == [
        1,
        2,
    ]
    assert [step.status for step in second_process.list_steps(run.run_id)] == [
        StepStatus.INTERRUPTED,
        StepStatus.SUCCEEDED,
    ]
    assert "step.interrupted" in [
        event.event_type for event in second_process.list_events(run.run_id)
    ]


def test_budget_exhaustion_is_atomic_and_pauses_active_run(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run = store.create_run(
        "qgen",
        {},
        budget=RunBudget(
            max_steps=1,
            max_llm_calls=2,
            max_tokens=10,
            max_wall_time_seconds=2,
        ),
    )
    store.begin_run(run.run_id)
    first = store.begin_step(run.run_id, "generate")
    store.record_usage(
        run.run_id,
        llm_calls=1,
        tokens=5,
        wall_time_seconds=0.5,
    )
    store.complete_step(first.step_id)

    with pytest.raises(BudgetExceededError) as step_exc:
        store.begin_step(run.run_id, "solve")
    assert step_exc.value.dimension == "steps"
    paused = store.require_run(run.run_id)
    assert paused.status is RunStatus.PAUSED
    assert paused.usage.steps == 1

    store.update_budget(
        run.run_id,
        RunBudget(
            max_steps=3,
            max_llm_calls=3,
            max_tokens=10,
            max_wall_time_seconds=2,
        ),
    )
    store.begin_run(run.run_id)
    active = store.begin_step(run.run_id, "solve")
    with pytest.raises(BudgetExceededError) as token_exc:
        store.record_usage(run.run_id, llm_calls=1, tokens=6)
    assert token_exc.value.dimension == "tokens"

    paused_after_usage = store.require_run(run.run_id)
    assert paused_after_usage.status is RunStatus.PAUSED
    # Reservation is all-or-nothing: neither LLM call nor tokens were charged.
    assert paused_after_usage.usage.llm_calls == 1
    assert paused_after_usage.usage.tokens == 5
    assert store.get_step(active.step_id).status is StepStatus.INTERRUPTED
    assert store.list_events(run.run_id)[-1].event_type == "budget.exhausted"


def test_cancel_flag_stops_new_work_and_acknowledgement_is_terminal(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run = store.create_run("qgen", {})
    store.begin_run(run.run_id)
    step = store.begin_step(run.run_id, "verify")

    requested = store.request_cancel(run.run_id, reason="user stopped")
    assert requested.cancel_requested is True
    with pytest.raises(RunCancellationRequestedError):
        store.ensure_can_continue(run.run_id)
    with pytest.raises(RunCancellationRequestedError):
        store.begin_step(run.run_id, "annotate")

    cancelled = store.acknowledge_cancel(run.run_id, reason="stopped at safe boundary")
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.current_step_id is None
    assert store.get_step(step.step_id).status is StepStatus.CANCELLED
    assert store.acknowledge_cancel(run.run_id).status is RunStatus.CANCELLED
    event_types = [event.event_type for event in store.list_events(run.run_id)]
    assert event_types[-2:] == ["step.cancelled", "run.cancelled"]
    with pytest.raises(InvalidRunTransitionError):
        store.begin_run(run.run_id)


def test_cancel_requested_after_last_step_blocks_success_commit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = store.create_run("qgen", {})
    store.begin_run(run.run_id)
    step = store.begin_step(run.run_id, "finalize")
    store.complete_step(step.step_id)
    store.request_cancel(run.run_id, reason="last-moment user cancellation")

    with pytest.raises(RunCancellationRequestedError):
        store.complete_run(run.run_id, {"should_not": "commit"})

    cancelled = store.acknowledge_cancel(run.run_id, reason="cancel won final commit race")
    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.output_payload is None


@pytest.mark.parametrize("initial_status", [RunStatus.PENDING, RunStatus.PAUSED])
def test_cancel_without_active_worker_is_immediately_terminal(
    tmp_path: Path,
    initial_status: RunStatus,
) -> None:
    store = _store(tmp_path)
    run = store.create_run("qgen", {})
    if initial_status is RunStatus.PAUSED:
        store.begin_run(run.run_id)
        store.pause_run(run.run_id, reason="waiting for a worker")

    cancelled = store.request_cancel(run.run_id, reason="user stopped")

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.cancel_requested is True
    assert [event.event_type for event in store.list_events(run.run_id)][-2:] == [
        "run.cancel_requested",
        "run.cancelled",
    ]


def test_checkpoint_is_idempotent_but_immutable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    run = store.create_run("qgen", {})
    store.begin_run(run.run_id)

    first = store.save_checkpoint(
        run.run_id,
        1,
        "retrieve",
        {"context_ids": ["kp-1"]},
        cursor={"next_node": "generate"},
    )
    repeated = store.save_checkpoint(
        run.run_id,
        1,
        "retrieve",
        {"context_ids": ["kp-1"]},
        cursor={"next_node": "generate"},
    )
    assert repeated.checkpoint_id == first.checkpoint_id
    assert len(store.list_checkpoints(run.run_id)) == 1

    with pytest.raises(CheckpointConflictError):
        store.save_checkpoint(
            run.run_id,
            1,
            "retrieve",
            {"context_ids": ["kp-2"]},
            cursor={"next_node": "generate"},
        )

    # Database triggers enforce append-only/immutable semantics even if a caller
    # bypasses the Python API.
    conn = sqlite3.connect(store.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                "UPDATE agent_run_events SET event_type = 'tampered' WHERE run_id = ?",
                (run.run_id,),
            )
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE agent_run_checkpoints SET node = 'tampered' WHERE checkpoint_id = ?",
                (first.checkpoint_id,),
            )
    finally:
        conn.close()


def test_concurrent_event_append_and_idempotent_step_claim(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    run = store.create_run("qgen", {})
    store.begin_run(run.run_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = list(
            pool.map(
                lambda _: store.begin_step(run.run_id, "generate", attempt=1).step_id,
                range(24),
            )
        )
    assert len(set(claimed)) == 1
    assert store.require_run(run.run_id).usage.steps == 1
    store.complete_step(claimed[0])

    with ThreadPoolExecutor(max_workers=8) as pool:
        appended = list(
            pool.map(
                lambda index: store.append_event(run.run_id, "candidate.scored", {"index": index}),
                range(40),
            )
        )

    assert len({event.event_id for event in appended}) == 40
    events = store.list_events(run.run_id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert sum(event.event_type == "candidate.scored" for event in events) == 40
