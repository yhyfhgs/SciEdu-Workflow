"""Tenant-scoped persistence for the conversational generation product.

Conversation data is deliberately separate from ``SQLiteRunStore``.  This
store owns product-facing messages and artifacts, while the runtime store stays
the sole source of truth for execution status, checkpoints, budgets and events.
Every public lookup includes ``tenant_id`` so a guessed identifier cannot be
used to probe another tenant's data.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

JsonObject = dict[str, Any]

_BUSY_TIMEOUT_MS = 10_000
_SCHEMA_VERSION = 1
_RESULT_ID_NAMESPACE = uuid.UUID("f05a9241-2641-48cf-95f8-75e6468aaef7")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    discipline TEXT NOT NULL DEFAULT 'math',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(conversation_id, tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_conversations_tenant_updated
    ON conversations(tenant_id, updated_at DESC, conversation_id);

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id, tenant_id)
        REFERENCES conversations(conversation_id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON conversation_messages(tenant_id, conversation_id, created_at, message_id);
CREATE INDEX IF NOT EXISTS idx_messages_run
    ON conversation_messages(tenant_id, run_id) WHERE run_id != '';

CREATE TABLE IF NOT EXISTS conversation_run_links (
    run_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    user_message_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id, tenant_id)
        REFERENCES conversations(conversation_id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_run_links_conversation
    ON conversation_run_links(tenant_id, conversation_id, created_at);

CREATE TABLE IF NOT EXISTS conversation_artifacts (
    artifact_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (conversation_id, tenant_id)
        REFERENCES conversations(conversation_id, tenant_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_artifacts_conversation
    ON conversation_artifacts(tenant_id, conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_run
    ON conversation_artifacts(tenant_id, run_id) WHERE run_id != '';
"""


@dataclass(frozen=True, slots=True)
class ConversationRecord:
    conversation_id: str
    tenant_id: str
    title: str
    discipline: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class MessageRecord:
    message_id: str
    conversation_id: str
    tenant_id: str
    role: str
    content: str
    mode: str
    run_id: str
    metadata: JsonObject = field(default_factory=dict)
    created_at: str = ""


@dataclass(frozen=True, slots=True)
class RunLinkRecord:
    run_id: str
    conversation_id: str
    tenant_id: str
    user_message_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    conversation_id: str
    tenant_id: str
    run_id: str
    kind: str
    name: str
    payload: JsonObject = field(default_factory=dict)
    created_at: str = ""


