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

    # ── Tool Calling ─────────────────────────────────────

    async def generate_with_tools(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        **kwargs,
    ) -> dict:
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
                temperature=kwargs.get("temperature", self.config.temperature),
                system=system,
                messages=messages,
                tools=tools,
            )
            return self._parse_tool_response(response)
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e

    def generate_with_tools_sync(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        **kwargs,
    ) -> dict:
        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=kwargs.get("max_tokens", self.config.max_tokens),
                temperature=kwargs.get("temperature", self.config.temperature),
                system=system,
                messages=messages,
                tools=tools,
            )
            return self._parse_tool_response(response)
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e

    @staticmethod
    def _parse_tool_response(response) -> dict:
        """将 Anthropic 响应解析为统一格式"""
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })
        return {
            "content": content,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "raw_content": response.content,
        }
