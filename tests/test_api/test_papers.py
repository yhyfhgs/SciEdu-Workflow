"""FastAPI 网关组卷端点测试（/v1/papers）"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from sciforge.api.app import create_app
from sciforge.core.models import Discipline, Question


def _noop() -> Any:
    return lambda _d: MagicMock()


def _mock_storage(questions: list[Question]) -> Any:
    by_id = {q.id: q for q in questions}
    s = MagicMock()
    s.search_questions = MagicMock(return_value=[q.id for q in questions])
    s.load_questions_by_ids = MagicMock(
        side_effect=lambda ids: [by_id[i] for i in ids if i in by_id]
    )
    s.close = MagicMock()
    return s


def test_papers_assembles_from_bank() -> None:
    qs = [
        Question(
            discipline=Discipline.MATH,
            question_type="proof",
            content=f"题目{i}内容足够长用于测试组卷。",
            difficulty=5,
            solution_content="解",
        )
        for i in range(3)
    ]
    store = _mock_storage(qs)
    client = TestClient(create_app(_noop(), _noop(), storage_factory=lambda: store))
    r = client.post("/v1/papers", json={"discipline": "math", "count": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert len(body["questions"]) == 3
    assert body["questions"][0]["solution"] == "解"
    store.close.assert_called_once()  # storage 资源已释放


def test_papers_count_limits_selection() -> None:
    qs = [
        Question(discipline=Discipline.MATH, content=f"题{i}内容足够长测试组卷。", difficulty=4)
        for i in range(10)
    ]
    store = _mock_storage(qs)
    client = TestClient(create_app(_noop(), _noop(), storage_factory=lambda: store))
    r = client.post("/v1/papers", json={"count": 5})
    assert r.status_code == 200
    assert r.json()["count"] == 5  # 仅取前 5 题


def test_papers_no_storage_returns_503() -> None:
    client = TestClient(create_app(_noop(), _noop()))
    r = client.post("/v1/papers", json={"count": 5})
    assert r.status_code == 503


def test_papers_bad_difficulty_range_422() -> None:
    store = _mock_storage([])
    client = TestClient(create_app(_noop(), _noop(), storage_factory=lambda: store))
    r = client.post("/v1/papers", json={"difficulty_min": 8, "difficulty_max": 3})
    assert r.status_code == 422
