"""Concurrency-safe SQLite persistence for long-running Agent executions.

The store is separate from the educational knowledge-base database. It uses a
short-lived connection and BEGIN IMMEDIATE for each read-modify-write operation,
so event sequences, step claims and budget reservations remain atomic across
threads and worker processes.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic import BaseModel

from sciforge.agents.runtime.models import (
    TERMINAL_RUN_STATUSES,
    BudgetExceededError,
    CheckpointConflictError,
    CheckpointRecord,
    InvalidRunTransitionError,
    JsonObject,
    RunBudget,
    RunCancellationRequestedError,
    RunEvent,
    RunNotFoundError,
    RunRecord,
    RunStatus,
    RunStoreError,
    RunUsage,
    StepNotFoundError,
    StepRecord,
    StepStatus,
)

_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 10_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runtime_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id TEXT PRIMARY KEY,
    run_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    metadata_json TEXT NOT NULL,
    budget_json TEXT NOT NULL,
    steps_used INTEGER NOT NULL DEFAULT 0 CHECK (steps_used >= 0),
    llm_calls_used INTEGER NOT NULL DEFAULT 0 CHECK (llm_calls_used >= 0),
    tokens_used INTEGER NOT NULL DEFAULT 0 CHECK (tokens_used >= 0),
    wall_time_seconds_used REAL NOT NULL DEFAULT 0 CHECK (wall_time_seconds_used >= 0),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    current_step_id TEXT,
    latest_checkpoint_step INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status
    ON agent_runs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_kind_status
    ON agent_runs(run_kind, status, created_at);

CREATE TABLE IF NOT EXISTS agent_run_steps (
    step_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    status TEXT NOT NULL,
    input_json TEXT NOT NULL,
    output_json TEXT,
    error TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT,
    UNIQUE(run_id, name, attempt)
);
CREATE INDEX IF NOT EXISTS idx_agent_run_steps_run
    ON agent_run_steps(run_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_run_steps_one_active
    ON agent_run_steps(run_id) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS agent_run_checkpoints (
    checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    step_number INTEGER NOT NULL CHECK (step_number >= 1),
    node TEXT NOT NULL,
    state_json TEXT NOT NULL,
    cursor_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, step_number)
);
CREATE INDEX IF NOT EXISTS idx_agent_run_checkpoints_latest
    ON agent_run_checkpoints(run_id, step_number DESC);

CREATE TABLE IF NOT EXISTS agent_run_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(run_id, sequence)
);
CREATE INDEX IF NOT EXISTS idx_agent_run_events_stream
    ON agent_run_events(run_id, sequence);

CREATE TRIGGER IF NOT EXISTS agent_run_events_no_update
BEFORE UPDATE ON agent_run_events
BEGIN
    SELECT RAISE(ABORT, 'agent_run_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS agent_run_events_no_delete
BEFORE DELETE ON agent_run_events
BEGIN
    SELECT RAISE(ABORT, 'agent_run_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS agent_run_checkpoints_no_update
BEFORE UPDATE ON agent_run_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'agent_run_checkpoints is immutable');
END;

CREATE TRIGGER IF NOT EXISTS agent_run_checkpoints_no_delete
BEFORE DELETE ON agent_run_checkpoints
BEGIN
    SELECT RAISE(ABORT, 'agent_run_checkpoints is immutable');
END;
"""


class RunStore(Protocol):
    """Backend-neutral contract for a future long-horizon harness."""

    def create_run(
        self,
        run_kind: str,
        input_payload: Mapping[str, Any],
        *,
        budget: RunBudget | None = None,
        metadata: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> RunRecord: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def begin_run(self, run_id: str) -> RunRecord: ...

    def begin_step(
        self,
        run_id: str,
        name: str,
        *,
        attempt: int = 1,
        input_payload: Mapping[str, Any] | None = None,
    ) -> StepRecord: ...

    def complete_step(
        self, step_id: str, output_payload: Mapping[str, Any] | None = None
    ) -> StepRecord: ...

    def save_checkpoint(
        self,
        run_id: str,
        step_number: int,
        node: str,
        state: Mapping[str, Any],
        *,
        cursor: Mapping[str, Any] | None = None,
    ) -> CheckpointRecord: ...

    def load_latest_checkpoint(self, run_id: str) -> CheckpointRecord | None: ...

    def request_cancel(self, run_id: str, *, reason: str = "") -> RunRecord: ...

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> RunEvent: ...

    def list_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 1000
    ) -> list[RunEvent]: ...


