"""Conversation API integration: durable runs, SSE and quality-gated retrieval."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from sciforge.agents import QGenAgent, QGenOutput, SolveOutput
from sciforge.agents.runtime import RunStatus, SQLiteRunStore
from sciforge.api.app import create_app
from sciforge.api.conversation_routes import (
    _persist_generated_question,
    _stream_events,
    _temporary_verification_policy,
)
from sciforge.api.conversations import ConversationStore
from sciforge.core.models import (
    DifficultyScore,
    Discipline,
    GeneratedQuestion,
    KnowledgePoint,
    Question,
    Solution,
    VerificationEvidence,
    VerificationEvidenceStatus,
    VerificationResult,
)
from sciforge.pipeline.evaluation import evaluate_item_rows
from sciforge.storage.saas import SaasStore
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


@contextmanager
def _stores(tmp_path: Path) -> Iterator[tuple[ConversationStore, SQLiteRunStore]]:
    yield (
        ConversationStore(tmp_path / "conversations.db"),
        SQLiteRunStore(tmp_path / "runs.db"),
    )


def _noop_factory() -> Any:
    return lambda _discipline: MagicMock()


def _generated_output() -> QGenOutput:
    question = Question(
        id="generated-question",
        discipline=Discipline.MATH,
        subject="analysis:分析",
        content="设函数在闭区间连续，证明它在该区间上一致连续。",
        difficulty=7,
    )
    return QGenOutput(
        generated=GeneratedQuestion(
            question=question,
            solution=Solution(
                question_id=question.id,
                solution_latex="由紧致性取有限子覆盖，再取最小半径即可。",
                confidence="high",
            ),
            difficulty_score=DifficultyScore(overall=7.0),
            verification=VerificationResult(passed=True, checks={"review_passed": True}),
        ),
        rounds_used=1,
        candidates_tried=1,
    )


class _QGenFacade:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.inputs: list[Any] = []

    async def run(self, input: object) -> QGenOutput:
        self.inputs.append(input)
        if self.fail:
            raise RuntimeError("facade generation exploded")
        return _generated_output()


class _SolveFacade:
    async def run(self, input: Any) -> SolveOutput:
        question = input.question
        assert question is not None
        return SolveOutput(
            solution=Solution(
                question_id=question.id,
                solution_latex="逐步计算后得到 42。",
                confidence="high",
            ),
            confidence="high",
        )


class _MeteredClient:
    def __init__(self) -> None:
        self.call_count = 0
        self.total_tokens = 0


class _MeteredSolveFacade(_SolveFacade):
    def __init__(self, *, calls: int = 1, tokens: int = 42) -> None:
        self.llm = _MeteredClient()
        self.calls = calls
        self.tokens = tokens

    async def run(self, input: Any) -> SolveOutput:
        self.llm.call_count += self.calls
        self.llm.total_tokens += self.tokens
        return await super().run(input)


def _local_app(
    conversations: ConversationStore,
    runs: SQLiteRunStore,
    *,
    qgen_factory: Any | None = None,
    solve_factory: Any | None = None,
    storage_factory: Any | None = None,
) -> TestClient:
    return TestClient(
        create_app(
            qgen_factory or _noop_factory(),
            solve_factory or _noop_factory(),
            conversation_store=conversations,
            run_store=runs,
            storage_factory=storage_factory,
            serve_console=False,
        )
    )


def _create_local_conversation(client: TestClient, *, title: str = "Session") -> str:
    response = client.post(
        "/v1/conversations",
        json={"title": title, "discipline": "math"},
    )
    assert response.status_code == 200
    return str(response.json()["id"])


def _seed_owned_run(
    conversations: ConversationStore,
    runs: SQLiteRunStore,
    *,
    tenant_id: str = "local",
    status: RunStatus = RunStatus.PENDING,
) -> tuple[str, str]:
    conversation = conversations.create_conversation(tenant_id)
    run = runs.create_run(
        "test",
        {"input": True},
        metadata={
            "tenant_id": tenant_id,
            "conversation_id": conversation.conversation_id,
            "mode": "new",
        },
    )
    conversations.link_run(conversation.conversation_id, tenant_id, run.run_id)
    if status is RunStatus.RUNNING:
        runs.begin_run(run.run_id)
    elif status is RunStatus.SUCCEEDED:
        runs.begin_run(run.run_id)
        runs.complete_run(run.run_id, {"ok": True})
    return conversation.conversation_id, run.run_id


def test_open_mode_uses_stable_local_tenant_and_exposes_templates(tmp_path: Path) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(conversations, runs)
        first = _create_local_conversation(client, title="Local work")
        second = _create_local_conversation(client, title="Another local work")

        listed = client.get("/v1/conversations")
        templates = client.get("/v1/templates")

    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()["items"]} == {first, second}
    assert all(record.tenant_id == "local" for record in conversations.list_conversations("local"))
    assert templates.status_code == 200
    assert {item["id"] for item in templates.json()["items"]} == {
        "new",
        "evolve",
        "variant",
        "adjust_difficulty",
        "deepen",
        "solve",
        "evaluate",
    }


def test_message_inherits_conversation_discipline_and_validates_template_contract(
    tmp_path: Path,
) -> None:
    facade = _QGenFacade()
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: facade,
        )
        created = client.post(
            "/v1/conversations",
            json={"title": "Physics", "discipline": "physics"},
        )
        conversation_id = created.json()["id"]
        generated = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={
                "mode": "new",
                "content": "design a mechanics problem",
                "question_type": "calculation",
                "question_form": "choice",
            },
        )
        mismatch = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={
                "mode": "new",
                "content": "x",
                "template_id": "deepen",
            },
        )
        missing_difficulty = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"mode": "adjust_difficulty", "source_question_id": "q"},
        )

    assert generated.status_code == 202
    generation_request = facade.inputs[0].request
    assert generation_request.discipline is Discipline.PHYSICS
    assert generation_request.question_type == "calculation"
    assert generation_request.question_form == "choice"
    assert mismatch.status_code == 422
    assert missing_difficulty.status_code == 422


def test_message_persists_pinned_knowledge_ids_for_recovery_and_audit(
    tmp_path: Path,
) -> None:
    facade = _QGenFacade()
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: facade,
        )
        conversation_id = _create_local_conversation(client)
        accepted = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={
                "mode": "new",
                "content": "Use the pinned theorem",
                "knowledge_ids": ["  kp-compactness  "],
            },
        )
        messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]

    assert accepted.status_code == 202
    run = runs.require_run(accepted.json()["run_id"])
    assert run.metadata["knowledge_ids"] == ["kp-compactness"]
    assert run.input_payload["knowledge_ids"] == ["kp-compactness"]
    assert messages[0]["metadata"]["knowledge_ids"] == ["kp-compactness"]


@pytest.mark.parametrize(
    "knowledge_ids",
    [
        [f"kp-{index}" for index in range(21)],
        ["   "],
        ["x" * 201],
    ],
)
def test_message_rejects_invalid_pinned_knowledge_ids(
    tmp_path: Path,
    knowledge_ids: list[str],
) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(conversations, runs)
        conversation_id = _create_local_conversation(client)
        response = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={
                "mode": "new",
                "content": "Use pinned context",
                "knowledge_ids": knowledge_ids,
            },
        )

    assert response.status_code == 422


def test_generation_snapshots_recent_turns_into_durable_request_context(
    tmp_path: Path,
) -> None:
    facade = _QGenFacade()
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: facade,
        )
        conversation_id = _create_local_conversation(client)
        first = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"mode": "new", "content": "先出一道关于紧致性的基础题。"},
        )
        second = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"mode": "new", "content": "沿用刚才主题，但改成反例题。"},
        )

    assert first.status_code == 202
    assert second.status_code == 202
    assert facade.inputs[0].request.conversation_context == ""
    second_request = facade.inputs[1].request
    assert "[user/new]" in second_request.conversation_context
    assert "先出一道关于紧致性的基础题" in second_request.conversation_context
    assert "[assistant]" in second_request.conversation_context
    assert "沿用刚才主题" not in second_request.conversation_context
    persisted = runs.require_run(second.json()["run_id"]).input_payload
    assert persisted["conversation_context"] == second_request.conversation_context


@pytest.mark.parametrize(
    ("output_type", "expected", "excluded"),
    [
        ("question_solution", "## 解答", "__never__"),
        ("question_only", "设函数在闭区间连续", "## 解答"),
        ("structured_json", '"generated"', "## 题目"),
        ("lecture_note", "# 学习讲义", "__never__"),
    ],
)
def test_output_type_controls_assistant_rendering(
    tmp_path: Path,
    output_type: str,
    expected: str,
    excluded: str,
) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: _QGenFacade(),
        )
        conversation_id = _create_local_conversation(client)
        response = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={
                "mode": "new",
                "content": "compactness",
                "output_type": output_type,
                "template_id": "new",
            },
        )
        messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]

    assert response.status_code == 202
    assert expected in messages[-1]["content"]
    assert excluded not in messages[-1]["content"]


def test_strict_policy_rejects_non_configurable_generation_facade(tmp_path: Path) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: _QGenFacade(),
        )
        conversation_id = _create_local_conversation(client)
        response = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={
                "mode": "new",
                "content": "formal proof",
                "verification_policy": "strict",
            },
        )

    assert response.status_code == 503
    assert "真实 QGenAgent verifier" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {
            "mode": "new",
            "content": "calculate an integral",
            "question_type": "calculation",
            "verification_policy": "formal",
        },
        {
            "mode": "new",
            "content": "invalid legacy type",
            "question_type": "computational",
        },
        {
            "mode": "new",
            "content": "derive a physics law",
            "discipline": "physics",
            "question_type": "proof",
            "verification_policy": "formal",
        },
    ],
)
def test_message_rejects_inapplicable_formal_and_noncanonical_question_type(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: _QGenFacade(),
        )
        conversation_id = _create_local_conversation(client)
        response = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json=payload,
        )

    assert response.status_code == 422
    assert conversations.list_messages(conversation_id, "local") == []


def test_conversation_and_run_identifiers_do_not_cross_tenants(tmp_path: Path) -> None:
    with (
        SaasStore(tmp_path / "saas.db") as saas,
        _stores(tmp_path) as (
            conversations,
            runs,
        ),
    ):
        tenant_a = saas.create_tenant("A")
        tenant_b = saas.create_tenant("B")
        key_a = saas.create_api_key(tenant_a, scopes=["generate"])
        key_b = saas.create_api_key(tenant_b, scopes=["generate"])
        client = TestClient(
            create_app(
                _noop_factory(),
                _noop_factory(),
                saas_store=saas,
                conversation_store=conversations,
                run_store=runs,
                serve_console=False,
            )
        )
        created = client.post(
            "/v1/conversations",
            json={"title": "Tenant A"},
            headers={"X-API-Key": key_a},
        )
        conversation_id = created.json()["id"]
        run = runs.create_run(
            "test",
            {},
            metadata={"tenant_id": tenant_a, "conversation_id": conversation_id},
        )
        conversations.link_run(conversation_id, tenant_a, run.run_id)

        assert (
            client.get(
                f"/v1/conversations/{conversation_id}", headers={"X-API-Key": key_b}
            ).status_code
            == 404
        )
        assert (
            client.get(
                f"/v1/conversations/{conversation_id}/messages",
                headers={"X-API-Key": key_b},
            ).status_code
            == 404
        )
        assert client.get(f"/v1/runs/{run.run_id}", headers={"X-API-Key": key_b}).status_code == 404
        assert (
            client.get(f"/v1/runs/{run.run_id}/events", headers={"X-API-Key": key_b}).status_code
            == 404
        )
        assert (
            client.post(f"/v1/runs/{run.run_id}/cancel", headers={"X-API-Key": key_b}).status_code
            == 404
        )
        assert client.get("/v1/conversations", headers={"X-API-Key": key_b}).json()["items"] == []


def test_qgen_facade_background_success_persists_message_artifact_and_events(
    tmp_path: Path,
) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: _QGenFacade(),
        )
        conversation_id = _create_local_conversation(client)
        accepted = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={
                "mode": "new",
                "topics": ["analysis:分析"],
                "difficulty": 7,
            },
        )

        assert accepted.status_code == 202
        run_id = accepted.json()["run_id"]
        run = client.get(f"/v1/runs/{run_id}")
        messages = client.get(f"/v1/conversations/{conversation_id}/messages")
        artifacts = conversations.list_artifacts(conversation_id, "local")
        events = runs.list_events(run_id)

    assert run.json()["status"] == "succeeded"
    assert run.json()["output"]["generated"]["question"]["id"] == "generated-question"
    assert [item["role"] for item in messages.json()["items"]] == ["user", "assistant"]
    assert "## 题目" in messages.json()["items"][1]["content"]
    assert artifacts[0].kind == "generated_question"
    assert {event.event_type for event in events} >= {
        "run.created",
        "run.started",
        "run.succeeded",
        "assistant.final",
    }


def test_verified_qgen_result_is_materialized_with_solution_for_followup(
    tmp_path: Path,
) -> None:
    bank_path = tmp_path / "bank.db"
    with SQLiteStorageBackend(bank_path):
        pass
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: _QGenFacade(),
            storage_factory=lambda: SQLiteStorageBackend(bank_path),
        )
        conversation_id = _create_local_conversation(client)
        accepted = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"mode": "new", "content": "生成可继续加工的紧致性题目"},
        )

    assert accepted.status_code == 202
    with SQLiteStorageBackend(bank_path) as storage:
        saved = storage.load_question("generated-question")
    assert saved.solution_content == "由紧致性取有限子覆盖，再取最小半径即可。"
    assert any(
        event.event_type == "question.persisted"
        and event.payload == {"question_id": "generated-question"}
        for event in runs.list_events(accepted.json()["run_id"])
    )


@pytest.mark.parametrize("recover", [False, True])
def test_duplicate_qgen_uses_canonical_ids_for_get_artifact_and_restart(
    tmp_path: Path, recover: bool
) -> None:
    bank_path = tmp_path / "bank.db"
    original = _generated_output()
    assert original.generated is not None
    with SQLiteStorageBackend(bank_path) as storage:
        storage.save_question(original.generated.question.model_copy(update={"id": "canonical"}))
    with _stores(tmp_path) as (conversations, runs):
        facade = _QGenFacade(fail=recover)
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: facade,
            storage_factory=lambda: SQLiteStorageBackend(bank_path),
        )
        conversation_id = _create_local_conversation(client)
        if recover:
            record = runs.create_run(
                "qgen",
                {},
                metadata={"tenant_id": "local", "conversation_id": conversation_id, "mode": "new"},
            )
            run_id = record.run_id
            conversations.link_run(conversation_id, "local", run_id)
            runs.begin_run(run_id)
            runs.complete_run(run_id, original.model_dump(mode="json"))
        with client:
            if not recover:
                accepted = client.post(
                    f"/v1/conversations/{conversation_id}/messages",
                    json={"mode": "new", "content": "生成可继续加工的紧致性题目"},
                )
                assert accepted.status_code == 202
                run_id = accepted.json()["run_id"]
            output = client.get(f"/v1/runs/{run_id}").json()["output"]
        artifact = conversations.list_artifacts(conversation_id, "local")[0]
        for payload in (output, artifact.payload):
            assert payload["generated"]["question"]["id"] == "canonical"
            assert payload["generated"]["solution"]["question_id"] == "canonical"
        raw = runs.require_run(run_id).output_payload
        assert raw is not None
        assert raw["generated"]["question"]["id"] == "generated-question"
        assert raw["generated"]["solution"]["question_id"] == "generated-question"
        with client:
            assert client.get(f"/v1/runs/{run_id}").json()["output"] == output
        assert conversations.list_artifacts(conversation_id, "local")[0] == artifact
        assert len(facade.inputs) == (0 if recover else 1)
        assert [
            event.payload
            for event in runs.list_events(run_id)
            if event.event_type == "question.persisted"
        ] == [{"question_id": "canonical"}]
    with SQLiteStorageBackend(bank_path) as storage:
        assert storage.load_question("canonical").content == original.generated.question.content
        with pytest.raises(FileNotFoundError):
            storage.load_question("generated-question")


def test_historical_delivered_artifact_stays_immutable_after_canonical_id_recovery(
    tmp_path: Path,
) -> None:
    bank_path = tmp_path / "bank.db"
    original = _generated_output()
    assert original.generated is not None
    raw_output = original.model_dump(mode="json")
    with SQLiteStorageBackend(bank_path) as storage:
        storage.save_question(original.generated.question.model_copy(update={"id": "canonical"}))
    with _stores(tmp_path) as (conversations, runs):
        facade = _QGenFacade(fail=True)
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: facade,
            storage_factory=lambda: SQLiteStorageBackend(bank_path),
        )
        conversation_id = _create_local_conversation(client)
        record = runs.create_run(
            "qgen",
            {},
            metadata={"tenant_id": "local", "conversation_id": conversation_id, "mode": "new"},
        )
        run_id = record.run_id
        conversations.link_run(conversation_id, "local", run_id)
        runs.begin_run(run_id)
        runs.complete_run(run_id, raw_output)
        message, artifact = conversations.record_assistant_result(
            conversation_id,
            "local",
            run_id=run_id,
            content="Historical delivery with generated-question ID.",
            artifact_kind="generated_question",
            artifact_payload=raw_output,
        )
        runs.append_event(run_id, "question.persisted", {"question_id": "generated-question"})
        runs.append_event(run_id, "assistant.final", {"artifact_id": artifact.artifact_id})
        with client:
            output = client.get(f"/v1/runs/{run_id}").json()["output"]
        assert output["generated"]["question"]["id"] == "canonical"
        assert output["generated"]["solution"]["question_id"] == "canonical"
        assert conversations.list_artifacts(conversation_id, "local") == [artifact]
        assert conversations.list_messages(conversation_id, "local") == [message]
        assert artifact.payload == raw_output
        assert runs.require_run(run_id).output_payload == raw_output
        events = runs.list_events(run_id)
        assert [event.payload for event in events if event.event_type == "question.persisted"] == [
            {"question_id": "generated-question"},
            {"question_id": "canonical"},
        ]
        assert sum(event.event_type == "assistant.final" for event in events) == 1
        assert not any(event.event_type == "product.reconciliation_failed" for event in events)
        assert facade.inputs == []


def test_qgen_materialization_resolves_canonical_id_within_current_tenant(tmp_path: Path) -> None:
    bank_path = tmp_path / "bank.db"
    output = _generated_output()
    assert output.generated is not None
    with SQLiteStorageBackend(bank_path) as storage:
        for tenant in ("tenant-a", "tenant-b"):
            storage.save_question(
                output.generated.question.model_copy(update={"id": tenant, "tenant_id": tenant})
            )
    persisted_id = _persist_generated_question(
        output,
        storage=None,
        storage_factory=lambda: SQLiteStorageBackend(bank_path),
        storage_tenant_id="tenant-a",
    )
    assert persisted_id == "tenant-a"
    assert output.generated.question.id == "generated-question"


def test_failed_qgen_storage_does_not_publish_id_and_recovers_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank_path = tmp_path / "bank.db"
    with SQLiteStorageBackend(bank_path):
        pass
    with _stores(tmp_path) as (conversations, runs):
        facade = _QGenFacade()
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: facade,
            storage_factory=lambda: SQLiteStorageBackend(bank_path),
        )
        conversation_id = _create_local_conversation(client)

        def fail_save(_storage: SQLiteStorageBackend, _question: Question) -> str:
            raise OSError("question storage unavailable")

        with monkeypatch.context() as patch, client:
            patch.setattr(SQLiteStorageBackend, "save_question", fail_save)
            accepted = client.post(
                f"/v1/conversations/{conversation_id}/messages",
                json={"mode": "new", "content": "生成可继续加工的紧致性题目"},
            )
            assert accepted.status_code == 202
            run_id = accepted.json()["run_id"]
        assert runs.require_run(run_id).status is RunStatus.SUCCEEDED
        assert not any(
            event.event_type == "question.persisted" for event in runs.list_events(run_id)
        )
        with client:
            assert client.get(f"/v1/runs/{run_id}").json()["status"] == "succeeded"
        assert len(facade.inputs) == 1
        assert [
            event.payload
            for event in runs.list_events(run_id)
            if event.event_type == "question.persisted"
        ] == [{"question_id": "generated-question"}]
    with SQLiteStorageBackend(bank_path) as storage:
        assert storage.load_question("generated-question").solution_content


def test_qgen_facade_background_failure_is_terminal_and_user_visible(tmp_path: Path) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            qgen_factory=lambda _discipline: _QGenFacade(fail=True),
        )
        conversation_id = _create_local_conversation(client)
        accepted = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"mode": "new", "content": "generate a theorem problem"},
        )
        run_id = accepted.json()["run_id"]

        run = client.get(f"/v1/runs/{run_id}")
        messages = client.get(f"/v1/conversations/{conversation_id}/messages").json()["items"]

    assert run.json()["status"] == "failed"
    assert "facade generation exploded" in run.json()["error"]
    assert messages[-1]["role"] == "assistant"
    assert "任务执行失败" in messages[-1]["content"]
    assert any(event.event_type == "assistant.error" for event in runs.list_events(run_id))


def test_solve_message_runs_solver_and_preserves_structured_artifact(tmp_path: Path) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            solve_factory=lambda _discipline: _SolveFacade(),
        )
        conversation_id = _create_local_conversation(client)
        accepted = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"mode": "solve", "content": "求满足条件的唯一整数。"},
        )
        run_id = accepted.json()["run_id"]

        run = client.get(f"/v1/runs/{run_id}").json()
        artifacts = conversations.list_artifacts(conversation_id, "local")

    assert run["status"] == "succeeded"
    assert run["output"]["confidence"] == "high"
    assert artifacts[0].kind == "solution"
    assert artifacts[0].payload["solution"]["solution_latex"] == "逐步计算后得到 42。"


def test_open_mode_uses_unscoped_bank_while_conversation_owner_remains_local(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bank.db"
    with SQLiteStorageBackend(db_path) as storage:
        storage.save_batch(
            [],
            [
                Question(
                    id="offline-private",
                    content="离线开放模式应可读取整个本地题库。",
                    tenant_id="some-hosted-tenant",
                )
            ],
        )
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            solve_factory=lambda _discipline: _SolveFacade(),
            storage_factory=lambda: SQLiteStorageBackend(db_path),
        )
        conversation_id = _create_local_conversation(client)
        accepted = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={"mode": "solve", "source_question_id": "offline-private"},
        )

    assert accepted.status_code == 202
    run = runs.require_run(accepted.json()["run_id"])
    assert run.metadata["tenant_id"] == "local"
    assert run.status is RunStatus.SUCCEEDED


def test_manual_solve_records_real_client_counters_and_pauses_on_budget(
    tmp_path: Path,
) -> None:
    successful = _MeteredSolveFacade(calls=1, tokens=42)
    over_budget = _MeteredSolveFacade(calls=2, tokens=84)
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            solve_factory=lambda discipline: (
                successful if discipline is Discipline.MATH else over_budget
            ),
        )
        success_conversation = _create_local_conversation(client)
        success = client.post(
            f"/v1/conversations/{success_conversation}/messages",
            json={
                "mode": "solve",
                "content": "求值。",
                "budget": {"max_llm_calls": 2, "max_tokens": 100},
            },
        )

        # Select the second facade using a physics content request.
        paused_conversation = client.post(
            "/v1/conversations", json={"discipline": "physics"}
        ).json()["id"]
        paused = client.post(
            f"/v1/conversations/{paused_conversation}/messages",
            json={
                "mode": "solve",
                "content": "计算速度。",
                "discipline": "physics",
                "budget": {"max_llm_calls": 1},
            },
        )

    success_run = runs.require_run(success.json()["run_id"])
    paused_run = runs.require_run(paused.json()["run_id"])
    assert success_run.status is RunStatus.SUCCEEDED
    assert success_run.usage.llm_calls == 1
    assert success_run.usage.tokens == 42
    assert success_run.usage.wall_time_seconds > 0
    assert paused_run.status is RunStatus.PAUSED
    assert paused_run.metadata["verification_policy"] == "fast"
    assert paused_run.usage.llm_calls == 0
    assert any(
        event.event_type == "assistant.paused" for event in runs.list_events(paused_run.run_id)
    )


def test_solve_rejects_explicit_verification_policy_that_it_cannot_honor(
    tmp_path: Path,
) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            solve_factory=lambda _discipline: _SolveFacade(),
        )
        conversation_id = _create_local_conversation(client)
        response = client.post(
            f"/v1/conversations/{conversation_id}/messages",
            json={
                "mode": "solve",
                "content": "证明该命题。",
                "verification_policy": "formal",
            },
        )

    assert response.status_code == 422
    assert "evaluate" in response.text
    assert conversations.list_messages(conversation_id, "local") == []


def test_sse_replays_only_events_after_last_event_id(tmp_path: Path) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(conversations, runs)
        _conversation_id, run_id = _seed_owned_run(conversations, runs, status=RunStatus.RUNNING)
        marker = runs.append_event(run_id, "verification.numeric", {"passed": True})
        runs.complete_run(run_id, {"ok": True})

        full = client.get(f"/v1/runs/{run_id}/events")
        replay = client.get(
            f"/v1/runs/{run_id}/events",
            headers={"Last-Event-ID": str(marker.sequence)},
        )
        invalid = client.get(f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": "not-an-int"})

    assert full.status_code == 200
    assert "event: verification.numeric" in full.text
    assert f"id: {marker.sequence}\n" in full.text
    assert "event: verification.numeric" not in replay.text
    assert "event: run.succeeded" in replay.text
    assert invalid.status_code == 400


@pytest.mark.asyncio
async def test_sse_terminal_grace_captures_delayed_assistant_event(tmp_path: Path) -> None:
    with _stores(tmp_path) as (conversations, runs):
        _conversation_id, run_id = _seed_owned_run(conversations, runs, status=RunStatus.RUNNING)
        runs.complete_run(run_id, {"ok": True})

        async def append_final() -> None:
            await asyncio.sleep(0.05)
            runs.append_event(run_id, "assistant.final", {"message_id": "late"})

        append_task = asyncio.create_task(append_final())
        chunks = [chunk async for chunk in _stream_events(runs, run_id, after_sequence=0)]
        await append_task

    stream = "".join(chunks)
    assert "event: run.succeeded" in stream
    assert "event: assistant.final" in stream


def test_cancel_finalizes_pending_but_only_requests_running_work(tmp_path: Path) -> None:
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(conversations, runs)
        _pending_conversation, pending_id = _seed_owned_run(conversations, runs)
        _running_conversation, running_id = _seed_owned_run(
            conversations, runs, status=RunStatus.RUNNING
        )

        pending = client.post(f"/v1/runs/{pending_id}/cancel")
        running = client.post(f"/v1/runs/{running_id}/cancel")

    assert pending.json() == {
        "id": pending_id,
        "status": "cancelled",
        "cancel_requested": True,
    }
    assert runs.require_run(pending_id).completed_at is not None
    assert running.json() == {
        "id": running_id,
        "status": "running",
        "cancel_requested": True,
    }
    assert runs.require_run(running_id).completed_at is None


class _AuditableVerifier:
    def __init__(self) -> None:
        self.instructions: list[str] = []

    async def verify(
        self,
        _question: Question,
        _solution: Solution,
        _context: object,
        *,
        review_instruction: str = "",
    ) -> VerificationResult:
        self.instructions.append(review_instruction)
        return VerificationResult(
            passed=True,
            checks={"review_passed": True, "numerical_valid": True},
            evidence=[
                VerificationEvidence(
                    kind="numerical",
                    status=VerificationEvidenceStatus.PASSED,
                    required=True,
                    backend="test-auditable-verifier",
                    summary="computed independently",
                )
            ],
        )


class _MinimalRetriever:
    def build_context(self, _request: object, _discipline: Discipline) -> Any:
        from sciforge.core.models import ContextBundle

        return ContextBundle()


class _MinimalSolver:
    async def solve(self, question: Question, _context: object) -> Solution:
        return Solution(
            question_id=question.id,
            solution_latex="independent solution",
            confidence="high",
        )


class _EvaluationFacade:
    def __init__(self, verifier: _AuditableVerifier) -> None:
        self.retriever = _MinimalRetriever()
        self.solver = _MinimalSolver()
        self.verifier = verifier


def _seed_source_question(db_path: Path) -> SQLiteStorageBackend:
    storage = SQLiteStorageBackend(db_path)
    storage.save_batch(
        [],
        [
            Question(
                id="source-question",
                discipline=Discipline.MATH,
                content="设函数在闭区间连续，证明它一致连续。",
                solution_content="由紧致性取得有限子覆盖，取最小半径。",
                subject="analysis:分析",
                tags=["math:数学", "analysis:分析"],
                difficulty=7,
                tenant_id="",
            )
        ],
    )
    return storage


def test_evaluate_rejects_facade_instead_of_fabricating_verification(tmp_path: Path) -> None:
    bank = _seed_source_question(tmp_path / "bank.db")
    try:
        with _stores(tmp_path) as (conversations, runs):
            client = _local_app(
                conversations,
                runs,
                qgen_factory=lambda _discipline: _QGenFacade(),
                storage_factory=lambda: SQLiteStorageBackend(tmp_path / "bank.db"),
            )
            conversation_id = _create_local_conversation(client)
            response = client.post(
                f"/v1/conversations/{conversation_id}/messages",
                json={"mode": "evaluate", "source_question_id": "source-question"},
            )
    finally:
        bank.close()

    assert response.status_code == 503
    assert "可审计的 retriever/solver/verifier" in response.json()["detail"]


def test_evaluate_uses_real_qgen_verifier_and_preserves_evidence(tmp_path: Path) -> None:
    bank = _seed_source_question(tmp_path / "bank.db")
    verifier = _AuditableVerifier()

    def qgen_factory(discipline: Discipline) -> QGenAgent:
        agent = QGenAgent(MagicMock(), bank, discipline)
        agent.verifier = verifier  # type: ignore[assignment]
        return agent

    try:
        with _stores(tmp_path) as (conversations, runs):
            client = _local_app(
                conversations,
                runs,
                qgen_factory=qgen_factory,
                storage_factory=lambda: SQLiteStorageBackend(tmp_path / "bank.db"),
            )
            conversation_id = _create_local_conversation(client)
            instruction = "重点检查有限子覆盖到统一 delta 的量词顺序。"
            accepted = client.post(
                f"/v1/conversations/{conversation_id}/messages",
                json={
                    "mode": "evaluate",
                    "source_question_id": "source-question",
                    "content": instruction,
                },
            )
            run = client.get(f"/v1/runs/{accepted.json()['run_id']}").json()
    finally:
        bank.close()

    assert accepted.status_code == 202
    assert run["status"] == "succeeded"
    assert verifier.instructions == [instruction]
    evidence = run["output"]["verification"]["evidence"]
    assert evidence == [
        {
            "kind": "numerical",
            "status": "passed",
            "required": True,
            "backend": "test-auditable-verifier",
            "isolation": "none",
            "summary": "computed independently",
            "stdout": "",
            "stderr": "",
            "diagnostics": [],
            "duration_ms": 0,
            "artifact": "",
            "details": {},
        }
    ]


def test_evaluate_accepts_minimal_capability_facade_but_uses_its_verifier(
    tmp_path: Path,
) -> None:
    bank = _seed_source_question(tmp_path / "bank.db")
    verifier = _AuditableVerifier()
    try:
        with _stores(tmp_path) as (conversations, runs):
            client = _local_app(
                conversations,
                runs,
                qgen_factory=lambda _discipline: _EvaluationFacade(verifier),
                storage_factory=lambda: SQLiteStorageBackend(tmp_path / "bank.db"),
            )
            conversation_id = _create_local_conversation(client)
            accepted = client.post(
                f"/v1/conversations/{conversation_id}/messages",
                json={
                    "mode": "evaluate",
                    "source_question_id": "source-question",
                    "content": "check quantifiers",
                },
            )
    finally:
        bank.close()

    run = runs.require_run(accepted.json()["run_id"])
    assert run.status is RunStatus.SUCCEEDED
    assert verifier.instructions == ["check quantifiers"]
    assert run.output_payload is not None
    assert run.output_payload["verification"]["evidence"][0]["backend"] == (
        "test-auditable-verifier"
    )


def test_verification_policy_is_temporary_and_only_requires_applicable_backends(
    tmp_path: Path,
) -> None:
    bank = _seed_source_question(tmp_path / "bank.db")
    try:
        agent = QGenAgent(MagicMock(), bank, Discipline.MATH)
        verifier = agent.verifier
        original = (
            verifier.enable_numerical_verification,
            verifier.require_numerical_verification,
            verifier.enable_lean_verification,
            verifier.require_lean_verification,
        )

        with _temporary_verification_policy(
            agent,
            policy="strict",
            discipline=Discipline.MATH,
            question_type="proof",
        ):
            assert verifier.enable_lean_verification is True
            assert verifier.require_lean_verification is True
            assert verifier.enable_numerical_verification is False

        assert (
            verifier.enable_numerical_verification,
            verifier.require_numerical_verification,
            verifier.enable_lean_verification,
            verifier.require_lean_verification,
        ) == original

        with _temporary_verification_policy(
            agent,
            policy="balanced",
            discipline=Discipline.MATH,
            question_type="calculation",
        ):
            assert verifier.enable_numerical_verification is True
            assert verifier.require_numerical_verification is False
            assert verifier.enable_lean_verification is False

        with _temporary_verification_policy(
            agent,
            policy="formal",
            discipline=Discipline.MATH,
            question_type="proof",
        ):
            assert verifier.enable_lean_verification is True
            assert verifier.require_lean_verification is True
            assert verifier.enable_numerical_verification is False

        with pytest.raises(ValueError, match="只适用于数学证明"):
            with _temporary_verification_policy(
                agent,
                policy="formal",
                discipline=Discipline.MATH,
                question_type="calculation",
            ):
                pass
    finally:
        bank.close()


def test_balanced_policy_lazily_loads_and_restores_relevance_template(
    tmp_path: Path,
) -> None:
    bank = _seed_source_question(tmp_path / "bank.db")
    try:
        agent = QGenAgent(
            MagicMock(),
            bank,
            Discipline.MATH,
            enable_solution_relevance=False,
        )
        assert agent.verifier._relevance_template == ""

        with _temporary_verification_policy(
            agent,
            policy="balanced",
            discipline=Discipline.MATH,
            question_type="proof",
        ):
            assert agent.verifier.enable_solution_relevance is True
            assert agent.verifier._relevance_template

        assert agent.verifier.enable_solution_relevance is False
        assert agent.verifier._relevance_template == ""
    finally:
        bank.close()


def _seed_quality_gated_knowledge(db_path: Path) -> None:
    with SQLiteStorageBackend(db_path) as storage:
        storage.save_batch(
            [
                KnowledgePoint(
                    id="kp-good",
                    discipline=Discipline.MATH,
                    kp_type="theorem",
                    number="定理 1",
                    content="闭区间上的连续函数必然一致连续，这是紧致性的重要推论。",
                    proof_content="证明：由开覆盖的有限子覆盖可取统一的正半径。",
                    difficulty=7,
                    subject="analysis:分析",
                    taxonomy_path="math.analysis.uniform_continuity",
                    tags=["math:数学", "analysis:分析"],
                    source_file="quality.md",
                    page=12,
                    start_line=1,
                    end_line=8,
                    order_index=1,
                ),
                KnowledgePoint(
                    id="kp-risky",
                    discipline=Discipline.MATH,
                    kp_type="theorem",
                    content="见上文。",
                    source_file="quality.md",
                    order_index=2,
                ),
            ],
            [],
        )
        batch = evaluate_item_rows(storage.evaluation_rows("quality.md", "math"))
        storage.save_evaluation_batch(batch)


def test_knowledge_search_applies_publication_quality_gate_before_browsing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "knowledge.db"
    _seed_quality_gated_knowledge(db_path)
    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            storage_factory=lambda: SQLiteStorageBackend(db_path),
        )

        response = client.get("/v1/knowledge/search?discipline=math&limit=20")
        filtered = client.get("/v1/knowledge/search?q=紧致性&discipline=math")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["kp-good"]
    assert (
        response.json()["items"][0]
        | {
            "source_file": "quality.md",
            "source_page": 12,
            "start_line": 1,
            "end_line": 8,
        }
        == response.json()["items"][0]
    )
    assert [item["id"] for item in filtered.json()["items"]] == ["kp-good"]


def test_knowledge_text_query_is_applied_before_storage_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "large-knowledge.db"
    noise = [
        KnowledgePoint(
            id=f"noise-{index:03d}",
            content=f"这是第 {index} 条关于连续函数基础性质的完整知识说明。",
            proof_content="证明包含完整条件、推导步骤与结论。",
            taxonomy_path="math.analysis.continuity",
            tags=["math:数学", "analysis:分析"],
            source_file="large.md",
            start_line=index * 3 + 1,
            end_line=index * 3 + 3,
            order_index=index,
        )
        for index in range(500)
    ]
    target = KnowledgePoint(
        id="target-after-limit",
        content="Banach 不动点定理在完备度量空间中保证压缩映射存在唯一不动点。",
        proof_content="由迭代序列的 Cauchy 性、完备性和压缩常数可得存在唯一性。",
        taxonomy_path="math.analysis.fixed_point",
        tags=["math:数学", "analysis:分析", "fixed_point:不动点"],
        source_file="large.md",
        start_line=2000,
        end_line=2010,
        order_index=501,
    )
    with SQLiteStorageBackend(db_path) as storage:
        storage.save_batch([*noise, target], [])
        storage.save_evaluation_batch(
            evaluate_item_rows(storage.evaluation_rows("large.md", "math"))
        )

    with _stores(tmp_path) as (conversations, runs):
        client = _local_app(
            conversations,
            runs,
            storage_factory=lambda: SQLiteStorageBackend(db_path),
        )
        response = client.get("/v1/knowledge/search?q=Banach&discipline=math")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == ["target-after-limit"]