class ConversationStore:
    """Concurrency-safe SQLite store with tenant-scoped reads and writes."""

    def __init__(self, db_path: Path | str) -> None:
        if str(db_path) == ":memory:":
            raise ValueError("ConversationStore requires a file path, not :memory:")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create_conversation(
        self,
        tenant_id: str,
        *,
        title: str = "",
        discipline: str = "math",
        conversation_id: str | None = None,
    ) -> ConversationRecord:
        tenant = _require_text(tenant_id, "tenant_id")
        identifier = _require_text(conversation_id or str(uuid.uuid4()), "conversation_id")
        with self._write() as conn:
            now = _utc_now()
            conn.execute(
                "INSERT INTO conversations "
                "(conversation_id, tenant_id, title, discipline, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (identifier, tenant, title.strip(), discipline.strip() or "math", now, now),
            )
            row = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ? AND tenant_id = ?",
                (identifier, tenant),
            ).fetchone()
        if row is None:
            raise RuntimeError("conversation transaction lost its inserted row")
        return _conversation_from_row(row)

    def get_conversation(
        self, conversation_id: str, tenant_id: str
    ) -> ConversationRecord | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE conversation_id = ? AND tenant_id = ?",
                (conversation_id, tenant_id),
            ).fetchone()
        return None if row is None else _conversation_from_row(row)

    def list_conversations(self, tenant_id: str, *, limit: int = 50) -> list[ConversationRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? "
                "ORDER BY updated_at DESC, conversation_id LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [_conversation_from_row(row) for row in rows]

    def add_message(
        self,
        conversation_id: str,
        tenant_id: str,
        *,
        role: str,
        content: str,
        mode: str = "",
        run_id: str = "",
        metadata: Mapping[str, Any] | None = None,
        message_id: str | None = None,
    ) -> MessageRecord:
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"unsupported message role: {role}")
        identifier = _require_text(message_id or str(uuid.uuid4()), "message_id")
        payload = _json_dumps(metadata)
        with self._write() as conn:
            now = _utc_now()
            self._require_conversation(conn, conversation_id, tenant_id)
            if run_id and role != "user":
                self._require_run_link(conn, conversation_id, tenant_id, run_id)
            conn.execute(
                "INSERT INTO conversation_messages "
                "(message_id, conversation_id, tenant_id, role, content, mode, run_id, "
                "metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    conversation_id,
                    tenant_id,
                    role,
                    content,
                    mode,
                    run_id,
                    payload,
                    now,
                ),
            )
            self._touch_conversation(conn, conversation_id, tenant_id, now)
            row = conn.execute(
                "SELECT * FROM conversation_messages WHERE message_id = ? AND tenant_id = ?",
                (identifier, tenant_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("message transaction lost its inserted row")
        return _message_from_row(row)

    def list_messages(
        self,
        conversation_id: str,
        tenant_id: str,
        *,
        limit: int = 500,
    ) -> list[MessageRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._read() as conn:
            if not self._conversation_exists(conn, conversation_id, tenant_id):
                return []
            rows = conn.execute(
                "SELECT * FROM conversation_messages "
                "WHERE conversation_id = ? AND tenant_id = ? "
                "ORDER BY created_at, message_id LIMIT ?",
                (conversation_id, tenant_id, limit),
            ).fetchall()
        return [_message_from_row(row) for row in rows]

    def add_user_message_and_link_run(
        self,
        conversation_id: str,
        tenant_id: str,
        run_id: str,
        *,
        content: str,
        mode: str = "",
        metadata: Mapping[str, Any] | None = None,
        message_id: str | None = None,
    ) -> tuple[MessageRecord, RunLinkRecord]:
        """Atomically persist the user command and its durable-run audit link."""

        identifier = _require_text(message_id or str(uuid.uuid4()), "message_id")
        run_identifier = _require_text(run_id, "run_id")
        payload = _json_dumps(metadata)
        with self._write() as conn:
            now = _utc_now()
            self._require_conversation(conn, conversation_id, tenant_id)
            conn.execute(
                "INSERT INTO conversation_messages "
                "(message_id, conversation_id, tenant_id, role, content, mode, run_id, "
                "metadata_json, created_at) VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?)",
                (
                    identifier,
                    conversation_id,
                    tenant_id,
                    content,
                    mode,
                    run_identifier,
                    payload,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO conversation_run_links "
                "(run_id, conversation_id, tenant_id, user_message_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_identifier, conversation_id, tenant_id, identifier, now),
            )
            self._touch_conversation(conn, conversation_id, tenant_id, now)
            message_row = conn.execute(
                "SELECT * FROM conversation_messages WHERE message_id = ? AND tenant_id = ?",
                (identifier, tenant_id),
            ).fetchone()
            link_row = conn.execute(
                "SELECT * FROM conversation_run_links WHERE run_id = ? AND tenant_id = ?",
                (run_identifier, tenant_id),
            ).fetchone()
        if message_row is None or link_row is None:
            raise RuntimeError("conversation command transaction lost its inserted rows")
        return _message_from_row(message_row), _run_link_from_row(link_row)

    def link_run(
        self,
        conversation_id: str,
        tenant_id: str,
        run_id: str,
        *,
        user_message_id: str = "",
    ) -> RunLinkRecord:
        identifier = _require_text(run_id, "run_id")
        with self._write() as conn:
            now = _utc_now()
            self._require_conversation(conn, conversation_id, tenant_id)
            if user_message_id:
                message = conn.execute(
                    "SELECT role, run_id FROM conversation_messages "
                    "WHERE message_id = ? AND conversation_id = ? AND tenant_id = ?",
                    (user_message_id, conversation_id, tenant_id),
                ).fetchone()
                if message is None:
                    raise LookupError("run user message not found in conversation")
                if str(message["role"]) != "user":
                    raise ValueError("run link must reference a user message")
                message_run_id = str(message["run_id"])
                if message_run_id and message_run_id != identifier:
                    raise ValueError("user message is bound to a different run")
            conn.execute(
                "INSERT INTO conversation_run_links "
                "(run_id, conversation_id, tenant_id, user_message_id, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (identifier, conversation_id, tenant_id, user_message_id, now),
            )
            self._touch_conversation(conn, conversation_id, tenant_id, now)
            row = conn.execute(
                "SELECT * FROM conversation_run_links WHERE run_id = ? AND tenant_id = ?",
                (identifier, tenant_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("run-link transaction lost its inserted row")
        return _run_link_from_row(row)

    def get_run_link(self, run_id: str, tenant_id: str) -> RunLinkRecord | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_run_links WHERE run_id = ? AND tenant_id = ?",
                (run_id, tenant_id),
            ).fetchone()
        return None if row is None else _run_link_from_row(row)

    def list_run_links(
        self,
        *,
        limit: int = 1_000,
        after_created_at: str = "",
        after_run_id: str = "",
    ) -> list[RunLinkRecord]:
        """Keyset-page product-owned runs for trusted reconciliation."""
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM conversation_run_links "
                "WHERE created_at > ? OR (created_at = ? AND run_id > ?) "
                "ORDER BY created_at, run_id LIMIT ?",
                (after_created_at, after_created_at, after_run_id, limit),
            ).fetchall()
        return [_run_link_from_row(row) for row in rows]

    def has_assistant_result(
        self,
        conversation_id: str,
        tenant_id: str,
        run_id: str,
        *,
        artifact_kind: str,
    ) -> bool:
        """Return whether the stable atomic product pair exists for a run."""
        artifact_id = _stable_result_id("artifact", run_id, artifact_kind)
        message_id = _stable_result_id("message", run_id, "assistant.final")
        with self._read() as conn:
            artifact = conn.execute(
                "SELECT 1 FROM conversation_artifacts "
                "WHERE artifact_id = ? AND conversation_id = ? AND tenant_id = ? "
                "AND run_id = ? AND kind = ?",
                (
                    artifact_id,
                    conversation_id,
                    tenant_id,
                    run_id,
                    artifact_kind,
                ),
            ).fetchone()
            message = conn.execute(
                "SELECT 1 FROM conversation_messages "
                "WHERE message_id = ? AND conversation_id = ? AND tenant_id = ? "
                "AND run_id = ? AND role = 'assistant'",
                (message_id, conversation_id, tenant_id, run_id),
            ).fetchone()
        return artifact is not None and message is not None

    def add_artifact(
        self,
        conversation_id: str,
        tenant_id: str,
        *,
        kind: str,
        payload: Mapping[str, Any],
        run_id: str = "",
        name: str = "",
        artifact_id: str | None = None,
    ) -> ArtifactRecord:
        identifier = _require_text(artifact_id or str(uuid.uuid4()), "artifact_id")
        artifact_kind = _require_text(kind, "kind")
        payload_json = _json_dumps(payload)
        with self._write() as conn:
            now = _utc_now()
            self._require_conversation(conn, conversation_id, tenant_id)
            if run_id:
                self._require_run_link(conn, conversation_id, tenant_id, run_id)
            conn.execute(
                "INSERT INTO conversation_artifacts "
                "(artifact_id, conversation_id, tenant_id, run_id, kind, name, "
                "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    identifier,
                    conversation_id,
                    tenant_id,
                    run_id,
                    artifact_kind,
                    name,
                    payload_json,
                    now,
                ),
            )
            self._touch_conversation(conn, conversation_id, tenant_id, now)
            row = conn.execute(
                "SELECT * FROM conversation_artifacts "
                "WHERE artifact_id = ? AND tenant_id = ?",
                (identifier, tenant_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("artifact transaction lost its inserted row")
        return _artifact_from_row(row)

    def record_assistant_result(
        self,
        conversation_id: str,
        tenant_id: str,
        run_id: str,
        *,
        content: str,
        artifact_kind: str,
        artifact_payload: Mapping[str, Any],
        artifact_name: str = "",
    ) -> tuple[MessageRecord, ArtifactRecord]:
        """Atomically and idempotently publish one run's final product result.

        Stable identifiers make process-recovery delivery safe to retry.  A
        retry must describe the same immutable result; divergent content is an
        invariant violation rather than a silent overwrite.
        """

        run_identifier = _require_text(run_id, "run_id")
        kind = _require_text(artifact_kind, "artifact_kind")
        artifact_id = _stable_result_id("artifact", run_identifier, kind)
        message_id = _stable_result_id("message", run_identifier, "assistant.final")
        payload_json = _json_dumps(artifact_payload)
        metadata_json = _json_dumps(
            {"artifact_id": artifact_id, "artifact_kind": kind}
        )

        with self._write() as conn:
            now = _utc_now()
            self._require_conversation(conn, conversation_id, tenant_id)
            self._require_run_link(conn, conversation_id, tenant_id, run_identifier)
            artifact_insert = conn.execute(
                "INSERT INTO conversation_artifacts "
                "(artifact_id, conversation_id, tenant_id, run_id, kind, name, "
                "payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(artifact_id) DO NOTHING",
                (
                    artifact_id,
                    conversation_id,
                    tenant_id,
                    run_identifier,
                    kind,
                    artifact_name or kind,
                    payload_json,
                    now,
                ),
            )
            message_insert = conn.execute(
                "INSERT INTO conversation_messages "
                "(message_id, conversation_id, tenant_id, role, content, mode, run_id, "
                "metadata_json, created_at) VALUES (?, ?, ?, 'assistant', ?, '', ?, ?, ?) "
                "ON CONFLICT(message_id) DO NOTHING",
                (
                    message_id,
                    conversation_id,
                    tenant_id,
                    content,
                    run_identifier,
                    metadata_json,
                    now,
                ),
            )
            artifact_row = conn.execute(
                "SELECT * FROM conversation_artifacts "
                "WHERE artifact_id = ? AND conversation_id = ? AND tenant_id = ?",
                (artifact_id, conversation_id, tenant_id),
            ).fetchone()
            message_row = conn.execute(
                "SELECT * FROM conversation_messages "
                "WHERE message_id = ? AND conversation_id = ? AND tenant_id = ?",
                (message_id, conversation_id, tenant_id),
            ).fetchone()
            if artifact_row is None or message_row is None:
                raise RuntimeError("assistant-result transaction lost an inserted row")
            if (
                str(artifact_row["run_id"]) != run_identifier
                or str(artifact_row["kind"]) != kind
                or str(artifact_row["name"]) != (artifact_name or kind)
                or str(artifact_row["payload_json"]) != payload_json
                or str(message_row["run_id"]) != run_identifier
                or str(message_row["role"]) != "assistant"
                or str(message_row["content"]) != content
                or str(message_row["metadata_json"]) != metadata_json
            ):
                raise RuntimeError("assistant result retry diverged from the stored result")
            if artifact_insert.rowcount or message_insert.rowcount:
                self._touch_conversation(conn, conversation_id, tenant_id, now)

        return _message_from_row(message_row), _artifact_from_row(artifact_row)

    def list_artifacts(
        self,
        conversation_id: str,
        tenant_id: str,
        *,
        limit: int = 200,
    ) -> list[ArtifactRecord]:
        if limit < 1:
            raise ValueError("limit must be positive")
        with self._read() as conn:
            if not self._conversation_exists(conn, conversation_id, tenant_id):
                return []
            rows = conn.execute(
                "SELECT * FROM conversation_artifacts "
                "WHERE conversation_id = ? AND tenant_id = ? "
                "ORDER BY created_at, artifact_id LIMIT ?",
                (conversation_id, tenant_id, limit),
            ).fetchall()
        return [_artifact_from_row(row) for row in rows]

    def _initialize(self) -> None:
        conn = self._connect(configure_journal=True)
        try:
            conn.executescript(_SCHEMA)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM conversation_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO conversation_meta(key, value) VALUES ('schema_version', ?)",
                    (str(_SCHEMA_VERSION),),
                )
            elif int(row["value"]) != _SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported conversation schema version: {row['value']}"
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _connect(self, *, configure_journal: bool = False) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=_BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        if configure_journal:
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

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
    def _conversation_exists(
        conn: sqlite3.Connection, conversation_id: str, tenant_id: str
    ) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM conversations WHERE conversation_id = ? AND tenant_id = ?",
                (conversation_id, tenant_id),
            ).fetchone()
            is not None
        )

    @classmethod
    def _require_conversation(
        cls, conn: sqlite3.Connection, conversation_id: str, tenant_id: str
    ) -> None:
        if not cls._conversation_exists(conn, conversation_id, tenant_id):
            raise LookupError("conversation not found")

    @staticmethod
    def _require_run_link(
        conn: sqlite3.Connection,
        conversation_id: str,
        tenant_id: str,
        run_id: str,
    ) -> None:
        row = conn.execute(
            "SELECT 1 FROM conversation_run_links "
            "WHERE run_id = ? AND conversation_id = ? AND tenant_id = ?",
            (run_id, conversation_id, tenant_id),
        ).fetchone()
        if row is None:
            raise LookupError("run is not linked to conversation")

    @staticmethod
    def _touch_conversation(
        conn: sqlite3.Connection,
        conversation_id: str,
        tenant_id: str,
        timestamp: str,
    ) -> None:
        conn.execute(
            "UPDATE conversations "
            "SET updated_at = CASE WHEN updated_at < ? THEN ? ELSE updated_at END "
            "WHERE conversation_id = ? AND tenant_id = ?",
            (timestamp, timestamp, conversation_id, tenant_id),
        )


def _conversation_from_row(row: sqlite3.Row) -> ConversationRecord:
    return ConversationRecord(
        conversation_id=str(row["conversation_id"]),
        tenant_id=str(row["tenant_id"]),
        title=str(row["title"]),
        discipline=str(row["discipline"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _message_from_row(row: sqlite3.Row) -> MessageRecord:
    return MessageRecord(
        message_id=str(row["message_id"]),
        conversation_id=str(row["conversation_id"]),
        tenant_id=str(row["tenant_id"]),
        role=str(row["role"]),
        content=str(row["content"]),
        mode=str(row["mode"]),
        run_id=str(row["run_id"]),
        metadata=_json_load_object(str(row["metadata_json"])),
        created_at=str(row["created_at"]),
    )


def _run_link_from_row(row: sqlite3.Row) -> RunLinkRecord:
    return RunLinkRecord(
        run_id=str(row["run_id"]),
        conversation_id=str(row["conversation_id"]),
        tenant_id=str(row["tenant_id"]),
        user_message_id=str(row["user_message_id"]),
        created_at=str(row["created_at"]),
    )


def _artifact_from_row(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=str(row["artifact_id"]),
        conversation_id=str(row["conversation_id"]),
        tenant_id=str(row["tenant_id"]),
        run_id=str(row["run_id"]),
        kind=str(row["kind"]),
        name=str(row["name"]),
        payload=_json_load_object(str(row["payload_json"])),
        created_at=str(row["created_at"]),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_text(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be non-empty")
    return normalized


def _stable_result_id(record_kind: str, run_id: str, suffix: str) -> str:
    return str(uuid.uuid5(_RESULT_ID_NAMESPACE, f"{record_kind}:{run_id}:{suffix}"))


def _json_dumps(value: Mapping[str, Any] | None) -> str:
    return json.dumps(
        dict(value or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_load_object(value: str) -> JsonObject:
    parsed = json.loads(
        value,
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON number: {token}")
        ),
    )
    if not isinstance(parsed, dict):
        raise ValueError("stored JSON must be an object")
    return cast(JsonObject, parsed)


__all__ = [
    "ArtifactRecord",
    "ConversationRecord",
    "ConversationStore",
    "MessageRecord",
    "RunLinkRecord",
]
