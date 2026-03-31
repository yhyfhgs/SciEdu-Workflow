"""Anthropic (Claude) 后端"""

from __future__ import annotations

import os

import structlog

from mathforge.core.exceptions import LLMError, LLMTimeoutError
from mathforge.llm.config import LLMConfig

logger = structlog.get_logger()


class AnthropicBackend:
    """Anthropic Claude API 后端，支持同步和异步"""

    def __init__(self, config: LLMConfig):
        try:
            import anthropic
        except ImportError:
            raise ImportError("需要安装 anthropic SDK: pip install anthropic")

        api_key = config.api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        self.async_client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        self.config = config

    async def generate(self, prompt: str, system: str, **kwargs) -> str:
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
                temperature=kwargs.get("temperature", self.config.temperature),
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e

    def generate_sync(self, prompt: str, system: str, **kwargs) -> str:
        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
                temperature=kwargs.get("temperature", self.config.temperature),
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e
