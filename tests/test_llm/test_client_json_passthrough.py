"""LLMClient.generate_json → generate() 额外参数透传回归测试

回归 bug：``generate_json`` 经 ``_default_json_mode`` 注入 ``response_format``，
再调 ``generate(**kwargs)``；旧签名不接受 ``response_format`` → ``TypeError``。
mock 直接打 backend 的测试会绕过 LLMClient 这层，故此处在 **LLMClient 层**断言：
``response_format`` 透传到 backend.generate 的 kwargs，且全程无 TypeError。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from sciforge.llm.client import LLMClient
from sciforge.llm.config import LLMConfig, LLMProvider


class _RecordingBackend:
    """记录收到的 kwargs 的假 backend，签名与真 backend 一致（**kwargs）。"""

    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] = {}
        self.last_usage: dict[str, int] = {}

    async def generate(self, prompt: str, system: str, **kwargs: Any) -> str:
        self.last_kwargs = kwargs
        return json.dumps({"ok": True})

    def generate_sync(self, prompt: str, system: str, **kwargs: Any) -> str:
        self.last_kwargs = kwargs
        return json.dumps({"ok": True})


def _client_with_recording_backend() -> tuple[LLMClient, _RecordingBackend]:
    cfg = LLMConfig(provider=LLMProvider.ANTHROPIC_COMPATIBLE, api_key="x", base_url="http://x/v1")
    client = LLMClient.__new__(LLMClient)  # 跳过真实 backend 构造（避免 SDK 依赖）
    client.config = cfg
    client._call_count = 0
    client._log_interval = 10
    client._log_dir = None
    client._request_semaphore = None
    client._model_semaphore = None
    backend = _RecordingBackend()
    client._backend = backend
    return client, backend


@pytest.mark.asyncio
async def test_generate_json_passes_response_format_through() -> None:
    client, backend = _client_with_recording_backend()
    result = await client.generate_json("hi")
    assert result == {"ok": True}
    # 关键断言：response_format 已被默认注入并透传到 backend（不再 TypeError）
    assert backend.last_kwargs.get("response_format") == {"type": "json_object"}


def test_generate_json_sync_passes_response_format_through() -> None:
    client, backend = _client_with_recording_backend()
    result = client.generate_json_sync("hi")
    assert result == {"ok": True}
    assert backend.last_kwargs.get("response_format") == {"type": "json_object"}


@pytest.mark.asyncio
async def test_generate_forwards_arbitrary_extra_kwargs() -> None:
    client, backend = _client_with_recording_backend()
    await client.generate("hi", temperature=0.3, max_tokens=128, seed=42, thinking={"type": "x"})
    kw = backend.last_kwargs
    assert kw["temperature"] == 0.3
    assert kw["max_tokens"] == 128
    assert kw["seed"] == 42
    assert kw["thinking"] == {"type": "x"}


@pytest.mark.asyncio
async def test_generate_drops_none_extra_kwargs() -> None:
    client, backend = _client_with_recording_backend()
    await client.generate("hi", seed=None)
    # None 值不应进入 kwargs（与 temperature/max_tokens 一致），交给 backend 决定默认
    assert "seed" not in backend.last_kwargs
