"""WP-41 LLMClient.generate_json 的 schema_model + output_config_json_schema_enabled。"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from sciforge.llm.client import LLMClient
from sciforge.llm.config import LLMConfig, LLMProvider


class _RecordingBackend:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, Any] = {}
        self.last_usage: dict[str, int] = {}

    async def generate(self, prompt: str, system: str, **kwargs: Any) -> str:
        self.last_kwargs = kwargs
        return json.dumps({"ok": True})

    def generate_sync(self, prompt: str, system: str, **kwargs: Any) -> str:
        self.last_kwargs = kwargs
        return json.dumps({"ok": True})


class _Schema(BaseModel):
    value: str = ""


def _client(provider: LLMProvider, *, strict_enabled: bool) -> tuple[LLMClient, _RecordingBackend]:
    cfg = LLMConfig(
        provider=provider,
        api_key="x",
        base_url="http://x/v1",
        output_config_json_schema_enabled=strict_enabled,
    )
    client = LLMClient.__new__(LLMClient)
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
async def test_flag_off_keeps_json_object_even_with_schema_model() -> None:
    """默认关闭：即便传了 schema_model，行为与现状完全一致（json_object）。"""
    client, backend = _client(LLMProvider.ANTHROPIC_COMPATIBLE, strict_enabled=False)
    await client.generate_json("hi", schema_model=_Schema)
    assert backend.last_kwargs.get("response_format") == {"type": "json_object"}
    assert "output_config" not in backend.last_kwargs


@pytest.mark.asyncio
async def test_no_schema_model_keeps_json_object_even_when_enabled() -> None:
    """开关打开但未传 schema_model → 仍退回 json_object（无法下发具体 schema）。"""
    client, backend = _client(LLMProvider.ANTHROPIC_COMPATIBLE, strict_enabled=True)
    await client.generate_json("hi")
    assert backend.last_kwargs.get("response_format") == {"type": "json_object"}


@pytest.mark.asyncio
async def test_anthropic_uses_output_config_when_enabled() -> None:
    client, backend = _client(LLMProvider.ANTHROPIC_COMPATIBLE, strict_enabled=True)
    await client.generate_json("hi", schema_model=_Schema)
    assert "response_format" not in backend.last_kwargs
    output_config = backend.last_kwargs["output_config"]
    assert output_config["format"]["type"] == "json_schema"
    assert output_config["format"]["name"] == "_Schema"
    assert output_config["format"]["schema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_openai_uses_strict_response_format_when_enabled() -> None:
    client, backend = _client(LLMProvider.OPENAI, strict_enabled=True)
    await client.generate_json("hi", schema_model=_Schema)
    rf = backend.last_kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["schema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_explicit_response_format_not_overridden() -> None:
    """调用方显式传 response_format 时，不管开关状态都不覆盖（现状约定不变）。"""
    client, backend = _client(LLMProvider.ANTHROPIC_COMPATIBLE, strict_enabled=True)
    await client.generate_json("hi", schema_model=_Schema, response_format={"type": "custom"})
    assert backend.last_kwargs["response_format"] == {"type": "custom"}
    assert "output_config" not in backend.last_kwargs


@pytest.mark.asyncio
async def test_gemini_falls_back_to_json_object_when_enabled() -> None:
    """Gemini 未接入严格 schema 通道（保持现状，避免引入未验证行为）。"""
    client, backend = _client(LLMProvider.GEMINI, strict_enabled=True)
    await client.generate_json("hi", schema_model=_Schema)
    assert backend.last_kwargs.get("response_format") == {"type": "json_object"}
