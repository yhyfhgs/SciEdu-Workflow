"""ConversationStore persistence, integrity and tenant-boundary tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sciforge.api.conversations import ConversationStore


def test_store_rejects_ephemeral_or_empty_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="file path"):
        ConversationStore(":memory:")

    store = ConversationStore(tmp_path / "conversations.db")
    with pytest.raises(ValueError, match="tenant_id"):
        store.create_conversation("  ")
    with pytest.raises(ValueError, match="conversation_id"):
        store.create_conversation("tenant-a", conversation_id="  ")


def test_messages_artifacts_and_links_roundtrip_with_json_metadata(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    conversation = store.create_conversation(
        "tenant-a",
        title="  Analysis practice  ",
        discipline="math",
        conversation_id="conversation-a",
    )
    user = store.add_message(
        conversation.conversation_id,
        "tenant-a",
        role="user",
        content="Create a compactness problem",
        mode="new",
        run_id="run-a",
        metadata={"topics": ["analysis:分析"], "difficulty": 7},
        message_id="message-user",
    )
    link = store.link_run(
        conversation.conversation_id,
        "tenant-a",
        "run-a",
        user_message_id=user.message_id,
    )
    assistant = store.add_message(
        conversation.conversation_id,
        "tenant-a",
        role="assistant",
        content="Generated result",
        run_id="run-a",
        metadata={"verified": True},
        message_id="message-assistant",
    )
    artifact = store.add_artifact(
        conversation.conversation_id,
        "tenant-a",
        kind="generated_question",
        name="question.json",
        run_id="run-a",
        payload={"question": {"content": "Prove the claim"}, "passed": True},
        artifact_id="artifact-a",
    )

    assert conversation.title == "Analysis practice"
    assert [item.message_id for item in store.list_messages("conversation-a", "tenant-a")] == [
        user.message_id,
        assistant.message_id,
    ]
    assert store.list_messages("conversation-a", "tenant-a")[0].metadata == {
        "difficulty": 7,
        "topics": ["analysis:分析"],
    }
    assert store.get_run_link("run-a", "tenant-a") == link
    assert store.list_artifacts("conversation-a", "tenant-a") == [artifact]

    reopened = ConversationStore(tmp_path / "conversations.db")
    assert reopened.get_conversation("conversation-a", "tenant-a") == store.get_conversation(
        "conversation-a", "tenant-a"
    )
    assert reopened.list_artifacts("conversation-a", "tenant-a")[0].payload["passed"] is True


def test_every_read_and_child_write_is_tenant_scoped(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("tenant-a", conversation_id="conversation-a")
    store.create_conversation("tenant-b", conversation_id="conversation-b")
    store.add_message(
        "conversation-a",
        "tenant-a",
        role="user",
        content="private A",
        run_id="run-a",
    )
    store.link_run("conversation-a", "tenant-a", "run-a")
    store.add_artifact(
        "conversation-a",
        "tenant-a",
        kind="evaluation",
        payload={"private": "A"},
        run_id="run-a",
    )

    assert store.get_conversation("conversation-a", "tenant-b") is None
    assert [item.conversation_id for item in store.list_conversations("tenant-b")] == [
        "conversation-b"
    ]
    assert store.list_messages("conversation-a", "tenant-b") == []
    assert store.get_run_link("run-a", "tenant-b") is None
    assert store.list_artifacts("conversation-a", "tenant-b") == []

    with pytest.raises(LookupError, match="conversation not found"):
        store.add_message("conversation-a", "tenant-b", role="user", content="cross-tenant write")
    with pytest.raises(LookupError, match="conversation not found"):
        store.link_run("conversation-a", "tenant-b", "run-b")
    with pytest.raises(LookupError, match="conversation not found"):
        store.add_artifact("conversation-a", "tenant-b", kind="leak", payload={"x": 1})


def test_invalid_roles_limits_and_duplicate_ids_fail_atomically(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("tenant-a", conversation_id="conversation-a")

    with pytest.raises(ValueError, match="unsupported message role"):
        store.add_message(
            "conversation-a", "tenant-a", role="tool", content="not a public chat role"
        )
    with pytest.raises(ValueError, match="positive"):
        store.list_conversations("tenant-a", limit=0)
    with pytest.raises(ValueError, match="positive"):
        store.list_messages("conversation-a", "tenant-a", limit=0)
    with pytest.raises(ValueError, match="positive"):
        store.list_recent_messages("conversation-a", "tenant-a", limit=0)
    with pytest.raises(ValueError, match="positive"):
        store.list_artifacts("conversation-a", "tenant-a", limit=0)

    with pytest.raises(sqlite3.IntegrityError):
        store.create_conversation("tenant-b", conversation_id="conversation-a")
    assert store.get_conversation("conversation-a", "tenant-a") is not None
    assert store.get_conversation("conversation-a", "tenant-b") is None


def test_recent_messages_reads_newest_page_but_returns_chronological_order(
    tmp_path: Path,
) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("tenant-a", conversation_id="conversation-a")
    for index in range(12):
        store.add_message(
            "conversation-a",
            "tenant-a",
            role="user" if index % 2 == 0 else "assistant",
            content=f"message-{index:02d}",
            message_id=f"message-{index:02d}",
        )

    recent = store.list_recent_messages("conversation-a", "tenant-a", limit=3)

    assert [message.content for message in recent] == [
        "message-09",
        "message-10",
        "message-11",
    ]
    assert store.list_recent_messages("missing", "tenant-a") == []


def test_schema_version_mismatch_is_rejected(tmp_path: Path) -> None:
    db_path = tmp_path / "conversations.db"
    ConversationStore(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE conversation_meta SET value = '999' WHERE key = 'schema_version'")

    with pytest.raises(RuntimeError, match="unsupported conversation schema version"):
        ConversationStore(db_path)


def test_run_children_require_same_conversation_link_and_user_message(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("tenant-a", conversation_id="conversation-a")
    store.create_conversation("tenant-a", conversation_id="conversation-b")
    foreign_message = store.add_message(
        "conversation-b",
        "tenant-a",
        role="user",
        content="belongs to B",
        run_id="run-b",
        message_id="message-b",
    )

    with pytest.raises(LookupError, match="user message"):
        store.link_run(
            "conversation-a",
            "tenant-a",
            "run-a",
            user_message_id=foreign_message.message_id,
        )
    with pytest.raises(LookupError, match="run is not linked"):
        store.add_message(
            "conversation-a",
            "tenant-a",
            role="assistant",
            content="orphan",
            run_id="run-a",
        )
    with pytest.raises(LookupError, match="run is not linked"):
        store.add_artifact(
            "conversation-a",
            "tenant-a",
            kind="orphan",
            payload={},
            run_id="run-a",
        )


def test_non_finite_json_is_rejected_without_partial_write(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("local", conversation_id="conversation")

    with pytest.raises(ValueError, match="Out of range float values"):
        store.add_message(
            "conversation",
            "local",
            role="user",
            content="bad metadata",
            metadata={"score": float("nan")},
        )

    assert store.list_messages("conversation", "local") == []


def test_user_command_and_run_link_are_atomic_on_duplicate_run(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("local", conversation_id="first")
    store.create_conversation("local", conversation_id="second")
    message, link = store.add_user_message_and_link_run(
        "first",
        "local",
        "shared-run",
        content="first command",
        message_id="first-message",
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.add_user_message_and_link_run(
            "second",
            "local",
            "shared-run",
            content="must roll back",
            message_id="rolled-back-message",
        )

    assert link.user_message_id == message.message_id
    assert store.list_messages("second", "local") == []
    assert store.get_run_link("shared-run", "local") == link


def test_assistant_result_is_atomic_and_idempotent(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("local", conversation_id="conversation")
    store.add_user_message_and_link_run(
        "conversation",
        "local",
        "run",
        content="generate",
    )

    first_message, first_artifact = store.record_assistant_result(
        "conversation",
        "local",
        "run",
        content="verified result",
        artifact_kind="generated_question",
        artifact_payload={"passed": True, "score": 9},
    )
    updated_after_first_delivery = store.get_conversation("conversation", "local").updated_at
    second_message, second_artifact = store.record_assistant_result(
        "conversation",
        "local",
        "run",
        content="verified result",
        artifact_kind="generated_question",
        artifact_payload={"score": 9, "passed": True},
    )

    assert second_message == first_message
    assert second_artifact == first_artifact
    messages = store.list_messages("conversation", "local")
    assert [item.role for item in messages] == ["user", "assistant"]
    assert messages[-1] == first_message
    assert store.list_artifacts("conversation", "local") == [first_artifact]
    assert (
        store.get_conversation("conversation", "local").updated_at == updated_after_first_delivery
    )


def test_assistant_result_rejects_divergent_retry_without_partial_write(
    tmp_path: Path,
) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("local", conversation_id="conversation")
    store.add_user_message_and_link_run(
        "conversation",
        "local",
        "run",
        content="generate",
    )
    message, artifact = store.record_assistant_result(
        "conversation",
        "local",
        "run",
        content="original",
        artifact_kind="solution",
        artifact_payload={"answer": 42},
    )

    with pytest.raises(RuntimeError, match="diverged"):
        store.record_assistant_result(
            "conversation",
            "local",
            "run",
            content="different",
            artifact_kind="solution",
            artifact_payload={"answer": 41},
        )

    assert store.list_messages("conversation", "local")[-1] == message
    assert store.list_artifacts("conversation", "local") == [artifact]


def test_run_links_support_complete_keyset_pagination(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path / "conversations.db")
    store.create_conversation("local", conversation_id="conversation")
    for index in range(3):
        store.add_user_message_and_link_run(
            "conversation",
            "local",
            f"run-{index}",
            content=f"command {index}",
        )

    observed = []
    after_created_at = ""
    after_run_id = ""
    while True:
        page = store.list_run_links(
            limit=1,
            after_created_at=after_created_at,
            after_run_id=after_run_id,
        )
        if not page:
            break
        observed.extend(page)
        after_created_at = page[-1].created_at
        after_run_id = page[-1].run_id

    assert {link.run_id for link in observed} == {"run-0", "run-1", "run-2"}
    assert len(observed) == 3
