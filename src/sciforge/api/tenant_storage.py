"""Tenant-scoped view over the shared question bank.

The CLI ``serve`` command historically injects a regular
``SQLiteStorageBackend`` into API/QGen code.  That backend is intentionally
unscoped for offline pipeline maintenance, so exposing it directly from an
authenticated request would make every tenant's generated questions visible.

This adapter keeps the offline backend unchanged and applies the API boundary's
visibility rule to every question read:

``question.tenant_id == current_tenant OR question.tenant_id == ''``

For the regular SQLite backend the predicate is evaluated by a dedicated
read-only connection *before* ``ORDER BY``/``LIMIT``.  This is important both
for confidentiality and for correct pagination when the database contains many
questions owned by other tenants.  Knowledge points remain shared public data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from sciforge.core.models import KnowledgePoint, Question


class TenantStorageConfigurationError(RuntimeError):
    """Raised when an authenticated storage cannot enforce tenant filtering."""


_EXACT_FILTER_COLUMNS = {
    "discipline",
    "question_type",
    "subject",
    "source_file",
    "content_hash",
}


def _build_filter_clause(filters: dict[str, object]) -> tuple[list[str], list[object]]:
    """Compile the same safe question filters as ``SQLiteStorageBackend``."""
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
    return conditions, params


class TenantScopedStorageView:
    """Fail-closed question view for one authenticated tenant.

    ``storage`` remains the owner of its main connection.  ``close()`` only
    closes this view's optional read-only SQLite connection; callers should
    continue closing an owned base storage separately.
    """

    def __init__(
        self,
        storage: Any,
        tenant_id: str,
        *,
        include_public: bool = True,
    ) -> None:
        if not tenant_id:
            raise ValueError("TenantScopedStorageView requires a non-empty tenant_id")
        self._storage = storage
        self.tenant_id = tenant_id
        self.include_public = include_public
        self._reader: sqlite3.Connection | None = None

        db_path = getattr(storage, "db_path", None)
        if isinstance(db_path, (str, Path)):
            path = Path(db_path).resolve()
            uri = f"file:{quote(str(path), safe='/')}?mode=ro"
            self._reader = sqlite3.connect(
                uri,
                uri=True,
                check_same_thread=False,
            )
            self._reader.row_factory = sqlite3.Row
            self._reader.execute("PRAGMA query_only = ON")
            columns = {str(row[1]) for row in self._reader.execute("PRAGMA table_info(questions)")}
            if "tenant_id" not in columns:
                self._reader.close()
                self._reader = None
                raise TenantStorageConfigurationError(
                    "questions table has no tenant_id column; authenticated reads are disabled"
                )
        else:
            tenant_search = getattr(storage, "search_questions_for_tenant", None)
            if not callable(tenant_search):
                raise TenantStorageConfigurationError(
                    "storage must expose db_path or search_questions_for_tenant()"
                )

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def __enter__(self) -> TenantScopedStorageView:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _visibility_sql(self, *, column: str = "tenant_id") -> tuple[str, list[object]]:
        if self.include_public:
            return f"({column} = ? OR {column} = '')", [self.tenant_id]
        return f"{column} = ?", [self.tenant_id]

    def _is_visible(self, question: Question) -> bool:
        owner = question.tenant_id or ""
        return owner == self.tenant_id or (self.include_public and owner == "")

    def search_questions(self, filters: dict[str, object]) -> list[str]:
        """Search only public/current-tenant questions, filtering before LIMIT."""
        if self._reader is None:
            search = getattr(self._storage, "search_questions_for_tenant")
            result = search(
                filters,
                tenant_id=self.tenant_id,
                include_public=self.include_public,
            )
            return [str(item_id) for item_id in result]

        conditions, params = _build_filter_clause(filters)
        visibility, visibility_params = self._visibility_sql()
        conditions.append(visibility)
        params.extend(visibility_params)
        where = " AND ".join(conditions)
        rows = self._reader.execute(
            f"SELECT id FROM questions WHERE {where} ORDER BY order_index LIMIT 500",  # noqa: S608
            params,
        ).fetchall()
        return [str(row["id"]) for row in rows]

    def _visible_id_set(self, ids: list[str]) -> set[str]:
        if not ids:
            return set()
        if self._reader is None:
            questions = self._storage.load_questions_by_ids(ids)
            return {q.id for q in questions if self._is_visible(q)}

        visible: set[str] = set()
        visibility, visibility_params = self._visibility_sql()
        # Stay below SQLite's conservative 999-parameter default.
        for offset in range(0, len(ids), 900):
            chunk = ids[offset : offset + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = self._reader.execute(
                f"SELECT id FROM questions WHERE id IN ({placeholders}) "  # noqa: S608
                f"AND {visibility}",
                [*chunk, *visibility_params],
            ).fetchall()
            visible.update(str(row["id"]) for row in rows)
        return visible

    def load_questions_by_ids(self, ids: list[str]) -> list[Question]:
        """Bulk load while preserving caller order and suppressing foreign IDs."""
        visible = self._visible_id_set(ids)
        ordered_ids = [item_id for item_id in ids if item_id in visible]
        loaded = self._storage.load_questions_by_ids(ordered_ids)
        by_id = {question.id: question for question in loaded if self._is_visible(question)}
        return [by_id[item_id] for item_id in ordered_ids if item_id in by_id]

    def load_question(self, id: str) -> Question:
        """Load a visible question; foreign IDs are indistinguishable from missing IDs."""
        if id not in self._visible_id_set([id]):
            raise FileNotFoundError(f"Question {id} not found")
        question = self._storage.load_question(id)
        if not self._is_visible(question):  # defence in depth for non-SQLite delegates
            raise FileNotFoundError(f"Question {id} not found")
        return cast("Question", question)

    def question_count(self) -> int:
        if self._reader is None:
            counter = getattr(self._storage, "count_questions_for_tenant", None)
            if not callable(counter):
                return len(self.search_questions({}))
            return int(counter(self.tenant_id, include_public=self.include_public))
        visibility, params = self._visibility_sql()
        row = self._reader.execute(
            f"SELECT COUNT(*) AS n FROM questions WHERE {visibility}",  # noqa: S608
            params,
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def save_question(self, question: Question) -> str:
        """Generated questions written through the API always belong to this tenant."""
        if question.tenant_id not in ("", self.tenant_id):
            raise PermissionError("cannot write a question owned by another tenant")
        scoped = question.model_copy(update={"tenant_id": self.tenant_id})
        return str(self._storage.save_question(scoped))

    def save_batch(
        self,
        kps: list[KnowledgePoint],
        questions: list[Question],
    ) -> int:
        scoped_questions: list[Question] = []
        for question in questions:
            if question.tenant_id not in ("", self.tenant_id):
                raise PermissionError("cannot write a question owned by another tenant")
            scoped_questions.append(question.model_copy(update={"tenant_id": self.tenant_id}))
        return int(self._storage.save_batch(kps, scoped_questions))

    # Knowledge points are deliberately shared public corpus.
    def search_knowledge(self, filters: dict[str, object]) -> list[str]:
        return list(self._storage.search_knowledge(filters))

    def load_knowledge_by_ids(self, ids: list[str]) -> list[KnowledgePoint]:
        return list(self._storage.load_knowledge_by_ids(ids))

    def load_knowledge_point(self, id: str) -> KnowledgePoint:
        return cast("KnowledgePoint", self._storage.load_knowledge_point(id))

    def knowledge_count(self) -> int:
        return int(self._storage.knowledge_count())

    def __getattr__(self, name: str) -> Any:
        # Fail closed for unwrapped question APIs.  Non-question capabilities
        # (for example concept graph reads) may still delegate transparently.
        if "question" in name or name in {"search_fulltext", "find_duplicates"}:
            raise AttributeError(f"tenant-scoped question method is not exposed: {name}")
        return getattr(self._storage, name)
