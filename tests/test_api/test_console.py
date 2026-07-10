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
    assert "app.js" in r.text and "styles.css" in r.text
    assert 'data-mode="new"' in r.text
    assert 'data-mode="evaluate"' in r.text
    assert 'id="verificationStack"' in r.text
    assert 'id="resumeRunButton"' in r.text
    assert 'id="resumeDialog"' in r.text


def test_console_javascript_uses_durable_conversation_contract() -> None:
    client = TestClient(create_app(_noop(), _noop()))

    script = client.get("/app/app.js")

    assert script.status_code == 200
    assert "/v1/conversations" in script.text
    assert "/events" in script.text
    assert "Last-Event-ID" in script.text
    assert "Authorization" in script.text
    assert "X-API-Key" in script.text
    assert "ReadableStream" not in script.text  # fetch body reader, no external polyfill
    assert ".getReader()" in script.text
    assert "/resume" in script.text
    assert "rail.inert" in script.text
    assert "innerHTML" not in script.text


def test_console_styles_include_responsive_three_panel_layout() -> None:
    client = TestClient(create_app(_noop(), _noop()))

    styles = client.get("/app/styles.css")

    assert styles.status_code == 200
    assert "grid-template-columns" in styles.text
    assert "@media (max-width: 720px)" in styles.text
    assert ".verification-card.unavailable" in styles.text
    assert ".rail-scrim[hidden]" in styles.text


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
