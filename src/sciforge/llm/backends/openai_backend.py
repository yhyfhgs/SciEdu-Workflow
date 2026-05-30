"""OpenAI 后端"""

from __future__ import annotations

import os
from typing import Any, cast

import structlog

from sciforge.llm.backends.base import BaseBackend
from sciforge.llm.config import LLMConfig

logger = structlog.get_logger()


class OpenAIBackend(BaseBackend):
    """OpenAI API 后端，支持同步和异步"""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            import openai
        except ImportError as exc:
            raise ImportError("需要安装 openai SDK: pip install openai") from exc

        api_key = config.api_key or os.environ.get("OPENAI_API_KEY")
        client_kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": config.timeout,
            "max_retries": config.max_retries,
        }
        if config.base_url:
            client_kwargs["base_url"] = config.base_url

        self.client = openai.OpenAI(**client_kwargs)
        self.async_client = openai.AsyncOpenAI(**client_kwargs)

    # ── 参数映射 ──────────────────────────────────────────

    def _to_sdk_params(self, neutral: dict[str, Any]) -> dict[str, Any]:
        """中性字段 → OpenAI chat.completions.create kwargs

        关键重命名：
        - stop_sequences → stop
        其他字段保留原名：temperature/max_tokens/top_p/seed/response_format/
        frequency_penalty/presence_penalty/reasoning_effort/service_tier/extra_body
        """
        sdk: dict[str, Any] = {}
        for key, value in neutral.items():
            if key == "stop_sequences":
                sdk["stop"] = value
            else:
                sdk[key] = value
        return sdk

    def _build_message_list(self, system: str, user_content: Any) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

    # ── 基础文本生成 ──────────────────────────────────────

    async def generate(self, prompt: str, system: str, **kwargs: Any) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        try:
            response = await self.async_client.chat.completions.create(
                model=self.config.model,
                messages=cast(Any, self._build_message_list(system, prompt)),
                **sdk_params,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            self._raise_mapped(e)

    def generate_sync(self, prompt: str, system: str, **kwargs: Any) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=cast(Any, self._build_message_list(system, prompt)),
                **sdk_params,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            self._raise_mapped(e)

    # ── Vision (多模态) ───────────────────────────────────

    def _make_image_block(self, image_data: bytes, media_type: str) -> dict[str, Any]:
        """构建 base64 图片内容块（含 detail=high 以获得最佳识别精度）"""
        b64 = self._encode_image(image_data)
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{media_type};base64,{b64}",
                "detail": "high",
            },
        }

    async def generate_vision(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        user_content = [
            self._make_image_block(image_data, image_media_type),
            {"type": "text", "text": prompt},
        ]
        try:
            response = await self.async_client.chat.completions.create(
                model=self.config.model,
                messages=cast(Any, self._build_message_list(system, user_content)),
                **sdk_params,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            self._raise_mapped(e)

    async def generate_vision_multi(
        self,
        prompt: str,
        images: list[bytes],
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        """多图 vision 调用"""
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        user_content: list[dict[str, Any]] = [
            self._make_image_block(img, image_media_type) for img in images
        ]
        user_content.append({"type": "text", "text": prompt})
        try:
            response = await self.async_client.chat.completions.create(
                model=self.config.model,
                messages=cast(Any, self._build_message_list(system, user_content)),
                **sdk_params,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            self._raise_mapped(e)

    def generate_vision_sync(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        user_content = [
            self._make_image_block(image_data, image_media_type),
            {"type": "text", "text": prompt},
        ]
        try:
            response = self.client.chat.completions.create(
                model=self.config.model,
                messages=cast(Any, self._build_message_list(system, user_content)),
                **sdk_params,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            self._raise_mapped(e)

    # ── Tool Calling ─────────────────────────────────────

    def _build_tool_params(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        from sciforge.llm.tool_definitions import to_openai_format

        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        all_messages: list[dict[str, Any]] = [{"role": "system", "content": system}] + messages
        return {
            "model": self.config.model,
            "messages": all_messages,
            "tools": to_openai_format(tools),
            **sdk_params,
        }

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = self._build_tool_params(messages, system, tools, **kwargs)
        try:
            response = await self.async_client.chat.completions.create(**params)
            return self._parse_tool_response(response)
        except Exception as e:
            self._raise_mapped(e)

    def generate_with_tools_sync(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        params = self._build_tool_params(messages, system, tools, **kwargs)
        try:
            response = self.client.chat.completions.create(**params)
            return self._parse_tool_response(response)
        except Exception as e:
            self._raise_mapped(e)

    @staticmethod
    def _parse_tool_response(response: Any) -> dict[str, Any]:
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
