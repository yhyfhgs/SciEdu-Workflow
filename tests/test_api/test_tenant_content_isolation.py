"""Cross-tenant content isolation at authenticated API read boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from sciforge.agents import QGenAgent, SolveAgent
from sciforge.agents.qgen.agent import QGenOutput
from sciforge.api.app import create_app
from sciforge.api.tenant_storage import TenantScopedStorageView
from sciforge.core.models import Discipline, Question
from sciforge.storage.saas import SaasStore
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


def _noop_factory() -> Any:
    return lambda _discipline: MagicMock()


def _question(qid: str, content: str, tenant_id: str, order_index: int) -> Question:
    return Question(
        id=qid,
        discipline=Discipline.MATH,
        content=content,
        difficulty=4,
        tenant_id=tenant_id,
        order_index=order_index,
    )


def _seed_bank(db_path: Path, tenant_a: str, tenant_b: str) -> None:
    with SQLiteStorageBackend(db_path) as storage:
        storage.save_batch(
            [],
            [
                _question("public", "公共题目内容足够长。", "", 1),
                _question("tenant-a", "租户 A 私有题目内容足够长。", tenant_a, 2),
                _question("tenant-b", "租户 B 私有题目内容足够长。", tenant_b, 3),
            ],
        )


def test_sql_scope_filters_before_limit(tmp_path: Path) -> None:
    """Foreign rows cannot consume the unscoped 500-row search window."""
    db_path = tmp_path / "bank.db"
    foreign = [_question(f"foreign-{i}", f"foreign content {i}", "tenant-b", i) for i in range(500)]
    with SQLiteStorageBackend(db_path) as storage:
        storage.save_batch(
            [],
            [
                *foreign,
                _question("public-after-limit", "public after limit", "", 501),
                _question("own-after-limit", "own after limit", "tenant-a", 502),
            ],
        )
        with TenantScopedStorageView(storage, "tenant-a") as scoped:
            assert scoped.search_questions({}) == ["public-after-limit", "own-after-limit"]


def test_papers_include_public_and_owner_but_not_foreign(tmp_path: Path) -> None:
    db_path = tmp_path / "bank.db"
    with SaasStore(tmp_path / "saas.db") as saas:
        tenant_a = saas.create_tenant("A")
        tenant_b = saas.create_tenant("B")
        key_a = saas.create_api_key(tenant_a, scopes=["generate"])
        _seed_bank(db_path, tenant_a, tenant_b)
        client = TestClient(
            create_app(
                _noop_factory(),
                _noop_factory(),
                saas_store=saas,
                storage_factory=lambda: SQLiteStorageBackend(db_path),
            )
        )

        response = client.post(
            "/v1/papers",
            json={"count": 10},
            headers={"X-API-Key": key_a},
        )

        assert response.status_code == 200
        assert {item["id"] for item in response.json()["questions"]} == {
            "public",
            "tenant-a",
        }


def test_recommend_never_returns_another_tenants_private_question(tmp_path: Path) -> None:
    db_path = tmp_path / "bank.db"
    with SaasStore(tmp_path / "saas.db") as saas:
        tenant_a = saas.create_tenant("A")
        tenant_b = saas.create_tenant("B")
        saas.create_user("alice", "pw", tenant_id=tenant_a)
        _seed_bank(db_path, tenant_a, tenant_b)
        client = TestClient(
            create_app(
                _noop_factory(),
                _noop_factory(),
                saas_store=saas,
                storage_factory=lambda: SQLiteStorageBackend(db_path),
                jwt_secret="test-secret",
            )
        )
        login = client.post("/v1/auth/login", json={"username": "alice", "password": "pw"})
        token = login.json()["token"]

        response = client.get(
            "/v1/recommend?limit=10",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert {item["question_id"] for item in response.json()["items"]} == {
            "public",
            "tenant-a",
        }


def test_qgen_retriever_is_rebound_to_tenant_scope(tmp_path: Path) -> None:
    db_path = tmp_path / "bank.db"
    with SaasStore(tmp_path / "saas.db") as saas:
        tenant_a = saas.create_tenant("A")
        tenant_b = saas.create_tenant("B")
        key_a = saas.create_api_key(tenant_a, scopes=["generate"])
        _seed_bank(db_path, tenant_a, tenant_b)
        long_lived_storage = SQLiteStorageBackend(db_path)
        observed_ids: list[str] = []

        def qgen_factory(discipline: Discipline) -> QGenAgent:
            agent = QGenAgent(MagicMock(), long_lived_storage, discipline)

            async def capture_context(_input: object) -> QGenOutput:
                scoped_storage = agent.retriever.storage
                ids = scoped_storage.search_questions({"discipline": discipline.value})
                observed_ids.extend(ids)
                # Exercise the bulk-load guard too: a known foreign UUID must
                # remain invisible even when explicitly supplied.
                loaded = scoped_storage.load_questions_by_ids([*ids, "tenant-b"])
                assert {q.id for q in loaded} == {"public", "tenant-a"}
                return QGenOutput(generated=None, rounds_used=1, reason="capture only")

            agent.run = AsyncMock(side_effect=capture_context)  # type: ignore[method-assign]
            return agent

        try:
            client = TestClient(
                create_app(
                    qgen_factory,
                    _noop_factory(),
                    saas_store=saas,
                    storage_factory=lambda: SQLiteStorageBackend(db_path),
                )
            )
            response = client.post(
                "/v1/generate",
                json={"topics": ["math:数学"]},
                headers={"X-API-Key": key_a},
            )
        finally:
            long_lived_storage.close()

        assert response.status_code == 200
        assert set(observed_ids) == {"public", "tenant-a"}


def test_solve_by_foreign_question_id_returns_same_404_as_missing(tmp_path: Path) -> None:
    db_path = tmp_path / "bank.db"
    with SaasStore(tmp_path / "saas.db") as saas:
        tenant_a = saas.create_tenant("A")
        tenant_b = saas.create_tenant("B")
        key_a = saas.create_api_key(tenant_a, scopes=["solve"])
        _seed_bank(db_path, tenant_a, tenant_b)
        long_lived_storage = SQLiteStorageBackend(db_path)

        def solve_factory(discipline: Discipline) -> SolveAgent:
            return SolveAgent(MagicMock(), long_lived_storage, discipline)

        try:
            client = TestClient(
                create_app(
                    _noop_factory(),
                    solve_factory,
                    saas_store=saas,
                    storage_factory=lambda: SQLiteStorageBackend(db_path),
                )
            )
            foreign = client.post(
                "/v1/solve",
                json={"question_id": "tenant-b"},
                headers={"X-API-Key": key_a},
            )
            missing = client.post(
                "/v1/solve",
                json={"question_id": "does-not-exist"},
                headers={"X-API-Key": key_a},
            )
        finally:
            long_lived_storage.close()

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json()
