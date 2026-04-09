"""Vision 后端 Protocol 和消息构造测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mathforge.llm.backends import VisionBackend
from mathforge.llm.config import LLMConfig, LLMProvider

# ── Protocol 一致性 ──────────────────────────────────────


class TestVisionBackendProtocol:
    """验证各后端实现了 VisionBackend Protocol"""

    def test_anthropic_is_vision_backend(self):
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            from mathforge.llm.backends.anthropic import AnthropicBackend

            cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test")
            backend = AnthropicBackend(cfg)
            assert isinstance(backend, VisionBackend)

    def test_openai_is_vision_backend(self):
        with patch("openai.OpenAI"), patch("openai.AsyncOpenAI"):
            from mathforge.llm.backends.openai_backend import OpenAIBackend

            cfg = LLMConfig(provider=LLMProvider.OPENAI, api_key="test")
            backend = OpenAIBackend(cfg)
            assert isinstance(backend, VisionBackend)

    def test_gemini_is_vision_backend(self):
        with patch("google.genai.Client"):
            from mathforge.llm.backends.gemini import GeminiBackend

            cfg = LLMConfig(provider=LLMProvider.GEMINI, api_key="test")
            backend = GeminiBackend(cfg)
            assert isinstance(backend, VisionBackend)


# ── Anthropic 消息构造 ───────────────────────────────────


class TestAnthropicVision:
    @pytest.fixture
    def backend(self):
        with (
            patch("anthropic.Anthropic") as mock_sync,
            patch("anthropic.AsyncAnthropic") as mock_async,
        ):
            from mathforge.llm.backends.anthropic import AnthropicBackend

            cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test")
            b = AnthropicBackend(cfg)
            b.client = mock_sync.return_value
            b.async_client = mock_async.return_value
            return b

    def test_vision_sync_message_format(self, backend):
        """验证 Anthropic vision 消息格式包含 image + text blocks"""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="result")]
        backend.client.messages.create.return_value = mock_response

        image_data = b"\x89PNG\r\n"
        result = backend.generate_vision_sync(
            prompt="describe",
            image_data=image_data,
            image_media_type="image/png",
            system="sys",
        )

        assert result == "result"
        call_kwargs = backend.client.messages.create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert len(messages) == 1
        content = messages[0]["content"]
        assert content[0]["type"] == "image"
        assert content[0]["source"]["type"] == "base64"
        assert content[0]["source"]["media_type"] == "image/png"
        assert content[1]["type"] == "text"
        assert content[1]["text"] == "describe"

    @pytest.mark.asyncio
    async def test_vision_async_message_format(self, backend):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="async_result")]
        backend.async_client.messages.create = AsyncMock(return_value=mock_response)

        result = await backend.generate_vision(
            prompt="describe",
            image_data=b"\x89PNG\r\n",
            image_media_type="image/png",
            system="sys",
        )

        assert result == "async_result"


# ── OpenAI 消息构造 ──────────────────────────────────────


class TestOpenAIVision:
    @pytest.fixture
    def backend(self):
        with patch("openai.OpenAI") as mock_sync, patch("openai.AsyncOpenAI") as mock_async:
            from mathforge.llm.backends.openai_backend import OpenAIBackend

            cfg = LLMConfig(provider=LLMProvider.OPENAI, api_key="test")
            b = OpenAIBackend(cfg)
            b.client = mock_sync.return_value
            b.async_client = mock_async.return_value
            return b

    def test_vision_sync_message_format(self, backend):
        """验证 OpenAI vision 消息格式包含 system + user(image_url + text)"""
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="result"))]
        backend.client.chat.completions.create.return_value = mock_response

        result = backend.generate_vision_sync(
            prompt="describe",
            image_data=b"\x89PNG\r\n",
            image_media_type="image/png",
            system="sys",
        )

        assert result == "result"
        call_kwargs = backend.client.chat.completions.create.call_args
        messages = call_kwargs.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        content = messages[1]["content"]
        assert content[0]["type"] == "image_url"
        assert content[0]["image_url"]["detail"] == "high"
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert content[1]["type"] == "text"


# ── LLMClient 集成 ───────────────────────────────────────


class TestLLMClientVision:
    def test_supports_vision_true(self):
        with patch("anthropic.Anthropic"), patch("anthropic.AsyncAnthropic"):
            from mathforge.llm.client import LLMClient

            cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test")
            client = LLMClient(cfg)
            assert client.supports_vision is True

    def test_supports_vision_false_for_non_vision(self):
        """自定义 backend 不实现 VisionBackend 时返回 False"""
        from mathforge.llm.client import LLMClient

        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test")
        client = LLMClient.__new__(LLMClient)
        client.config = cfg
        client._backend = MagicMock(spec=[])  # 无 generate_vision 方法
        client._call_count = 0
        client._log_interval = 10
        client._log_dir = None
        assert client.supports_vision is False

    @pytest.mark.asyncio
    async def test_generate_with_image_raises_without_vision(self):
        from mathforge.core.exceptions import LLMVisionError
        from mathforge.llm.client import LLMClient

        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test")
        client = LLMClient.__new__(LLMClient)
        client.config = cfg
        client._backend = MagicMock(spec=[])
        client._call_count = 0
        client._log_interval = 10
        client._log_dir = None

        with pytest.raises(LLMVisionError):
            await client.generate_with_image("prompt", b"image")
