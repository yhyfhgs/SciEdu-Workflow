"""Persistent execution models for long-running Agent work.

These models deliberately live outside the knowledge-base schema.  A generation
run is operational state (restart, budget and observability), not educational
content, so it can evolve without forcing a migration of ``sciforge.db``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

JsonObject = dict[str, Any]


class RunStatus(StrEnum):
    """Lifecycle of one durable Agent run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    """Lifecycle of one attempted run step."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class RunBudget:
    """Hard limits for a run; ``None`` means unlimited for that dimension.

    Wall time is accumulated execution time reported by the harness.  It is not
    inferred from calendar time, which keeps pause/resume from consuming budget
    while no worker is active.
    """

    max_steps: int | None = None
    max_llm_calls: int | None = None
    max_tokens: int | None = None
    max_wall_time_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_steps", "max_llm_calls", "max_tokens"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.max_wall_time_seconds is not None and self.max_wall_time_seconds < 0:
            raise ValueError("max_wall_time_seconds must be non-negative")

    def to_dict(self) -> JsonObject:
        return {
            "max_steps": self.max_steps,
            "max_llm_calls": self.max_llm_calls,
            "max_tokens": self.max_tokens,
            "max_wall_time_seconds": self.max_wall_time_seconds,
        }

    @classmethod
    def from_dict(cls, value: JsonObject) -> RunBudget:
        return cls(
            max_steps=_optional_int(value.get("max_steps")),
            max_llm_calls=_optional_int(value.get("max_llm_calls")),
            max_tokens=_optional_int(value.get("max_tokens")),
            max_wall_time_seconds=_optional_float(value.get("max_wall_time_seconds")),
        )


@dataclass(frozen=True, slots=True)
class RunUsage:
    steps: int = 0
    llm_calls: int = 0
    tokens: int = 0
    wall_time_seconds: float = 0.0

    def to_dict(self) -> JsonObject:
        return {
            "steps": self.steps,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "wall_time_seconds": self.wall_time_seconds,
        }


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    run_kind: str
    status: RunStatus
    input_payload: JsonObject
    output_payload: JsonObject | None
    metadata: JsonObject
    budget: RunBudget
    usage: RunUsage
    cancel_requested: bool
    current_step_id: str | None
    latest_checkpoint_step: int
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str = ""


@dataclass(frozen=True, slots=True)
class StepRecord:
    step_id: str
    run_id: str
    name: str
    attempt: int
    status: StepStatus
    input_payload: JsonObject
    output_payload: JsonObject | None
    error: str
    started_at: str
    ended_at: str | None = None


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    checkpoint_id: int
    run_id: str
    step_number: int
    node: str
    state: JsonObject
    cursor: JsonObject
    created_at: str


@dataclass(frozen=True, slots=True)
class RunEvent:
    event_id: int
    run_id: str
    sequence: int
    event_type: str
    payload: JsonObject = field(default_factory=dict)
    created_at: str = ""


class RunStoreError(RuntimeError):
    """Base class for durable-run persistence failures."""


class RunNotFoundError(RunStoreError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run not found: {run_id}")
        self.run_id = run_id


class StepNotFoundError(RunStoreError):
    def __init__(self, step_id: str) -> None:
        super().__init__(f"step not found: {step_id}")
        self.step_id = step_id


class InvalidRunTransitionError(RunStoreError):
    """The requested lifecycle transition is not legal from current state."""


class RunCancellationRequestedError(RunStoreError):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"cancellation requested for run: {run_id}")
        self.run_id = run_id


class CheckpointConflictError(RunStoreError):
    """A checkpoint step is immutable and already has different content."""


class BudgetExceededError(RunStoreError):
    """A budget reservation would exceed one hard run limit."""

    def __init__(
        self,
        *,
        run_id: str,
        dimension: str,
        used: int | float,
        requested: int | float,
        limit: int | float,
    ) -> None:
        self.run_id = run_id
        self.dimension = dimension
        self.used = used
        self.requested = requested
        self.limit = limit
        super().__init__(
            f"run {run_id} budget exceeded for {dimension}: "
            f"used={used}, requested={requested}, limit={limit}"
        )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected int or None, got {type(value).__name__}")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected number or None, got {type(value).__name__}")
    return float(value)
