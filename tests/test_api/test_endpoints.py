"""SciForge FastAPI 网关端点测试（P2）— TestClient + mock agent 工厂"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from sciforge.agents.qgen.agent import QGenOutput
from sciforge.agents.solve_agent import SolveOutput
from sciforge.api.app import create_app
from sciforge.core.models import (
    DifficultyScore,
    Discipline,
    GeneratedQuestion,
    Question,
    Solution,
    VerificationResult,
)


def _factory_returning(output: Any) -> Any:
    agent = MagicMock()
    agent.run = AsyncMock(return_value=output)
    return lambda _d: agent


def _noop_factory() -> Any:
    return lambda _d: MagicMock()


def test_health() -> None:
    client = TestClient(create_app(_noop_factory(), _noop_factory()))
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "sciforge"}


def test_generate_success() -> None:
    q = Question(discipline=Discipline.MATH, question_type="proof", content="证明题题面足够长。")
    gq = GeneratedQuestion(
        question=q,
        solution=Solution(solution_latex="证明过程"),
        difficulty_score=DifficultyScore(),
        verification=VerificationResult(passed=True),
    )
    out = QGenOutput(generated=gq, rounds_used=1, candidates_tried=1)
    client = TestClient(create_app(_factory_returning(out), _noop_factory()))
    r = client.post("/v1/generate", json={"topics": ["math:数学"], "difficulty": 6})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["question"]["content"] == "证明题题面足够长。"
    assert body["question"]["verified"] is True


def test_generate_failure_returns_reason() -> None:
    out = QGenOutput(generated=None, rounds_used=3, reason="3 轮后未通过验证")
    client = TestClient(create_app(_factory_returning(out), _noop_factory()))
    r = client.post("/v1/generate", json={"topics": ["x"], "difficulty": 5})
    assert r.status_code == 200
    assert r.json()["success"] is False
    assert "未通过" in r.json()["reason"]


def test_generate_empty_topics_422() -> None:
    client = TestClient(create_app(_noop_factory(), _noop_factory()))
    r = client.post("/v1/generate", json={"topics": [], "difficulty": 5})
    assert r.status_code == 422


def test_generate_unknown_discipline_422() -> None:
    client = TestClient(create_app(_noop_factory(), _noop_factory()))
    r = client.post("/v1/generate", json={"topics": ["x"], "discipline": "astrology"})
    assert r.status_code == 422


def test_generate_uses_domain_default_type_and_structured_form() -> None:
    agent = MagicMock()
    agent.run = AsyncMock(return_value=QGenOutput(generated=None, reason="capture"))
    client = TestClient(create_app(lambda _discipline: agent, _noop_factory()))

    response = client.post(
        "/v1/generate",
        json={
            "discipline": "chemistry",
            "topics": ["kinetics:动力学"],
            "question_form": "choice",
        },
    )

    assert response.status_code == 200
    request = agent.run.await_args.args[0].request
    assert request.discipline is Discipline.CHEMISTRY
    assert request.question_type == "calculation"
    assert request.question_form == "choice"


def test_generate_rejects_noncanonical_question_type() -> None:
    client = TestClient(create_app(_noop_factory(), _noop_factory()))
    response = client.post(
        "/v1/generate",
        json={"topics": ["analysis:分析"], "question_type": "computational"},
    )
    assert response.status_code == 422


def test_solve_success() -> None:
    out = SolveOutput(solution=Solution(solution_latex="解答内容"), confidence="high")
    client = TestClient(create_app(_noop_factory(), _factory_returning(out)))
    r = client.post("/v1/solve", json={"content": "求这道题的解答"})
    assert r.status_code == 200
    assert r.json()["success"] is True
    assert r.json()["confidence"] == "high"


def test_solve_requires_input_422() -> None:
    client = TestClient(create_app(_noop_factory(), _noop_factory()))
    r = client.post("/v1/solve", json={})
    assert r.status_code == 422
