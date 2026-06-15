"""签名 token（HMAC-SHA256）测试"""

from __future__ import annotations

from sciforge.api.security import sign_token, verify_token


def test_sign_verify_roundtrip() -> None:
    token = sign_token({"user_id": "u1", "tenant_id": "t1"}, "secret")
    payload = verify_token(token, "secret")
    assert payload is not None
    assert payload["user_id"] == "u1"
    assert payload["tenant_id"] == "t1"
    assert "exp" in payload


def test_verify_wrong_secret() -> None:
    token = sign_token({"user_id": "u1"}, "secret")
    assert verify_token(token, "other-secret") is None


def test_verify_tampered_payload() -> None:
    token = sign_token({"user_id": "u1"}, "secret")
    raw, sig = token.split(".", 1)
    assert verify_token(f"{raw}x.{sig}", "secret") is None


def test_verify_expired() -> None:
    token = sign_token({"user_id": "u1"}, "secret", ttl_seconds=-1)
    assert verify_token(token, "secret") is None


def test_verify_garbage() -> None:
    assert verify_token("not-a-token", "secret") is None
    assert verify_token("", "secret") is None
