"""FastAPI 网关 API Key 鉴权 + 多租户计费测试"""

from __future__ import annotations

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
        discipline=Discipline.MATH, question_type="proof", content="生成的证明题题面足够长。"
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


def test_generate_requires_api_key(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        client = TestClient(create_app(_qgen_factory(), _noop(), saas_store=store))
        r = client.post("/v1/generate", json={"topics": ["x"]})
        assert r.status_code == 401


def test_generate_with_valid_key_records_usage(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme", monthly_quota=100)
        key = store.create_api_key(tid)
        client = TestClient(create_app(_qgen_factory(), _noop(), saas_store=store))
        r = client.post("/v1/generate", json={"topics": ["x"]}, headers={"X-API-Key": key})
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert store.get_usage(tid).used == 1  # 计费已记录


def test_quota_exhausted_returns_429(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme", monthly_quota=1)
        key = store.create_api_key(tid)
        store.record_usage(tid, "/v1/generate", billable_units=1)  # 配额用尽
        client = TestClient(create_app(_qgen_factory(), _noop(), saas_store=store))
        r = client.post("/v1/generate", json={"topics": ["x"]}, headers={"X-API-Key": key})
        assert r.status_code == 429


def test_usage_endpoint(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme", monthly_quota=50)
        key = store.create_api_key(tid)
        client = TestClient(create_app(_noop(), _noop(), saas_store=store))
        r = client.get("/v1/usage", headers={"X-API-Key": key})
        assert r.status_code == 200
        body = r.json()
        assert body["quota"] == 50 and body["remaining"] == 50


def test_open_mode_needs_no_key(tmp_path: Path) -> None:
    """无 saas_store → 开放模式，无需 key；usage 端点 404"""
    client = TestClient(create_app(_qgen_factory(), _noop()))
    r = client.post("/v1/generate", json={"topics": ["x"]})
    assert r.status_code == 200 and r.json()["success"] is True
    assert client.get("/v1/usage").status_code == 404


def test_register_and_login_flow(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        client = TestClient(create_app(_qgen_factory(), _noop(), saas_store=store, jwt_secret="s"))
        r = client.post("/v1/auth/register", json={"username": "alice", "password": "pw123"})
        assert r.status_code == 200
        assert r.json()["token"] and r.json()["tenant_id"]
        # 重复注册 → 409
        assert (
            client.post("/v1/auth/register", json={"username": "alice", "password": "x"})
        ).status_code == 409
        # 登录
        r3 = client.post("/v1/auth/login", json={"username": "alice", "password": "pw123"})
        assert r3.status_code == 200 and r3.json()["token"]
        # 错误密码 → 401
        r4 = client.post("/v1/auth/login", json={"username": "alice", "password": "wrong"})
        assert r4.status_code == 401


def test_bearer_token_authorizes_generate(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        client = TestClient(create_app(_qgen_factory(), _noop(), saas_store=store, jwt_secret="s"))
        token = client.post(
            "/v1/auth/register", json={"username": "bob", "password": "pw"}
        ).json()["token"]
        r = client.post(
            "/v1/generate", json={"topics": ["x"]}, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 200 and r.json()["success"] is True


def test_bad_bearer_token_401(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        client = TestClient(create_app(_qgen_factory(), _noop(), saas_store=store))
        r = client.post(
            "/v1/generate",
            json={"topics": ["x"]},
            headers={"Authorization": "Bearer invalid.token"},
        )
        assert r.status_code == 401
