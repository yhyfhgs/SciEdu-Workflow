"""REC-08 — anthropic_compatible prompt cache opt-in + 真实 token usage 采集。"""

from __future__ import annotations

from typing import Any

from sciforge.llm.backends.anthropic import _AUTO_CACHE_MIN_CHARS, AnthropicBackend
from sciforge.llm.capabilities import PROVIDER_CAPABILITIES
from sciforge.llm.config import LLMConfig, LLMProvider


def _backend(provider: LLMProvider, supports_cache: bool) -> AnthropicBackend:
    cfg = LLMConfig(
        provider=provider,
        model="claude-x",
        api_key="dummy",
        base_url="http://x",
        supports_prompt_cache=supports_cache,
    )
    return AnthropicBackend(cfg)


_LONG_SYSTEM = "指令" * (_AUTO_CACHE_MIN_CHARS)  # 远超阈值


def test_compatible_matrix_allows_cache_control() -> None:
    assert "cache_control" in PROVIDER_CAPABILITIES[LLMProvider.ANTHROPIC_COMPATIBLE]


def test_system_param_compatible_caches_when_opted_in() -> None:
    out = _backend(LLMProvider.ANTHROPIC_COMPATIBLE, True)._system_param(_LONG_SYSTEM)
    assert isinstance(out, list)
    assert out[0]["cache_control"]["type"] == "ephemeral"


def test_system_param_compatible_no_cache_by_default() -> None:
    out = _backend(LLMProvider.ANTHROPIC_COMPATIBLE, False)._system_param(_LONG_SYSTEM)
    assert out == _LONG_SYSTEM  # 未放行 → 原样返回，不缓存


def test_system_param_pure_anthropic_auto_caches() -> None:
    out = _backend(LLMProvider.ANTHROPIC, False)._system_param(_LONG_SYSTEM)
    assert isinstance(out, list)  # 纯 Anthropic 自动缓存


def test_extract_usage_reads_all_fields() -> None:
    class _Usage:
        input_tokens = 100
        output_tokens = 40
        cache_read_input_tokens = 90
        cache_creation_input_tokens = 10

    class _Resp:
        usage = _Usage()

    assert AnthropicBackend._extract_usage(_Resp()) == {
        "input_tokens": 100,
        "output_tokens": 40,
        "cache_read_input_tokens": 90,
        "cache_creation_input_tokens": 10,
    }


def test_extract_usage_handles_missing() -> None:
    class _Resp:
        usage = None

    assert AnthropicBackend._extract_usage(_Resp()) == {}


def test_from_app_config_threads_supports_prompt_cache() -> None:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.config import ProviderConfig

    app = AppConfig(
        providers={
            "gw": ProviderConfig(
                type=LLMProvider.ANTHROPIC_COMPATIBLE,
                base_url="http://x",
                supports_prompt_cache=True,
            )
        },
        default_provider_ref="gw",
    )
    cfg = LLMConfig.from_app_config(app, task="extract_and_classify")
    assert cfg.supports_prompt_cache is True


def test_backend_last_usage_starts_empty() -> None:
    b = _backend(LLMProvider.ANTHROPIC, False)
    assert b.last_usage == {}


def test_client_exposes_last_usage(monkeypatch: Any) -> None:
    from sciforge.llm.client import LLMClient

    cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, model="claude-x", api_key="dummy")
    client = LLMClient(cfg)
    client._backend.last_usage = {"input_tokens": 7}
    assert client.last_usage == {"input_tokens": 7}
