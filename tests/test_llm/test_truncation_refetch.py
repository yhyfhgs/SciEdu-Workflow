"""REC-07 — Anthropic max_tokens 截断 → 翻倍重抓一次。"""

from __future__ import annotations

import asyncio
from typing import Any

from sciforge.llm.backends.anthropic import AnthropicBackend
from sciforge.llm.config import LLMConfig, LLMProvider


class _Block:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _Resp:
    def __init__(self, text: str, stop: str) -> None:
        self.content = [_Block(text)]
        self.stop_reason = stop
        self.usage = None


class _FakeMessages:
    def __init__(self, responses: list[_Resp]) -> None:
        self._r = list(responses)
        self.calls = 0
        self.last_max_tokens: int | None = None

    async def create(self, **kw: Any) -> _Resp:
        self.calls += 1
        self.last_max_tokens = kw.get("max_tokens")
        return self._r.pop(0)


class _FakeAsync:
    def __init__(self, responses: list[_Resp]) -> None:
        self.messages = _FakeMessages(responses)


def _backend(responses: list[_Resp]) -> AnthropicBackend:
    be = AnthropicBackend(
        LLMConfig(provider=LLMProvider.ANTHROPIC, model="claude-x", api_key="dummy")
    )
    be.async_client = _FakeAsync(responses)  # type: ignore[assignment]
    return be


def test_refetch_on_max_tokens() -> None:
    be = _backend([_Resp("截断的部分", "max_tokens"), _Resp("完整结果", "end_turn")])
    out = asyncio.run(be.generate("p", "s", max_tokens=4096))
    assert out == "完整结果"
    assert be.async_client.messages.calls == 2  # type: ignore[attr-defined]
    assert be.async_client.messages.last_max_tokens == 8192  # type: ignore[attr-defined]


def test_no_refetch_when_complete() -> None:
    be = _backend([_Resp("一次就完整", "end_turn")])
    out = asyncio.run(be.generate("p", "s", max_tokens=4096))
    assert out == "一次就完整"
    assert be.async_client.messages.calls == 1  # type: ignore[attr-defined]


def test_no_refetch_when_at_ceiling() -> None:
    # max_tokens 已达上限 → 即便截断也不再翻倍（避免超模型上限 4xx）
    be = _backend([_Resp("截断但到顶", "max_tokens")])
    out = asyncio.run(be.generate("p", "s", max_tokens=32768))
    assert out == "截断但到顶"
    assert be.async_client.messages.calls == 1  # type: ignore[attr-defined]