class SQLiteRunStore:
    """Durable run/checkpoint/event store backed by a dedicated SQLite file."""

    def __init__(self, db_path: Path | str) -> None:
        if str(db_path) == ":memory:":
            raise ValueError("SQLiteRunStore requires a file path, not :memory:")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    # Run lifecycle

    def create_run(
        self,
        run_kind: str,
        input_payload: Mapping[str, Any],
        *,
        budget: RunBudget | None = None,
        metadata: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        kind = _require_text(run_kind, "run_kind")
        identifier = _require_text(run_id or str(uuid.uuid4()), "run_id")
        effective_budget = budget or RunBudget()
        now = _utc_now()
        try:
            with self._write() as conn:
                conn.execute(
                    "INSERT INTO agent_runs "
                    "(run_id, run_kind, status, input_json, metadata_json, budget_json, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        identifier,
                        kind,
                        RunStatus.PENDING.value,
                        _json_dumps(input_payload),
                        _json_dumps(metadata),
                        _json_dumps(effective_budget.to_dict()),
                        now,
                        now,
                    ),
                )
                self._append_event_conn(
                    conn,
                    identifier,
                    "run.created",
                    {"run_kind": kind, "budget": effective_budget.to_dict()},
                )
                row = self._require_run_row(conn, identifier)
        except sqlite3.IntegrityError as exc:
            raise RunStoreError(f"run already exists: {identifier}") from exc
        return _run_from_row(row)

    def get_run(self, run_id: str) -> RunRecord | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
            return None if row is None else _run_from_row(row)
        finally:
            conn.close()

    def require_run(self, run_id: str) -> RunRecord:
        record = self.get_run(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record

    def begin_run(self, run_id: str) -> RunRecord:
        with self._write() as conn:
            row = self._require_run_row(conn, run_id)
            status = RunStatus(str(row["status"]))
            if status in TERMINAL_RUN_STATUSES:
                raise InvalidRunTransitionError(f"cannot begin {run_id} from {status.value}")
            self._raise_if_cancel_requested(row)
            if status is RunStatus.RUNNING:
                return _run_from_row(row)
            if status not in {RunStatus.PENDING, RunStatus.PAUSED}:
                raise InvalidRunTransitionError(f"cannot begin {run_id} from {status.value}")
            now = _utc_now()
            conn.execute(
                "UPDATE agent_runs SET status = ?, updated_at = ?, "
                "started_at = COALESCE(started_at, ?), last_error = '' WHERE run_id = ?",
                (RunStatus.RUNNING.value, now, now, run_id),
            )
            self._append_event_conn(
                conn,
                run_id,
                "run.started",
                {"resumed": status is RunStatus.PAUSED},
            )
            return _run_from_row(self._require_run_row(conn, run_id))

    def pause_run(self, run_id: str, *, reason: str = "") -> RunRecord:
        with self._write() as conn:
            row = self._require_run_row(conn, run_id)
            status = RunStatus(str(row["status"]))
            if status is RunStatus.PAUSED:
                return _run_from_row(row)
            if status is not RunStatus.RUNNING:
                raise InvalidRunTransitionError(f"cannot pause {run_id} from {status.value}")
            if row["current_step_id"] is not None:
                raise InvalidRunTransitionError(
                    "finish or interrupt the active step before pausing"
                )
            now = _utc_now()
            conn.execute(
                "UPDATE agent_runs SET status = ?, updated_at = ?, last_error = ? WHERE run_id = ?",
                (RunStatus.PAUSED.value, now, reason, run_id),
            )
            self._append_event_conn(conn, run_id, "run.paused", {"reason": reason})
            return _run_from_row(self._require_run_row(conn, run_id))

    def complete_run(
        self,
        run_id: str,
        output_payload: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        return self._finish_run(
            run_id,
            RunStatus.SUCCEEDED,
            output_payload=output_payload,
            error="",
        )

    def fail_run(self, run_id: str, error: str) -> RunRecord:
        return self._finish_run(
            run_id,
            RunStatus.FAILED,
            output_payload=None,
            error=error,
        )

    def request_cancel(self, run_id: str, *, reason: str = "") -> RunRecord:
        with self._write() as conn:
            row = self._require_run_row(conn, run_id)
            status = RunStatus(str(row["status"]))
            if status in TERMINAL_RUN_STATUSES or bool(row["cancel_requested"]):
                return _run_from_row(row)
            now = _utc_now()
            conn.execute(
                "UPDATE agent_runs SET cancel_requested = 1, updated_at = ? WHERE run_id = ?",
                (now, run_id),
            )
            self._append_event_conn(conn, run_id, "run.cancel_requested", {"reason": reason})
            # No worker owns a pending/paused run, so there is no future node
            # boundary at which it could acknowledge the flag.  Finalize these
            # states immediately; running work remains cooperatively cancelled
            # at its next safe graph boundary.
            if status in {RunStatus.PENDING, RunStatus.PAUSED} and row["current_step_id"] is None:
                conn.execute(
                    "UPDATE agent_runs SET status = ?, completed_at = ?, "
                    "updated_at = ?, last_error = ? WHERE run_id = ?",
                    (RunStatus.CANCELLED.value, now, now, reason, run_id),
                )
                self._append_event_conn(conn, run_id, "run.cancelled", {"reason": reason})
            return _run_from_row(self._require_run_row(conn, run_id))

    def acknowledge_cancel(self, run_id: str, *, reason: str = "") -> RunRecord:
        with self._write() as conn:
            row = self._require_run_row(conn, run_id)
            status = RunStatus(str(row["status"]))
            if status is RunStatus.CANCELLED:
                return _run_from_row(row)
            if status in TERMINAL_RUN_STATUSES:
                raise InvalidRunTransitionError(f"cannot cancel {run_id} from {status.value}")
            if not bool(row["cancel_requested"]):
                raise InvalidRunTransitionError("cancellation has not been requested")
            now = _utc_now()
            self._end_active_step_conn(conn, run_id, StepStatus.CANCELLED, reason, now)
            conn.execute(
                "UPDATE agent_runs SET status = ?, current_step_id = NULL, "
                "completed_at = ?, updated_at = ?, last_error = ? WHERE run_id = ?",
                (RunStatus.CANCELLED.value, now, now, reason, run_id),
            )
            self._append_event_conn(conn, run_id, "run.cancelled", {"reason": reason})
            return _run_from_row(self._require_run_row(conn, run_id))

    def ensure_can_continue(self, run_id: str) -> RunRecord:
        record = self.require_run(run_id)
        if record.cancel_requested:
            raise RunCancellationRequestedError(run_id)
        if record.status is not RunStatus.RUNNING:
            raise InvalidRunTransitionError(
                f"run {run_id} is {record.status.value}, expected running"
            )
        return record

    def prepare_resume(
        self, run_id: str, *, reason: str = "worker restarted"
    ) -> CheckpointRecord | None:
        """Interrupt stale work and return the latest harness-defined checkpoint."""
        with self._write() as conn:
            row = self._require_run_row(conn, run_id)
            status = RunStatus(str(row["status"]))
            self._raise_if_cancel_requested(row)
            if status in TERMINAL_RUN_STATUSES:
                raise InvalidRunTransitionError(f"cannot resume {run_id} from {status.value}")
            now = _utc_now()
            if status is RunStatus.RUNNING:
                self._end_active_step_conn(conn, run_id, StepStatus.INTERRUPTED, reason, now)
                conn.execute(
                    "UPDATE agent_runs SET status = ?, current_step_id = NULL, "
                    "updated_at = ?, last_error = ? WHERE run_id = ?",
                    (RunStatus.PAUSED.value, now, reason, run_id),
                )
            checkpoint_row = conn.execute(
                "SELECT * FROM agent_run_checkpoints WHERE run_id = ? "
                "ORDER BY step_number DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            checkpoint_id = None if checkpoint_row is None else int(checkpoint_row["checkpoint_id"])
            self._append_event_conn(
                conn,
                run_id,
                "run.resume_prepared",
                {"checkpoint_id": checkpoint_id, "reason": reason},
            )
            return None if checkpoint_row is None else _checkpoint_from_row(checkpoint_row)

    def list_recoverable_runs(
        self, *, run_kind: str | None = None, limit: int = 100
    ) -> list[RunRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        conn = self._connect()
        statuses = (
            RunStatus.PENDING.value,
            RunStatus.RUNNING.value,
            RunStatus.PAUSED.value,
        )
        try:
            if run_kind is None:
                rows = conn.execute(
                    "SELECT * FROM agent_runs WHERE status IN (?, ?, ?) "
                    "ORDER BY created_at LIMIT ?",
                    (*statuses, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_runs WHERE run_kind = ? "
                    "AND status IN (?, ?, ?) ORDER BY created_at LIMIT ?",
                    (run_kind, *statuses, limit),
                ).fetchall()
            return [_run_from_row(row) for row in rows]
        finally:
            conn.close()

    # Step lifecycle and budgets

    def begin_step(
        self,
        run_id: str,
        name: str,
        *,
        attempt: int = 1,
        input_payload: Mapping[str, Any] | None = None,
    ) -> StepRecord:
        step_name = _require_text(name, "name")
        if attempt < 1:
            raise ValueError("attempt must be at least 1")
        violation: BudgetExceededError | None = None
        result: StepRecord | None = None

        with self._write() as conn:
            run_row = self._require_running_row(conn, run_id)
            existing = conn.execute(
                "SELECT * FROM agent_run_steps WHERE run_id = ? AND name = ? AND attempt = ?",
                (run_id, step_name, attempt),
            ).fetchone()
            if existing is not None:
                return _step_from_row(existing)
            if run_row["current_step_id"] is not None:
                raise InvalidRunTransitionError(
                    f"run {run_id} already has active step {run_row['current_step_id']}"
                )

            violation = self._budget_violation(run_row, steps=1)
            if violation is not None:
                self._pause_for_budget_conn(conn, run_id, violation)
            else:
                step_id = str(uuid.uuid4())
                now = _utc_now()
                conn.execute(
                    "INSERT INTO agent_run_steps "
                    "(step_id, run_id, name, attempt, status, input_json, started_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        step_id,
                        run_id,
                        step_name,
                        attempt,
                        StepStatus.RUNNING.value,
                        _json_dumps(input_payload),
                        now,
                    ),
                )
                conn.execute(
                    "UPDATE agent_runs SET current_step_id = ?, "
                    "steps_used = steps_used + 1, updated_at = ? WHERE run_id = ?",
                    (step_id, now, run_id),
                )
                self._append_event_conn(
                    conn,
                    run_id,
                    "step.started",
                    {"step_id": step_id, "name": step_name, "attempt": attempt},
                )
                result = _step_from_row(self._require_step_row(conn, step_id))

        if violation is not None:
            raise violation
        if result is None:  # pragma: no cover - defensive invariant
            raise RunStoreError("step transaction committed without a result")
        return result

    def complete_step(
        self,
        step_id: str,
        output_payload: Mapping[str, Any] | None = None,
    ) -> StepRecord:
        return self._finish_step(
            step_id,
            StepStatus.SUCCEEDED,
            output_payload=output_payload,
            error="",
        )

    def fail_step(self, step_id: str, error: str) -> StepRecord:
        return self._finish_step(
            step_id,
            StepStatus.FAILED,
            output_payload=None,
            error=error,
        )

    def cancel_step(self, step_id: str, *, reason: str = "") -> StepRecord:
        return self._finish_step(
            step_id,
            StepStatus.CANCELLED,
            output_payload=None,
            error=reason,
        )

    def get_step(self, step_id: str) -> StepRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM agent_run_steps WHERE step_id = ?", (step_id,)
            ).fetchone()
            return None if row is None else _step_from_row(row)
        finally:
            conn.close()

    def list_steps(self, run_id: str) -> list[StepRecord]:
        conn = self._connect()
        try:
            if (
                conn.execute("SELECT 1 FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
                is None
            ):
                raise RunNotFoundError(run_id)
            rows = conn.execute(
                "SELECT * FROM agent_run_steps WHERE run_id = ? ORDER BY started_at, rowid",
                (run_id,),
            ).fetchall()
            return [_step_from_row(row) for row in rows]
        finally:
            conn.close()

    def record_usage(
        self,
        run_id: str,
        *,
        llm_calls: int = 0,
        tokens: int = 0,
        wall_time_seconds: float = 0.0,
    ) -> RunUsage:
        if llm_calls < 0 or tokens < 0 or wall_time_seconds < 0:
            raise ValueError("usage deltas must be non-negative")
        violation: BudgetExceededError | None = None
        usage: RunUsage | None = None

        with self._write() as conn:
            row = self._require_running_row(conn, run_id)
            violation = self._budget_violation(
                row,
                llm_calls=llm_calls,
                tokens=tokens,
                wall_time_seconds=wall_time_seconds,
            )
            if violation is not None:
                self._pause_for_budget_conn(conn, run_id, violation)
            else:
                now = _utc_now()
                conn.execute(
                    "UPDATE agent_runs SET "
                    "llm_calls_used = llm_calls_used + ?, "
                    "tokens_used = tokens_used + ?, "
                    "wall_time_seconds_used = wall_time_seconds_used + ?, "
                    "updated_at = ? WHERE run_id = ?",
                    (llm_calls, tokens, wall_time_seconds, now, run_id),
                )
                updated = self._require_run_row(conn, run_id)
                usage = _usage_from_row(updated)
                self._append_event_conn(
                    conn,
                    run_id,
                    "usage.recorded",
                    {
                        "delta": {
                            "llm_calls": llm_calls,
                            "tokens": tokens,
                            "wall_time_seconds": wall_time_seconds,
                        },
                        "total": usage.to_dict(),
                    },
                )

        if violation is not None:
            raise violation
        if usage is None:  # pragma: no cover - defensive invariant
            raise RunStoreError("usage transaction committed without a result")
        return usage

    def update_budget(self, run_id: str, budget: RunBudget) -> RunRecord:
        with self._write() as conn:
            row = self._require_run_row(conn, run_id)
            status = RunStatus(str(row["status"]))
            if status not in {RunStatus.PENDING, RunStatus.PAUSED}:
                raise InvalidRunTransitionError(
                    "budget can only be updated while a run is pending or paused"
                )
            _validate_budget_covers_usage(budget, _usage_from_row(row))
            now = _utc_now()
            conn.execute(
                "UPDATE agent_runs SET budget_json = ?, updated_at = ?, "
                "last_error = '' WHERE run_id = ?",
                (_json_dumps(budget.to_dict()), now, run_id),
            )
            self._append_event_conn(conn, run_id, "budget.updated", budget.to_dict())
            return _run_from_row(self._require_run_row(conn, run_id))

    # Checkpoints

    def save_checkpoint(
        self,
        run_id: str,
        step_number: int,
        node: str,
        state: Mapping[str, Any],
        *,
        cursor: Mapping[str, Any] | None = None,
    ) -> CheckpointRecord:
        if step_number < 1:
            raise ValueError("step_number must be at least 1")
        node_name = _require_text(node, "node")
        state_json = _json_dumps(state)
        cursor_json = _json_dumps(cursor)

        with self._write() as conn:
            run_row = self._require_run_row(conn, run_id)
            status = RunStatus(str(run_row["status"]))
            if status not in {RunStatus.RUNNING, RunStatus.PAUSED}:
                raise InvalidRunTransitionError(f"cannot checkpoint {run_id} while {status.value}")
            existing = conn.execute(
                "SELECT * FROM agent_run_checkpoints WHERE run_id = ? AND step_number = ?",
                (run_id, step_number),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["node"]) == node_name
                    and str(existing["state_json"]) == state_json
                    and str(existing["cursor_json"]) == cursor_json
                ):
                    return _checkpoint_from_row(existing)
                raise CheckpointConflictError(
                    f"checkpoint {run_id}/{step_number} already exists with different content"
                )
            latest = int(run_row["latest_checkpoint_step"])
            if step_number <= latest:
                raise CheckpointConflictError(
                    f"checkpoint step {step_number} is behind latest {latest}"
                )

            now = _utc_now()
            cur = conn.execute(
                "INSERT INTO agent_run_checkpoints "
                "(run_id, step_number, node, state_json, cursor_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, step_number, node_name, state_json, cursor_json, now),
            )
            checkpoint_id = int(cast(int, cur.lastrowid))
            conn.execute(
                "UPDATE agent_runs SET latest_checkpoint_step = ?, updated_at = ? WHERE run_id = ?",
                (step_number, now, run_id),
            )
            self._append_event_conn(
                conn,
                run_id,
                "checkpoint.saved",
                {
                    "checkpoint_id": checkpoint_id,
                    "step_number": step_number,
                    "node": node_name,
                    "cursor": _json_load_object(cursor_json),
                },
            )
            row = conn.execute(
                "SELECT * FROM agent_run_checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
            if row is None:  # pragma: no cover - SQLite invariant
                raise RunStoreError("checkpoint insert did not return a row")
            return _checkpoint_from_row(row)

    def load_latest_checkpoint(self, run_id: str) -> CheckpointRecord | None:
        conn = self._connect()
        try:
            if (
                conn.execute("SELECT 1 FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
                is None
            ):
                raise RunNotFoundError(run_id)
            row = conn.execute(
                "SELECT * FROM agent_run_checkpoints WHERE run_id = ? "
                "ORDER BY step_number DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            return None if row is None else _checkpoint_from_row(row)
        finally:
            conn.close()

    def list_checkpoints(self, run_id: str) -> list[CheckpointRecord]:
        conn = self._connect()
        try:
            if (
                conn.execute("SELECT 1 FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
                is None
            ):
                raise RunNotFoundError(run_id)
            rows = conn.execute(
                "SELECT * FROM agent_run_checkpoints WHERE run_id = ? ORDER BY step_number",
                (run_id,),
            ).fetchall()
            return [_checkpoint_from_row(row) for row in rows]
        finally:
            conn.close()

    # Append-only event stream

    def append_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
    ) -> RunEvent:
        with self._write() as conn:
            self._require_run_row(conn, run_id)
            return self._append_event_conn(conn, run_id, event_type, payload)

    def list_events(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> list[RunEvent]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit < 1:
            raise ValueError("limit must be positive")
        conn = self._connect()
        try:
            if (
                conn.execute("SELECT 1 FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
                is None
            ):
                raise RunNotFoundError(run_id)
            rows = conn.execute(
                "SELECT * FROM agent_run_events "
                "WHERE run_id = ? AND sequence > ? "
                "ORDER BY sequence LIMIT ?",
                (run_id, after_sequence, limit),
            ).fetchall()
            return [_event_from_row(row) for row in rows]
        finally:
            conn.close()

    # Internal transaction helpers

    def _initialize(self) -> None:
        conn = sqlite3.connect(str(self.db_path), timeout=_BUSY_TIMEOUT_MS / 1000)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT OR IGNORE INTO runtime_meta (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            row = conn.execute(
                "SELECT value FROM runtime_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None or int(row[0]) != _SCHEMA_VERSION:
                found = "missing" if row is None else str(row[0])
                raise RunStoreError(
                    f"unsupported Agent runtime schema {found}; expected {_SCHEMA_VERSION}"
                )
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=_BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        return conn

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _require_run_row(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM agent_runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError(run_id)
        return cast(sqlite3.Row, row)

    @staticmethod
    def _require_step_row(conn: sqlite3.Connection, step_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM agent_run_steps WHERE step_id = ?", (step_id,)).fetchone()
        if row is None:
            raise StepNotFoundError(step_id)
        return cast(sqlite3.Row, row)

    def _require_running_row(self, conn: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = self._require_run_row(conn, run_id)
        self._raise_if_cancel_requested(row)
        status = RunStatus(str(row["status"]))
        if status is not RunStatus.RUNNING:
            raise InvalidRunTransitionError(f"run {run_id} is {status.value}, expected running")
        return row

    @staticmethod
    def _raise_if_cancel_requested(row: sqlite3.Row) -> None:
        if bool(row["cancel_requested"]):
            raise RunCancellationRequestedError(str(row["run_id"]))

    def _finish_run(
        self,
        run_id: str,
        target: RunStatus,
        *,
        output_payload: Mapping[str, Any] | None,
        error: str,
    ) -> RunRecord:
        if target not in {RunStatus.SUCCEEDED, RunStatus.FAILED}:
            raise ValueError(f"unsupported terminal status: {target.value}")
        with self._write() as conn:
            row = self._require_run_row(conn, run_id)
            status = RunStatus(str(row["status"]))
            if status is target:
                return _run_from_row(row)
            if status in TERMINAL_RUN_STATUSES:
                raise InvalidRunTransitionError(
                    f"cannot finish {run_id} as {target.value} from {status.value}"
                )
            if target is RunStatus.SUCCEEDED and bool(row["cancel_requested"]):
                # Close the last-step race: cancellation requested after the
                # final step but before the run commit must win over success.
                raise RunCancellationRequestedError(run_id)
            if target is RunStatus.SUCCEEDED and status is not RunStatus.RUNNING:
                raise InvalidRunTransitionError(f"cannot complete {run_id} from {status.value}")
            now = _utc_now()
            if row["current_step_id"] is not None:
                if target is RunStatus.SUCCEEDED:
                    raise InvalidRunTransitionError(
                        "complete the active step before completing the run"
                    )
                self._end_active_step_conn(conn, run_id, StepStatus.FAILED, error, now)
            conn.execute(
                "UPDATE agent_runs SET status = ?, output_json = ?, "
                "current_step_id = NULL, completed_at = ?, updated_at = ?, "
                "last_error = ? WHERE run_id = ?",
                (
                    target.value,
                    None if output_payload is None else _json_dumps(output_payload),
                    now,
                    now,
                    error,
                    run_id,
                ),
            )
            self._append_event_conn(
                conn,
                run_id,
                f"run.{target.value}",
                {"error": error} if error else {},
            )
            return _run_from_row(self._require_run_row(conn, run_id))

    def _finish_step(
        self,
        step_id: str,
        target: StepStatus,
        *,
        output_payload: Mapping[str, Any] | None,
        error: str,
    ) -> StepRecord:
        with self._write() as conn:
            row = self._require_step_row(conn, step_id)
            status = StepStatus(str(row["status"]))
            if status is target:
                return _step_from_row(row)
            if status is not StepStatus.RUNNING:
                raise InvalidRunTransitionError(
                    f"cannot finish step {step_id} as {target.value} from {status.value}"
                )
            run_id = str(row["run_id"])
            self._require_running_row(conn, run_id)
            now = _utc_now()
            conn.execute(
                "UPDATE agent_run_steps SET status = ?, output_json = ?, "
                "error = ?, ended_at = ? WHERE step_id = ?",
                (
                    target.value,
                    None if output_payload is None else _json_dumps(output_payload),
                    error,
                    now,
                    step_id,
                ),
            )
            conn.execute(
                "UPDATE agent_runs SET current_step_id = NULL, updated_at = ? "
                "WHERE run_id = ? AND current_step_id = ?",
                (now, run_id, step_id),
            )
            self._append_event_conn(
                conn,
                run_id,
                f"step.{target.value}",
                {
                    "step_id": step_id,
                    "name": str(row["name"]),
                    "attempt": int(row["attempt"]),
                    "error": error,
                },
            )
            return _step_from_row(self._require_step_row(conn, step_id))

    def _end_active_step_conn(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        target: StepStatus,
        error: str,
        ended_at: str,
    ) -> None:
        row = conn.execute(
            "SELECT * FROM agent_run_steps WHERE run_id = ? AND status = ?",
            (run_id, StepStatus.RUNNING.value),
        ).fetchone()
        if row is None:
            return
        conn.execute(
            "UPDATE agent_run_steps SET status = ?, error = ?, ended_at = ? WHERE step_id = ?",
            (
                target.value,
                error,
                ended_at,
                str(row["step_id"]),
            ),
        )
        self._append_event_conn(
            conn,
            run_id,
            f"step.{target.value}",
            {
                "step_id": str(row["step_id"]),
                "name": str(row["name"]),
                "attempt": int(row["attempt"]),
                "error": error,
            },
        )

    def _budget_violation(
        self,
        row: sqlite3.Row,
        *,
        steps: int = 0,
        llm_calls: int = 0,
        tokens: int = 0,
        wall_time_seconds: float = 0.0,
    ) -> BudgetExceededError | None:
        run_id = str(row["run_id"])
        budget = RunBudget.from_dict(_json_load_object(str(row["budget_json"])))
        checks: tuple[tuple[str, int | float, int | float, int | float | None], ...] = (
            ("steps", int(row["steps_used"]), steps, budget.max_steps),
            (
                "llm_calls",
                int(row["llm_calls_used"]),
                llm_calls,
                budget.max_llm_calls,
            ),
            ("tokens", int(row["tokens_used"]), tokens, budget.max_tokens),
            (
                "wall_time_seconds",
                float(row["wall_time_seconds_used"]),
                wall_time_seconds,
                budget.max_wall_time_seconds,
            ),
        )
        for dimension, used, requested, limit in checks:
            if limit is not None and used + requested > limit:
                return BudgetExceededError(
                    run_id=run_id,
                    dimension=dimension,
                    used=used,
                    requested=requested,
                    limit=limit,
                )
        return None

    def _pause_for_budget_conn(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        violation: BudgetExceededError,
    ) -> None:
        now = _utc_now()
        self._end_active_step_conn(conn, run_id, StepStatus.INTERRUPTED, str(violation), now)
        conn.execute(
            "UPDATE agent_runs SET status = ?, current_step_id = NULL, "
            "updated_at = ?, last_error = ? WHERE run_id = ?",
            (RunStatus.PAUSED.value, now, str(violation), run_id),
        )
        self._append_event_conn(
            conn,
            run_id,
            "budget.exhausted",
            {
                "dimension": violation.dimension,
                "used": violation.used,
                "requested": violation.requested,
                "limit": violation.limit,
            },
        )

    def _append_event_conn(
        self,
        conn: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None,
    ) -> RunEvent:
        kind = _require_text(event_type, "event_type")
        payload_json = _json_dumps(payload)
        sequence_row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM agent_run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        sequence = 1 if sequence_row is None else int(sequence_row[0])
        now = _utc_now()
        cur = conn.execute(
            "INSERT INTO agent_run_events "
            "(run_id, sequence, event_type, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (run_id, sequence, kind, payload_json, now),
        )
        return RunEvent(
            event_id=int(cast(int, cur.lastrowid)),
            run_id=run_id,
            sequence=sequence,
            event_type=kind,
            payload=_json_load_object(payload_json),
            created_at=now,
        )


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    output_raw = row["output_json"]
    return RunRecord(
        run_id=str(row["run_id"]),
        run_kind=str(row["run_kind"]),
        status=RunStatus(str(row["status"])),
        input_payload=_json_load_object(str(row["input_json"])),
        output_payload=(None if output_raw is None else _json_load_object(str(output_raw))),
        metadata=_json_load_object(str(row["metadata_json"])),
        budget=RunBudget.from_dict(_json_load_object(str(row["budget_json"]))),
        usage=_usage_from_row(row),
        cancel_requested=bool(row["cancel_requested"]),
        current_step_id=(None if row["current_step_id"] is None else str(row["current_step_id"])),
        latest_checkpoint_step=int(row["latest_checkpoint_step"]),
        last_error=str(row["last_error"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=(None if row["started_at"] is None else str(row["started_at"])),
        completed_at=(None if row["completed_at"] is None else str(row["completed_at"])),
    )


def _usage_from_row(row: sqlite3.Row) -> RunUsage:
    return RunUsage(
        steps=int(row["steps_used"]),
        llm_calls=int(row["llm_calls_used"]),
        tokens=int(row["tokens_used"]),
        wall_time_seconds=float(row["wall_time_seconds_used"]),
    )


def _step_from_row(row: sqlite3.Row) -> StepRecord:
    output_raw = row["output_json"]
    return StepRecord(
        step_id=str(row["step_id"]),
        run_id=str(row["run_id"]),
        name=str(row["name"]),
        attempt=int(row["attempt"]),
        status=StepStatus(str(row["status"])),
        input_payload=_json_load_object(str(row["input_json"])),
        output_payload=(None if output_raw is None else _json_load_object(str(output_raw))),
        error=str(row["error"]),
        started_at=str(row["started_at"]),
        ended_at=None if row["ended_at"] is None else str(row["ended_at"]),
    )


def _checkpoint_from_row(row: sqlite3.Row) -> CheckpointRecord:
    return CheckpointRecord(
        checkpoint_id=int(row["checkpoint_id"]),
        run_id=str(row["run_id"]),
        step_number=int(row["step_number"]),
        node=str(row["node"]),
        state=_json_load_object(str(row["state_json"])),
        cursor=_json_load_object(str(row["cursor_json"])),
        created_at=str(row["created_at"]),
    )


def _event_from_row(row: sqlite3.Row) -> RunEvent:
    return RunEvent(
        event_id=int(row["event_id"]),
        run_id=str(row["run_id"]),
        sequence=int(row["sequence"]),
        event_type=str(row["event_type"]),
        payload=_json_load_object(str(row["payload_json"])),
        created_at=str(row["created_at"]),
    )


def _validate_budget_covers_usage(budget: RunBudget, usage: RunUsage) -> None:
    checks: tuple[tuple[str, int | float | None, int | float], ...] = (
        ("max_steps", budget.max_steps, usage.steps),
        ("max_llm_calls", budget.max_llm_calls, usage.llm_calls),
        ("max_tokens", budget.max_tokens, usage.tokens),
        (
            "max_wall_time_seconds",
            budget.max_wall_time_seconds,
            usage.wall_time_seconds,
        ),
    )
    for name, limit, used in checks:
        if limit is not None and limit < used:
            raise ValueError(f"{name}={limit} is below already-used amount {used}")


def _json_dumps(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        dict(value or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"object of type {type(value).__name__} is not JSON serializable")


def _json_load_object(raw: str) -> JsonObject:
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RunStoreError("stored JSON payload is not an object")
    return cast(JsonObject, value)


def _require_text(value: str, field_name: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError(f"{field_name} must not be empty")
    return result


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
