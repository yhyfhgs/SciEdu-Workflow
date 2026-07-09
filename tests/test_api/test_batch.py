"""FastAPI 网关异步批量出题测试（/v1/generate/batch + /v1/tasks/{id}）"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from sciforge.agents.qgen.agent import QGenOutput
from sciforge.api.app import create_app
from sciforge.core.models import (
    DifficultyScore,
    Discipline,
    GeneratedQuestion,
    Question,
    Solution,
    VerificationResult,
)
from sciforge.storage.saas import SaasStore


def _qgen_factory() -> Any:
    q = Question(
        discipline=Discipline.MATH, question_type="proof", content="批量生成的题面足够长。"
    )
    gq = GeneratedQuestion(
        question=q,
        solution=Solution(solution_latex="解"),
        difficulty_score=DifficultyScore(),
        verification=VerificationResult(passed=True),
    )
    agent = MagicMock()
    agent.run = AsyncMock(return_value=QGenOutput(generated=gq, rounds_used=1))
    return lambda _d: agent


def _noop() -> Any:
    return lambda _d: MagicMock()


def test_batch_generates_tracks_and_bills(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme", monthly_quota=3)
        key = store.create_api_key(tid, scopes=["generate"])
        client = TestClient(create_app(_qgen_factory(), _noop(), saas_store=store))
        r = client.post(
            "/v1/generate/batch",
            json={"topics": ["math:数学"], "count": 3},
            headers={"X-API-Key": key},
        )
        assert r.status_code == 200
        task_id = r.json()["task_id"]
        assert r.json()["total"] == 3

        # TestClient 在响应后同步执行 BackgroundTasks，此时任务已跑完
        r2 = client.get(f"/v1/tasks/{task_id}", headers={"X-API-Key": key})
        assert r2.status_code == 200
        body = r2.json()
        assert body["status"] == "completed"
        assert body["done"] == 3
        assert len(body["result"]) == 3
        assert "tenant_id" not in body
        # 计费：3 题
        assert store.get_usage(tid).used == 3


def test_batch_open_mode_returns_503() -> None:
    client = TestClient(create_app(_qgen_factory(), _noop()))  # 无 saas_store
    r = client.post("/v1/generate/batch", json={"topics": ["x"], "count": 2})
    assert r.status_code == 503


def test_task_not_found_returns_404(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme")
        key = store.create_api_key(tid)
        client = TestClient(create_app(_noop(), _noop(), saas_store=store))
        r = client.get("/v1/tasks/nonexistent", headers={"X-API-Key": key})
        assert r.status_code == 404


def test_task_lookup_and_update_are_tenant_scoped(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        owner = store.create_tenant("Owner")
        outsider = store.create_tenant("Outsider")
        owner_key = store.create_api_key(owner, scopes=["generate"])
        outsider_key = store.create_api_key(outsider, scopes=["generate"])
        task_id = store.create_task(owner, 2)
        client = TestClient(create_app(_noop(), _noop(), saas_store=store))

        owner_response = client.get(f"/v1/tasks/{task_id}", headers={"X-API-Key": owner_key})
        outsider_response = client.get(f"/v1/tasks/{task_id}", headers={"X-API-Key": outsider_key})

        assert owner_response.status_code == 200
        assert "tenant_id" not in owner_response.json()
        assert outsider_response.status_code == 404
        assert store.update_task(task_id, outsider, status="completed") is False
        owner_task = store.get_task(task_id, owner)
        assert owner_task is not None and owner_task.status == "pending"


def test_batch_rejects_requested_units_before_task_or_agent_creation(tmp_path: Path) -> None:
    db_path = tmp_path / "saas.db"
    with SaasStore(db_path) as store:
        tid = store.create_tenant("Acme", monthly_quota=4)
        key = store.create_api_key(tid, scopes=["generate"])
        store.record_usage(tid, "/v1/generate", billable_units=2)
        qgen_factory = MagicMock()
        client = TestClient(create_app(qgen_factory, _noop(), saas_store=store))

        r = client.post(
            "/v1/generate/batch",
            json={"topics": ["math:数学"], "count": 3},
            headers={"X-API-Key": key},
        )

        assert r.status_code == 429
        qgen_factory.assert_not_called()
        with sqlite3.connect(db_path) as conn:
            task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        assert task_count == 0
