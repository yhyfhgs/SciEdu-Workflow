"""Anthropic (Claude) 后端"""

from __future__ import annotations

import os
from typing import Any, cast

import structlog

from sciforge.llm.backends.base import BaseBackend
from sciforge.llm.config import LLMConfig

logger = structlog.get_logger()


class AnthropicBackend(BaseBackend):
    """Anthropic Claude API 后端，支持同步和异步"""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("需要安装 anthropic SDK: pip install anthropic") from exc

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

    @staticmethod
    def _extract_text(response: Any) -> str:
        """从响应中提取文本，跳过 thinking block"""
        for block in response.content:
            if getattr(block, "type", None) == "text" and block.text:
                return cast(str, block.text)
        return cast(str, response.content[0].text or "")

    # ── 参数映射 ──────────────────────────────────────────

    def _to_sdk_params(self, neutral: dict[str, Any]) -> dict[str, Any]:
        """中性字段 → Anthropic Messages API kwargs

        Anthropic SDK 的参数命名大多与中性命名一致（temperature/max_tokens/
        top_p/top_k/stop_sequences/thinking/metadata/service_tier/extra_body），
        但 cache_control 必须注入到 content block 里（不是 top-level kwarg），
        在这里剔除。
        """
        return {k: v for k, v in neutral.items() if k != "cache_control"}

    def _maybe_inject_cache_control(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """若 config.cache_control 非空，把它注入到最后一个 user message 的末尾文本块"""
        if not self.config.cache_control:
            return messages

        cache_spec = self.config.cache_control
        out = [dict(m) for m in messages]
        if not out:
            return out
        last = out[-1]
        content = last.get("content")
        if isinstance(content, str):
            last["content"] = [{"type": "text", "text": content, "cache_control": cache_spec}]
        elif isinstance(content, list) and content:
            tail = content[-1]
            if isinstance(tail, dict):
                tail["cache_control"] = cache_spec
        return out

    # ── 基础文本生成 ──────────────────────────────────────

    async def generate(self, prompt: str, system: str, **kwargs: Any) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = self._maybe_inject_cache_control([{"role": "user", "content": prompt}])
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                **sdk_params,
            )
            return self._extract_text(response)
        except Exception as e:
            self._raise_mapped(e)

    def generate_sync(self, prompt: str, system: str, **kwargs: Any) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = self._maybe_inject_cache_control([{"role": "user", "content": prompt}])
        try:
            response = self.client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                **sdk_params,
            )
            return self._extract_text(response)
        except Exception as e:
            self._raise_mapped(e)

    # ── Vision (多模态) ───────────────────────────────────

    def _make_image_block(self, image_data: bytes, media_type: str) -> dict[str, Any]:
        b64 = self._encode_image(image_data)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        }

    def _build_vision_message(
        self, prompt: str, image_data: bytes, image_media_type: str
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    self._make_image_block(image_data, image_media_type),
                    {"type": "text", "text": prompt},
                ],
            }
        ]

    def _build_multi_vision_message(
        self, prompt: str, images: list[bytes], image_media_type: str
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            self._make_image_block(img, image_media_type) for img in images
        ]
        content.append({"type": "text", "text": prompt})
        return [{"role": "user", "content": content}]

    async def generate_vision(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = self._maybe_inject_cache_control(
            self._build_vision_message(prompt, image_data, image_media_type)
        )
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                **sdk_params,
            )
            return self._extract_text(response)
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
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = self._maybe_inject_cache_control(
            self._build_multi_vision_message(prompt, images, image_media_type)
        )
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                **sdk_params,
            )
            return self._extract_text(response)
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
        messages = self._maybe_inject_cache_control(
            self._build_vision_message(prompt, image_data, image_media_type)
        )
        try:
            response = self.client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                **sdk_params,
            )
            return self._extract_text(response)
        except Exception as e:
            self._raise_mapped(e)

    # ── Tool Calling ─────────────────────────────────────

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                tools=cast(Any, tools),
                **sdk_params,
            )
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
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        try:
            response = self.client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                tools=cast(Any, tools),
                **sdk_params,
            )
            return self._parse_tool_response(response)
        except Exception as e:
            self._raise_mapped(e)

    @staticmethod
    def _parse_tool_response(response: Any) -> dict[str, Any]:
        """将 Anthropic 响应解析为统一格式"""
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input,
                    }
                )
        return {
            "content": content,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "raw_content": response.content,
        }
