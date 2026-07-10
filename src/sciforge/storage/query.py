"""Typed search policies shared by SQLite and tenant-scoped readers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class QualityGateFilter:
    """Versioned publication gate applied inside a storage query.

    ``allow_legacy_when_unscored`` is a whole-corpus migration fallback: all
    rows remain visible only while no evaluation exists for the selected item
    kind/rubric/source.  Once evaluation starts, an individual unscored row is
    not silently treated as eligible.
    """

    item_kind: Literal["knowledge", "question"]
    rubric_version: str = "v1"
    source: str = "pipeline_auto"
    extraction_min: float = 0.7
    solution_min: float = 0.6
    allow_legacy_when_unscored: bool = True

    def __post_init__(self) -> None:
        if not self.rubric_version.strip():
            raise ValueError("rubric_version must not be empty")
        if not self.source.strip():
            raise ValueError("evaluation source must not be empty")
        for name in ("extraction_min", "solution_min"):
            value = float(getattr(self, name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


_EXACT_FILTER_COLUMNS = frozenset(
    {
        "discipline",
        "kp_type",
        "question_type",
        "subject",
        "source_file",
        "content_hash",
    }
)


def compile_search_filters(filters: dict[str, object]) -> tuple[list[str], list[object]]:
    """Compile the repository's allow-listed filters into SQL and parameters."""
    conditions: list[str] = []
    params: list[object] = []
    for key, value in filters.items():
        if key == "difficulty_range" and isinstance(value, (list, tuple)) and len(value) >= 2:
            conditions.append("difficulty BETWEEN ? AND ?")
            params.extend(value[:2])
        elif key == "exclude_ids" and isinstance(value, (list, set, tuple)):
            ids = list(value)
            if ids:
                placeholders = ",".join("?" for _ in ids)
                conditions.append(f"id NOT IN ({placeholders})")
                params.extend(ids)
        elif key in _EXACT_FILTER_COLUMNS:
            conditions.append(f"{key} = ?")
            params.append(value)
        elif key == "quality_gate" and isinstance(value, QualityGateFilter):
            clause, clause_params = _quality_gate_clause(value)
            conditions.append(clause)
            params.extend(clause_params)
    return conditions, params


def _quality_gate_clause(policy: QualityGateFilter) -> tuple[str, list[object]]:
    identity = [policy.item_kind, policy.rubric_version, policy.source]
    eligible = (
        "EXISTS (SELECT 1 FROM item_evaluations AS ie "
        "WHERE ie.item_id = id AND ie.item_kind = ? AND ie.rubric_version = ? "
        "AND ie.source = ? AND ie.extraction_quality >= ? "
        "AND ie.solution_completeness >= ?)"
    )
    eligible_params: list[object] = [
        *identity,
        policy.extraction_min,
        policy.solution_min,
    ]
    if not policy.allow_legacy_when_unscored:
        return eligible, eligible_params
    no_evaluations = (
        "NOT EXISTS (SELECT 1 FROM item_evaluations AS population "
        "WHERE population.item_kind = ? AND population.rubric_version = ? "
        "AND population.source = ?)"
    )
    return f"({no_evaluations} OR {eligible})", [*identity, *eligible_params]


__all__ = ["QualityGateFilter", "compile_search_filters"]
