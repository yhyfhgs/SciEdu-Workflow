"""Backend 参数路由 + 能力过滤 + SDK 映射测试"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sciforge.llm.config import LLMConfig, LLMProvider

__all__: list[str] = []


# ── Anthropic 参数路由 ─────────────────────────────────────


class TestAnthropicParamRouting:
    @pytest.fixture
    def backend(self):
        with (
            patch("anthropic.Anthropic") as mock_sync,
            patch("anthropic.AsyncAnthropic") as mock_async,
        ):
            from sciforge.llm.backends.anthropic import AnthropicBackend

            cfg = LLMConfig(
                provider=LLMProvider.ANTHROPIC,
                api_key="test",
                top_p=0.9,
                top_k=40,
                stop_sequences=["<END>"],
                thinking={"type": "enabled", "budget_tokens": 8192},
            )
            b = AnthropicBackend(cfg)
            b.client = mock_sync.return_value
            b.async_client = mock_async.return_value
            return b

    def test_extended_params_reach_sdk(self, backend):
        """top_p / top_k / stop_sequences / thinking 都应该传到 messages.create"""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        backend.client.messages.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="sys")

        call_kwargs = backend.client.messages.create.call_args.kwargs
        assert call_kwargs["top_p"] == 0.9
        assert call_kwargs["top_k"] == 40
        assert call_kwargs["stop_sequences"] == ["<END>"]
        # thinking 经 extra_body 透传（兼容老 SDK + anthropic 兼容网关），非 top-level
        assert "thinking" not in call_kwargs
        assert call_kwargs["extra_body"]["thinking"] == {
            "type": "enabled",
            "budget_tokens": 8192,
        }
        assert call_kwargs["temperature"] == 0.7  # default
        assert call_kwargs["max_tokens"] == 16384  # default

    def test_cache_control_on_system_block(self, backend):
        """cache_control 不是 top-level kwarg；显式配置时注入到 **system block**（修复 D11）。

        旧行为是注入 user message 末尾（动态 → 永不命中）；新行为缓存 system 前缀。
        """
        backend.config = LLMConfig(
            provider=LLMProvider.ANTHROPIC,
            api_key="test",
            cache_control={"type": "ephemeral"},
        )
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        backend.client.messages.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="sys")

        call_kwargs = backend.client.messages.create.call_args.kwargs
        assert "cache_control" not in call_kwargs
        # system 被包成带 cache_control 的 content block 列表
        system = call_kwargs["system"]
        assert isinstance(system, list)
        assert system[-1]["cache_control"] == {"type": "ephemeral"}
        assert system[-1]["text"] == "sys"
        # user message 保持纯字符串（不再注入缓存）
        assert call_kwargs["messages"][-1]["content"] == "hi"

    def test_long_system_auto_cached(self, backend):
        """纯 Anthropic + 长 system（≥阈值）自动缓存，无需显式配置。"""
        backend.config = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test")
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        backend.client.messages.create.return_value = mock_response

        long_system = "指令" * 1200  # 2400 字 > 阈值 2048（按模型家族路由，默认 2048）
        backend.generate_sync(prompt="hi", system=long_system)

        system = backend.client.messages.create.call_args.kwargs["system"]
        assert isinstance(system, list)
        assert system[-1]["cache_control"] == {"type": "ephemeral"}

    def test_short_system_not_cached(self, backend):
        """短 system（无显式配置）不主动缓存 → 仍是纯字符串。"""
        backend.config = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test")
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        backend.client.messages.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="短")

        system = backend.client.messages.create.call_args.kwargs["system"]
        assert system == "短"

    def test_unsupported_param_dropped(self, backend):
        """Anthropic 不支持 seed / response_format / frequency_penalty"""
        backend.config = LLMConfig(
            provider=LLMProvider.ANTHROPIC,
            api_key="test",
            seed=42,
            frequency_penalty=0.5,
            response_format={"type": "json_object"},
        )
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        backend.client.messages.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="sys")

        call_kwargs = backend.client.messages.create.call_args.kwargs
        assert "seed" not in call_kwargs
        assert "frequency_penalty" not in call_kwargs
        assert "response_format" not in call_kwargs

    def test_kwargs_override_config(self, backend):
        """generate(temperature=0.2) 应该覆盖 config.temperature=0.7"""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]
        backend.client.messages.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="sys", temperature=0.2)

        call_kwargs = backend.client.messages.create.call_args.kwargs
        assert call_kwargs["temperature"] == 0.2


# ── OpenAI 参数路由 ───────────────────────────────────────


class TestOpenAIParamRouting:
    @pytest.fixture
    def backend(self):
        with (
            patch("openai.OpenAI") as mock_sync,
            patch("openai.AsyncOpenAI") as mock_async,
        ):
            from sciforge.llm.backends.openai_backend import OpenAIBackend

            cfg = LLMConfig(
                provider=LLMProvider.OPENAI,
                api_key="test",
                seed=42,
                response_format={"type": "json_object"},
                frequency_penalty=0.1,
                reasoning_effort="high",
                stop_sequences=["<STOP>"],
                extra_body={"provider": {"order": ["z-ai"]}},
            )
            b = OpenAIBackend(cfg)
            b.client = mock_sync.return_value
            b.async_client = mock_async.return_value
            return b

    def test_extended_params_reach_sdk(self, backend):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        backend.client.chat.completions.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="sys")

        call_kwargs = backend.client.chat.completions.create.call_args.kwargs
        assert call_kwargs["seed"] == 42
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert call_kwargs["frequency_penalty"] == 0.1
        assert call_kwargs["reasoning_effort"] == "high"

    def test_stop_sequences_renamed_to_stop(self, backend):
        """OpenAI 使用 `stop` 而非 `stop_sequences`"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        backend.client.chat.completions.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="sys")

        call_kwargs = backend.client.chat.completions.create.call_args.kwargs
        assert call_kwargs["stop"] == ["<STOP>"]
        assert "stop_sequences" not in call_kwargs

    def test_extra_body_passthrough(self, backend):
        """OpenRouter provider routing via extra_body 必须保留"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        backend.client.chat.completions.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="sys")

        call_kwargs = backend.client.chat.completions.create.call_args.kwargs
        assert call_kwargs["extra_body"] == {"provider": {"order": ["z-ai"]}}

    def test_anthropic_only_param_dropped(self, backend):
        """OpenAI 不支持 top_k / thinking / cache_control"""
        backend.config = LLMConfig(
            provider=LLMProvider.OPENAI,
            api_key="test",
            top_k=40,
            thinking={"type": "enabled"},
            cache_control={"type": "ephemeral"},
        )
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
        backend.client.chat.completions.create.return_value = mock_response

        backend.generate_sync(prompt="hi", system="sys")

        call_kwargs = backend.client.chat.completions.create.call_args.kwargs
        assert "top_k" not in call_kwargs
        assert "thinking" not in call_kwargs
        assert "cache_control" not in call_kwargs


# ── Gemini 参数路由 ───────────────────────────────────────


class TestGeminiParamRouting:
    @pytest.fixture
    def backend(self):
        pytest.importorskip("google.genai")
        with patch("google.genai.Client"):
            from sciforge.llm.backends.gemini import GeminiBackend

            cfg = LLMConfig(
                provider=LLMProvider.GEMINI,
                api_key="test",
                top_p=0.95,
                top_k=40,
                seed=42,
                stop_sequences=["<END>"],
                response_format={"type": "json_object"},
            )
            return GeminiBackend(cfg)

    def test_max_tokens_renamed_to_max_output_tokens(self, backend):
        """Gemini 用 max_output_tokens 而非 max_tokens"""
        neutral = backend._resolve_params()
        sdk = backend._to_sdk_params(neutral)
        assert "max_output_tokens" in sdk
        assert "max_tokens" not in sdk

    def test_response_format_to_response_mime_type(self, backend):
        """response_format={"type":"json_object"} → response_mime_type="application/json" """
        neutral = backend._resolve_params()
        sdk = backend._to_sdk_params(neutral)
        assert sdk.get("response_mime_type") == "application/json"
        assert "response_format" not in sdk

    def test_stop_sequences_kept(self, backend):
        neutral = backend._resolve_params()
        sdk = backend._to_sdk_params(neutral)
        assert sdk["stop_sequences"] == ["<END>"]

    def test_frequency_penalty_dropped(self, backend):
        """Gemini 不支持 frequency_penalty / presence_penalty"""
        backend.config = LLMConfig(
            provider=LLMProvider.GEMINI,
            api_key="test",
            frequency_penalty=0.5,
            presence_penalty=0.5,
        )
        neutral = backend._resolve_params()
        sdk = backend._to_sdk_params(neutral)
        assert "frequency_penalty" not in sdk
        assert "presence_penalty" not in sdk


# ── OpenAI Compatible 继承 ─────────────────────────────────


class TestOpenAICompatibleInherits:
    def test_compatible_requires_base_url(self):
        from sciforge.llm.backends.compatible import OpenAICompatibleBackend

        with pytest.raises(ValueError, match="openai_compatible"):
            OpenAICompatibleBackend(
                LLMConfig(provider=LLMProvider.OPENAI_COMPATIBLE, api_key="test")
            )

    def test_compatible_accepts_dummy_api_key(self):
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            from sciforge.llm.backends.compatible import OpenAICompatibleBackend

            backend = OpenAICompatibleBackend(
                LLMConfig(
                    provider=LLMProvider.OPENAI_COMPATIBLE,
                    base_url="http://localhost:11434/v1",
                )
            )
            assert backend.config.api_key == "dummy"

    def test_compatible_inherits_extra_body(self):
        """OpenAI Compatible 继承 OpenAIBackend 的 extra_body 透传能力"""
        with patch("openai.OpenAI") as mock_sync, patch("openai.AsyncOpenAI"):
            from sciforge.llm.backends.compatible import OpenAICompatibleBackend

            backend = OpenAICompatibleBackend(
                LLMConfig(
                    provider=LLMProvider.OPENAI_COMPATIBLE,
                    base_url="https://openrouter.ai/api/v1",
                    api_key="sk-or-x",
                    extra_body={"provider": {"order": ["z-ai"]}},
                )
            )
            backend.client = mock_sync.return_value

            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
            backend.client.chat.completions.create.return_value = mock_response

            backend.generate_sync(prompt="hi", system="sys")

            call_kwargs = backend.client.chat.completions.create.call_args.kwargs
            assert call_kwargs["extra_body"] == {"provider": {"order": ["z-ai"]}}


# ── 错误映射 ─────────────────────────────────────────────


class TestErrorMapping:
    def test_timeout_mapped_to_llm_timeout_error(self):
        from sciforge.core.exceptions import LLMTimeoutError
        from sciforge.llm.backends.base import BaseBackend

        with pytest.raises(LLMTimeoutError):
            BaseBackend._raise_mapped(RuntimeError("Request timed out after 30s"))

    def test_other_exception_mapped_to_llm_error(self):
        from sciforge.core.exceptions import LLMError
        from sciforge.llm.backends.base import BaseBackend

        with pytest.raises(LLMError):
            BaseBackend._raise_mapped(RuntimeError("Invalid model name"))
