"""Typed search policies shared by SQLite and tenant-scoped readers."""

from __future__ import annotations

from dataclasses import dataclass, replace
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
        "ingestion_status",
    }
)


def compile_search_filters(
    filters: dict[str, object],
    *,
    expected_item_kind: Literal["knowledge", "question"] | None = None,
) -> tuple[list[str], list[object]]:
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
        elif key == "text_query" and isinstance(value, str) and value.strip():
            needle = "%" + _escape_like(value.strip()) + "%"
            conditions.append(
                "(content LIKE ? ESCAPE '\\' OR number LIKE ? ESCAPE '\\' "
                "OR subject LIKE ? ESCAPE '\\' OR taxonomy_path LIKE ? ESCAPE '\\' "
                "OR tags LIKE ? ESCAPE '\\')"
            )
            params.extend([needle] * 5)
        elif key == "quality_gate" and isinstance(value, QualityGateFilter):
            if expected_item_kind is not None and value.item_kind != expected_item_kind:
                raise ValueError(
                    "quality gate item_kind does not match search table: "
                    f"expected {expected_item_kind!r}, got {value.item_kind!r}"
                )
            clause, clause_params = _quality_gate_clause(value)
            conditions.append(clause)
            params.extend(clause_params)
    return conditions, params


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


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
    accepted = "ingestion_status = 'accepted'"
    if not policy.allow_legacy_when_unscored:
        return f"({accepted} AND {eligible})", eligible_params
    no_evaluations = (
        "NOT EXISTS (SELECT 1 FROM item_evaluations AS population "
        "WHERE population.item_kind = ? AND population.rubric_version = ? "
        "AND population.source = ?)"
    )
    return (
        f"({accepted} AND ({no_evaluations} OR {eligible}))",
        [*identity, *eligible_params],
    )


def require_scored_quality_gate(filters: dict[str, object]) -> dict[str, object]:
    """Disable corpus-wide legacy fallback at tenant boundaries.

    The legacy population check is intentionally global for offline single-corpus
    migration. In a shared database that would let one tenant's evaluation state
    change another tenant's availability, so hosted reads require explicit scores.
    """
    policy = filters.get("quality_gate")
    if not isinstance(policy, QualityGateFilter) or not policy.allow_legacy_when_unscored:
        return filters
    scoped = dict(filters)
    scoped["quality_gate"] = replace(policy, allow_legacy_when_unscored=False)
    return scoped


__all__ = [
    "QualityGateFilter",
    "compile_search_filters",
    "require_scored_quality_gate",
]
