"""Google Gemini 后端"""

from __future__ import annotations

import os

import structlog

from sciforge.core.exceptions import LLMError, LLMTimeoutError
from sciforge.llm.config import LLMConfig

logger = structlog.get_logger()


class GeminiBackend:
    """Google Gemini API 后端，支持同步和异步"""

    def __init__(self, config: LLMConfig):
        try:
            from google import genai
        except ImportError:
            raise ImportError("需要安装 google-genai SDK: pip install 'sciforge[gemini]'")

        api_key = config.api_key or os.environ.get("GOOGLE_API_KEY")
        self.client = genai.Client(api_key=api_key)
        self.config = config

    def _build_gen_config(self, **kwargs):
        from google.genai import types

        return types.GenerateContentConfig(
            system_instruction=kwargs.get("system", ""),
            temperature=kwargs.get("temperature", self.config.temperature),
            max_output_tokens=kwargs.get("max_tokens", self.config.max_tokens),
        )

    async def generate(self, prompt: str, system: str, **kwargs) -> str:
        config = self._build_gen_config(system=system, **kwargs)
        try:
            response = await self.client.aio.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e

    def generate_sync(self, prompt: str, system: str, **kwargs) -> str:
        config = self._build_gen_config(system=system, **kwargs)
        try:
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e

    # ── Vision (多模态) ───────────────────────────────────

    async def generate_vision(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs,
    ) -> str:
        from google.genai import types

        config = self._build_gen_config(system=system, **kwargs)
        contents = [
            types.Part.from_bytes(data=image_data, mime_type=image_media_type),
            prompt,
        ]
        try:
            response = await self.client.aio.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e

    def generate_vision_sync(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs,
    ) -> str:
        from google.genai import types

        config = self._build_gen_config(system=system, **kwargs)
        contents = [
            types.Part.from_bytes(data=image_data, mime_type=image_media_type),
            prompt,
        ]
        try:
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=contents,
                config=config,
            )
            return response.text or ""
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                raise LLMTimeoutError(str(e)) from e
            raise LLMError(str(e)) from e
