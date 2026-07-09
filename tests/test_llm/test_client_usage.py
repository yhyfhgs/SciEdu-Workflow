from __future__ import annotations

from types import SimpleNamespace

from sciforge.llm.client import LLMClient


def _client(usage: dict[str, int]) -> LLMClient:
    client = LLMClient.__new__(LLMClient)
    client._backend = SimpleNamespace(last_usage=usage)
    client._call_count = 0
    client._total_tokens = 0
    client._log_interval = 10
    client._log_dir = None
    return client


def test_cumulative_usage_prefers_provider_tokens() -> None:
    client = _client(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_input_tokens": 20,
        }
    )

    client._log_exchange("prompt", "response", 3)

    assert client.call_count == 1
    assert client.total_tokens == 35


def test_cumulative_usage_falls_back_to_character_estimate() -> None:
    client = _client({})

    client._log_exchange("a" * 8, "b" * 4, 3)

    assert client.call_count == 1
    assert client.total_tokens == 3
