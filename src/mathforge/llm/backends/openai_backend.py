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

    # ── Tool Calling ─────────────────────────────────────

    def _build_tool_params(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        **kwargs,
    ) -> dict:
        from mathforge.llm.tool_definitions import to_openai_format

        all_messages = [{"role": "system", "content": system}] + messages
        params: dict = {
            "model": self.config.model,
            "max_tokens": kwargs.get("max_tokens", self.config.max_tokens),
            "temperature": kwargs.get("temperature", self.config.temperature),
            "messages": all_messages,
            "tools": to_openai_format(tools),
        }
        if self.config.reasoning_effort:
            params["reasoning_effort"] = self.config.reasoning_effort
        return params

    async def generate_with_tools(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        **kwargs,
    ) -> dict:
        params = self._build_tool_params(messages, system, tools, **kwargs)
        try:
            response = await self.async_client.chat.completions.create(**params)
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
        params = self._build_tool_params(messages, system, tools, **kwargs)
        try:
            response = self.client.chat.completions.create(**params)
            return self._parse_tool_response(response)
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e

    @staticmethod
    def _parse_tool_response(response) -> dict:
        """将 OpenAI 响应解析为统一格式"""
        import json

        message = response.choices[0].message
        content = message.content or ""
        tool_calls = []

        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, AttributeError):
                    args = {}
                tool_calls.append(
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": args,
                    }
                )

        finish_reason = response.choices[0].finish_reason
        stop_reason = "tool_use" if finish_reason == "tool_calls" else "end_turn"

        return {
            "content": content,
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
        }
