"""OpenAI 后端"""

from __future__ import annotations

import os

import structlog

from mathforge.core.exceptions import LLMError, LLMTimeoutError
from mathforge.llm.config import LLMConfig

logger = structlog.get_logger()


class OpenAIBackend:
    """OpenAI API 后端，支持同步和异步"""

    def __init__(self, config: LLMConfig):
        try:
            import openai
        except ImportError:
            raise ImportError("需要安装 openai SDK: pip install openai")

        api_key = config.api_key or os.environ.get("OPENAI_API_KEY")
        kwargs = {
            "api_key": api_key,
            "timeout": config.timeout,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            kwargs["base_url"] = config.base_url

        self.client = openai.OpenAI(**kwargs)
        self.async_client = openai.AsyncOpenAI(**kwargs)
        self.config = config

    def _build_params(self, prompt: str, system: str, **kwargs) -> dict:
        params = {
            "model": self.config.model,
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        if self.config.reasoning_effort:
            params["reasoning_effort"] = self.config.reasoning_effort
        return params

    async def generate(self, prompt: str, system: str, **kwargs) -> str:
        params = self._build_params(prompt, system, **kwargs)
        try:
            response = await self.async_client.chat.completions.create(**params)
            return response.choices[0].message.content
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e

    def generate_sync(self, prompt: str, system: str, **kwargs) -> str:
        params = self._build_params(prompt, system, **kwargs)
        try:
            response = self.client.chat.completions.create(**params)
            return response.choices[0].message.content
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e
