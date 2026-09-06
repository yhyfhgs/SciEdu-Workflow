"""REC-23 — Anthropic Message Batches API（提交/轮询/取回）。"""

from __future__ import annotations

import asyncio
from typing import Any

from sciforge.llm.backends.anthropic import AnthropicBackend
from sciforge.llm.config import LLMConfig, LLMProvider


class _Batch:
    id = "batch_abc"
    processing_status = "ended"


class _Block:
    type = "text"

    def __init__(self, t: str) -> None:
        self.text = t


class _Msg:
    def __init__(self, t: str) -> None:
        self.content = [_Block(t)]


class _Result:
    def __init__(self, t: str, ok: bool = True) -> None:
        self.type = "succeeded" if ok else "errored"
        self.message = _Msg(t) if ok else None


class _Entry:
    def __init__(self, cid: str, t: str, ok: bool = True) -> None:
        self.custom_id = cid
        self.result = _Result(t, ok)


class _AsyncResults:
    def __init__(self, entries: list[_Entry]) -> None:
        self._entries = entries

    def __aiter__(self) -> Any:
        async def gen() -> Any:
            for e in self._entries:
                yield e

        return gen()


class _Batches:
    def __init__(self) -> None:
        self.created_with: Any = None

    async def create(self, requests: list[dict[str, Any]]) -> _Batch:
        self.created_with = requests
        return _Batch()

    async def retrieve(self, batch_id: str) -> _Batch:
        return _Batch()

    async def results(self, batch_id: str) -> _AsyncResults:
        return _AsyncResults(
            [_Entry("q1", "答案1"), _Entry("q2", "答案2"), _Entry("q3", "", ok=False)]
        )


class _Messages:
    def __init__(self) -> None:
        self.batches = _Batches()


class _AsyncClient:
    def __init__(self) -> None:
        self.messages = _Messages()


def _backend() -> AnthropicBackend:
    be = AnthropicBackend(
        LLMConfig(provider=LLMProvider.ANTHROPIC, model="claude-x", api_key="dummy")
    )
    be.async_client = _AsyncClient()  # type: ignore[assignment]
    return be


def test_submit_batch_builds_payload() -> None:
    be = _backend()
    bid = asyncio.run(
        be.submit_message_batch(
            [
                {"custom_id": "q1", "prompt": "题1", "system": "sys", "max_tokens": 1000},
                {"custom_id": "q2", "prompt": "题2"},
            ]
        )
    )
    assert bid == "batch_abc"
    reqs = be.async_client.messages.batches.created_with  # type: ignore[attr-defined]
    assert reqs[0]["custom_id"] == "q1"
    assert reqs[0]["params"]["max_tokens"] == 1000
    assert reqs[0]["params"]["system"] == "sys"
    assert reqs[0]["params"]["messages"][0]["content"] == "题1"
    assert "system" not in reqs[1]["params"]  # 未给 system 不注入


def test_batch_status_and_results() -> None:
    be = _backend()
    status = asyncio.run(be.get_message_batch_status("batch_abc"))
    assert status == "ended"
    results = asyncio.run(be.fetch_message_batch_results("batch_abc"))
    assert results == {"q1": "答案1", "q2": "答案2", "q3": ""}  # 失败条目空串


def test_client_supports_batch() -> None:
    from sciforge.llm.client import LLMClient

    client = LLMClient(LLMConfig(provider=LLMProvider.ANTHROPIC, model="x", api_key="d"))
    assert client.supports_batch is True
