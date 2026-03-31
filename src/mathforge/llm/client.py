"""统一 LLM 客户端"""

from __future__ import annotations

import time

import structlog

from mathforge.llm.config import LLMConfig, LLMProvider
from mathforge.llm.parser import parse_llm_json

logger = structlog.get_logger()

DEFAULT_SYSTEM = "你是一位资深数学教育专家。"
JSON_SYSTEM = "你是一位资深数学教育专家。请严格按 JSON 格式输出，不要包含 markdown 代码块。"


class LLMClient:
    """统一 LLM 客户端，支持多提供者、同步/异步"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._backend = self._create_backend(config)

    def _create_backend(self, config: LLMConfig):
        match config.provider:
            case LLMProvider.ANTHROPIC:
                from mathforge.llm.backends.anthropic import AnthropicBackend
                return AnthropicBackend(config)
            case LLMProvider.OPENAI:
                from mathforge.llm.backends.openai_backend import OpenAIBackend
                return OpenAIBackend(config)
            case LLMProvider.GEMINI:
                from mathforge.llm.backends.gemini import GeminiBackend
                return GeminiBackend(config)
            case LLMProvider.OPENAI_COMPATIBLE:
                from mathforge.llm.backends.compatible import OpenAICompatibleBackend
                return OpenAICompatibleBackend(config)
            case _:
                raise ValueError(f"未知提供者: {config.provider}")

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """异步生成文本"""
        system = system or DEFAULT_SYSTEM
        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        logger.debug(
            "llm_request",
            provider=self.config.provider.value,
            model=self.config.model,
            temperature=kwargs.get("temperature", self.config.temperature),
        )

        start = time.monotonic()
        result = await self._backend.generate(prompt=prompt, system=system, **kwargs)
        elapsed = int((time.monotonic() - start) * 1000)

        logger.info(
            "llm_response",
            provider=self.config.provider.value,
            model=self.config.model,
            response_length=len(result),
            latency_ms=elapsed,
        )
        return result

    def generate_sync(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """同步生成文本"""
        system = system or DEFAULT_SYSTEM
        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        logger.debug(
            "llm_request",
            provider=self.config.provider.value,
            model=self.config.model,
            temperature=kwargs.get("temperature", self.config.temperature),
        )

        start = time.monotonic()
        result = self._backend.generate_sync(prompt=prompt, system=system, **kwargs)
        elapsed = int((time.monotonic() - start) * 1000)

        logger.info(
            "llm_response",
            provider=self.config.provider.value,
            model=self.config.model,
            response_length=len(result),
            latency_ms=elapsed,
        )
        return result

    async def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        **kwargs,
    ) -> dict:
        """异步生成并解析 JSON"""
        system = system or JSON_SYSTEM
        raw = await self.generate(prompt, system=system, **kwargs)
        return parse_llm_json(raw)

    def generate_json_sync(
        self,
        prompt: str,
        system: str | None = None,
        **kwargs,
    ) -> dict:
        """同步生成并解析 JSON"""
        system = system or JSON_SYSTEM
        raw = self.generate_sync(prompt, system=system, **kwargs)
        return parse_llm_json(raw)
