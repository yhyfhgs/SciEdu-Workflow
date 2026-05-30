"""Google Gemini 后端"""

from __future__ import annotations

import os
from typing import Any

import structlog

from sciforge.llm.backends.base import BaseBackend
from sciforge.llm.config import LLMConfig

logger = structlog.get_logger()


class GeminiBackend(BaseBackend):
    """Google Gemini API 后端，支持同步和异步"""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from google import genai
        except ImportError as exc:
            raise ImportError("需要安装 google-genai SDK: pip install 'sciforge[gemini]'") from exc

        api_key = config.api_key or os.environ.get("GOOGLE_API_KEY")
        self.client = genai.Client(api_key=api_key)

    # ── 参数映射 ──────────────────────────────────────────

    def _to_sdk_params(self, neutral: dict[str, Any]) -> dict[str, Any]:
        """中性字段 → google.genai GenerateContentConfig kwargs

        关键重命名：
        - max_tokens → max_output_tokens
        - stop_sequences → stop_sequences (保留)
        - response_format={"type":"json_object"} → response_mime_type="application/json"
        """
        sdk: dict[str, Any] = {}
        for key, value in neutral.items():
            if key == "max_tokens":
                sdk["max_output_tokens"] = value
            elif key == "response_format":
                if isinstance(value, dict) and value.get("type") == "json_object":
                    sdk["response_mime_type"] = "application/json"
            else:
                sdk[key] = value
        return sdk

    def _build_gen_config(self, system: str, **kwargs: Any) -> Any:
        """构造 google.genai.types.GenerateContentConfig"""
        from google.genai import types

        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        return types.GenerateContentConfig(
            system_instruction=system,
            **sdk_params,
        )

    # ── 基础文本生成 ──────────────────────────────────────

    async def generate(self, prompt: str, system: str, **kwargs: Any) -> str:
        config = self._build_gen_config(system=system, **kwargs)
        try:
            response = await self.client.aio.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            self._raise_mapped(e)

    def generate_sync(self, prompt: str, system: str, **kwargs: Any) -> str:
        config = self._build_gen_config(system=system, **kwargs)
        try:
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            self._raise_mapped(e)

    # ── Vision (多模态) ───────────────────────────────────

    def _build_vision_contents(
        self, prompt: str, image_data: bytes, image_media_type: str
    ) -> list[Any]:
        from google.genai import types

        return [
            types.Part.from_bytes(data=image_data, mime_type=image_media_type),
            prompt,
        ]

    def _build_multi_vision_contents(
        self, prompt: str, images: list[bytes], image_media_type: str
    ) -> list[Any]:
        from google.genai import types

        parts: list[Any] = [
            types.Part.from_bytes(data=img, mime_type=image_media_type) for img in images
        ]
        parts.append(prompt)
        return parts

    async def generate_vision(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        config = self._build_gen_config(system=system, **kwargs)
        contents = self._build_vision_contents(prompt, image_data, image_media_type)
        try:
            response = await self.client.aio.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=config,
            )
            return response.text or ""
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
        config = self._build_gen_config(system=system, **kwargs)
        contents = self._build_multi_vision_contents(prompt, images, image_media_type)
        try:
            response = await self.client.aio.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=config,
            )
            return response.text or ""
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
        config = self._build_gen_config(system=system, **kwargs)
        contents = self._build_vision_contents(prompt, image_data, image_media_type)
        try:
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            self._raise_mapped(e)
