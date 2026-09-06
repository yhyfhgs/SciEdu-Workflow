"""FastAPI 网关 P4 推荐端点测试（/v1/behavior + /v1/recommend）"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from sciforge.api.app import create_app
from sciforge.core.models import Discipline, Question
from sciforge.storage.saas import SaasStore


def _noop() -> Any:
    return lambda _d: MagicMock()


def _mock_storage(questions: list[Question]) -> Any:
    by_id = {q.id: q for q in questions}
    s = MagicMock()
    s.search_questions = MagicMock(return_value=[q.id for q in questions])
    s.search_questions_for_tenant = MagicMock(
        side_effect=lambda _filters, *, tenant_id, include_public=True: [
            q.id
            for q in questions
            if q.tenant_id == tenant_id or (include_public and not q.tenant_id)
        ]
    )
    s.load_questions_by_ids = MagicMock(
        side_effect=lambda ids: [by_id[i] for i in ids if i in by_id]
    )
    s.close = MagicMock()
    return s


def _register(client: TestClient, username: str = "alice") -> str:
    r = client.post("/v1/auth/register", json={"username": username, "password": "pw"})
    return str(r.json()["token"])


def test_behavior_requires_user_token(tmp_path: Path) -> None:
    """API Key（B2B，无终端用户）不能记录练习行为 → 400。"""
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme")
        key = store.create_api_key(tid)
        client = TestClient(create_app(_noop(), _noop(), saas_store=store))
        r = client.post(
            "/v1/behavior",
            json={"question_id": "q1", "outcome": "correct"},
            headers={"X-API-Key": key},
        )
        assert r.status_code == 400


def test_behavior_recorded_for_logged_in_user(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        client = TestClient(create_app(_noop(), _noop(), saas_store=store, jwt_secret="s"))
        token = _register(client)
        r = client.post(
            "/v1/behavior",
            json={"question_id": "q1", "outcome": "correct", "difficulty": 5, "tags": ["a:甲"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200 and r.json()["status"] == "recorded"


def test_recommend_returns_ranked_items(tmp_path: Path) -> None:
    def _mkq(qid: str, diff: int) -> Question:
        return Question(
            id=qid, discipline=Discipline.MATH, content=f"{qid}题内容足够长。", difficulty=diff
        )

    qs = [_mkq("easy", 2), _mkq("target", 6), _mkq("hard", 10)]
    with SaasStore(tmp_path / "saas.db") as store:
        bank = _mock_storage(qs)
        client = TestClient(
            create_app(
                _noop(), _noop(), saas_store=store, storage_factory=lambda: bank, jwt_secret="s"
            )
        )
        token = _register(client)
        hdr = {"Authorization": f"Bearer {token}"}
        # 答对难度 5 → 掌握难度 5 → 目标 6 → "target" 应排首
        client.post(
            "/v1/behavior",
            json={"question_id": "seed", "outcome": "correct", "difficulty": 5},
            headers=hdr,
        )
        r = client.get("/v1/recommend?discipline=math&limit=3", headers=hdr)
        assert r.status_code == 200
        body = r.json()
        assert body["mastered_difficulty"] == 5.0
        assert body["items"][0]["question_id"] == "target"
        assert body["items"][0]["content"]  # 带回题面


def test_recommend_requires_user_token(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme")
        key = store.create_api_key(tid)
        bank = _mock_storage([])
        client = TestClient(
            create_app(_noop(), _noop(), saas_store=store, storage_factory=lambda: bank)
        )
        r = client.get("/v1/recommend", headers={"X-API-Key": key})
        assert r.status_code == 400


def test_recommend_open_mode_404(tmp_path: Path) -> None:
    """无 saas_store（开放模式）→ 推荐端点 404。"""
    client = TestClient(create_app(_noop(), _noop()))
    assert client.get("/v1/recommend").status_code == 404
