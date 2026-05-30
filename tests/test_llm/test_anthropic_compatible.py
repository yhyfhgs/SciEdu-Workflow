"""AnthropicCompatibleBackend 单元测试"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sciforge.llm.config import LLMConfig, LLMProvider


class TestAnthropicCompatibleBackend:
    def test_requires_base_url(self):
        """缺 base_url 时直接报错"""
        from sciforge.llm.backends.anthropic_compatible import (
            AnthropicCompatibleBackend,
        )

        with pytest.raises(ValueError, match="anthropic_compatible"):
            AnthropicCompatibleBackend(
                LLMConfig(
                    provider=LLMProvider.ANTHROPIC_COMPATIBLE,
                    api_key="test",
                )
            )

    def test_accepts_dummy_api_key(self):
        """空 api_key 被填充为 dummy"""
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            from sciforge.llm.backends.anthropic_compatible import (
                AnthropicCompatibleBackend,
            )

            backend = AnthropicCompatibleBackend(
                LLMConfig(
                    provider=LLMProvider.ANTHROPIC_COMPATIBLE,
                    base_url="https://api.z.ai/api/coding/paas/v4/anthropic",
                )
            )
            assert backend.config.api_key == "dummy"

    def test_passes_base_url_to_sdk(self):
        """SDK 构造时 base_url 被传入"""
        with (
            patch("anthropic.Anthropic") as mock_sync,
            patch("anthropic.AsyncAnthropic") as mock_async,
        ):
            from sciforge.llm.backends.anthropic_compatible import (
                AnthropicCompatibleBackend,
            )

            AnthropicCompatibleBackend(
                LLMConfig(
                    provider=LLMProvider.ANTHROPIC_COMPATIBLE,
                    api_key="sk-or-xyz",
                    base_url="https://openrouter.ai/api/v1",
                )
            )
            assert (
                mock_sync.call_args.kwargs["base_url"]
                == "https://openrouter.ai/api/v1"
            )
            assert (
                mock_async.call_args.kwargs["base_url"]
                == "https://openrouter.ai/api/v1"
            )
            assert mock_sync.call_args.kwargs["api_key"] == "sk-or-xyz"

    def test_generate_sync_calls_anthropic_sdk(self):
        """generate_sync 走 messages.create 接口"""
        with (
            patch("anthropic.Anthropic") as mock_sync,
            patch("anthropic.AsyncAnthropic") as mock_async,
        ):
            from sciforge.llm.backends.anthropic_compatible import (
                AnthropicCompatibleBackend,
            )

            backend = AnthropicCompatibleBackend(
                LLMConfig(
                    provider=LLMProvider.ANTHROPIC_COMPATIBLE,
                    api_key="test",
                    base_url="https://api.z.ai/api/coding/paas/v4/anthropic",
                    model="claude-sonnet-4-20250514",
                )
            )
            backend.client = mock_sync.return_value
            backend.async_client = mock_async.return_value

            mock_response = MagicMock()
            mock_response.content = [MagicMock(text="compat-ok")]
            backend.client.messages.create.return_value = mock_response

            result = backend.generate_sync(prompt="hi", system="sys")

            assert result == "compat-ok"
            call_kwargs = backend.client.messages.create.call_args.kwargs
            assert call_kwargs["model"] == "claude-sonnet-4-20250514"
            assert call_kwargs["system"] == "sys"
            assert call_kwargs["messages"] == [
                {"role": "user", "content": "hi"}
            ]

    def test_is_vision_and_tool_backend(self):
        """继承 AnthropicBackend 的 Vision / Tool Calling Protocol"""
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            from sciforge.llm.backends import ToolCallingBackend, VisionBackend
            from sciforge.llm.backends.anthropic_compatible import (
                AnthropicCompatibleBackend,
            )

            backend = AnthropicCompatibleBackend(
                LLMConfig(
                    provider=LLMProvider.ANTHROPIC_COMPATIBLE,
                    api_key="test",
                    base_url="https://api.z.ai/...",
                )
            )
            assert isinstance(backend, VisionBackend)
            assert isinstance(backend, ToolCallingBackend)

    def test_llm_client_routes_to_anthropic_compatible(self):
        """LLMClient._create_backend 正确路由到 AnthropicCompatibleBackend"""
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            from sciforge.llm.backends.anthropic_compatible import (
                AnthropicCompatibleBackend,
            )
            from sciforge.llm.client import LLMClient

            client = LLMClient(
                LLMConfig(
                    provider=LLMProvider.ANTHROPIC_COMPATIBLE,
                    base_url="https://openrouter.ai/api/v1",
                    api_key="test",
                )
            )
            assert isinstance(client._backend, AnthropicCompatibleBackend)
            assert client.supports_vision is True
            assert client.supports_tools is True

    def test_capability_matrix_drops_thinking(self):
        """anthropic_compatible 不支持 thinking，_resolve_params 会丢弃"""
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            from sciforge.llm.backends.anthropic_compatible import (
                AnthropicCompatibleBackend,
            )

            backend = AnthropicCompatibleBackend(
                LLMConfig(
                    provider=LLMProvider.ANTHROPIC_COMPATIBLE,
                    api_key="test",
                    base_url="https://api.z.ai/...",
                    thinking={"type": "enabled", "budget_tokens": 8192},
                    metadata={"user_id": "foo"},
                    top_k=40,
                )
            )

            resolved = backend._resolve_params()
            assert "thinking" not in resolved
            assert "metadata" not in resolved
            # 但基础参数保留
            assert resolved["top_k"] == 40
            assert resolved["temperature"] == 0.7
