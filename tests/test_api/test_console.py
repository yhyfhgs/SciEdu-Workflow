"""内置 SaaS 控制台静态页 + CORS 中间件测试"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from sciforge.api.app import create_app


def _noop() -> Any:
    return lambda _d: MagicMock()


def test_root_redirects_to_console() -> None:
    client = TestClient(create_app(_noop(), _noop()), follow_redirects=False)
    r = client.get("/")
    assert r.status_code in (307, 308)
    assert r.headers["location"] == "/app/"


def test_console_index_served() -> None:
    client = TestClient(create_app(_noop(), _noop()))
    r = client.get("/app/")
    assert r.status_code == 200
    assert "SciForge" in r.text
    assert "/v1/generate" in r.text  # 前端确实调用统一 API


def test_console_can_be_disabled() -> None:
    client = TestClient(create_app(_noop(), _noop(), serve_console=False), follow_redirects=False)
    assert client.get("/").status_code == 404


def test_cors_header_present() -> None:
    client = TestClient(create_app(_noop(), _noop()))
    r = client.get("/v1/health", headers={"Origin": "https://app.example.com"})
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "*"


def test_api_routes_not_shadowed_by_static_mount() -> None:
    """静态挂载在 /app，不应遮蔽 /v1/* 与 /docs。"""
    client = TestClient(create_app(_noop(), _noop()))
    assert client.get("/v1/health").json()["status"] == "ok"
    assert client.get("/docs").status_code == 200
