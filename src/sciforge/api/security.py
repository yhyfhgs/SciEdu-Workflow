"""无状态签名 token（HMAC-SHA256，标准库实现，无需 PyJWT）

token 格式 ``<base64url(payload)>.<base64url(hmac)>``，payload 含 ``exp`` 过期时间戳。
适合 C 端（Web/App/小程序）会话；B2B 仍用 X-API-Key。OAuth（微信/Apple）需密钥，后续接入。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _sign(raw: str, secret: str) -> str:
    return _b64encode(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).digest())


def sign_token(payload: dict[str, Any], secret: str, *, ttl_seconds: int = 86400) -> str:
    """签发带过期时间的 token。"""
    body = {**payload, "exp": int(time.time()) + ttl_seconds}
    raw = _b64encode(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode())
    return f"{raw}.{_sign(raw, secret)}"


def verify_token(token: str, secret: str) -> dict[str, Any] | None:
    """校验 token；签名不符 / 过期 / 格式错误返回 None。"""
    try:
        raw, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(raw, secret)):
        return None
    try:
        body = json.loads(_b64decode(raw))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    if int(body.get("exp", 0)) < int(time.time()):
        return None
    return body
