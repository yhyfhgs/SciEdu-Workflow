"""LLM 配置与提供者枚举"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    OPENAI_COMPATIBLE = "openai_compatible"


class LLMConfig(BaseModel):
    """LLM 调用配置，支持任务预设和多级覆写"""

    provider: LLMProvider = LLMProvider.ANTHROPIC
    model: str = "claude-sonnet-4-20250514"
    temperature: float = Field(ge=0.0, le=2.0, default=0.7)
    max_tokens: int = Field(ge=1, le=65536, default=4096)
    api_key: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    timeout: int = 120
    max_retries: int = 3

    PRESETS: ClassVar[dict[str, dict]] = {
        "generate": {
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        "solve": {
            "temperature": 0.0,
            "max_tokens": 8192,
        },
        "review": {
            "temperature": 0.0,
            "max_tokens": 2048,
        },
        "tag": {
            "temperature": 0.0,
            "max_tokens": 1024,
        },
        "repair": {
            "temperature": 0.0,
            "max_tokens": 4096,
        },
    }

    @classmethod
    def for_task(cls, task: str, **overrides) -> LLMConfig:
        """获取任务预设配置，支持覆写"""
        preset = cls.PRESETS.get(task, cls.PRESETS["generate"])
        config = {**preset, **{k: v for k, v in overrides.items() if v is not None}}
        return cls(**config)

    @classmethod
    def from_app_config(cls, app: AppConfig, task: str = "generate", **overrides) -> LLMConfig:
        """从 AppConfig 构建，桥接环境变量与任务预设

        优先级: CLI overrides > 任务预设(temperature/max_tokens) > AppConfig/env
        """
        preset = cls.PRESETS.get(task, cls.PRESETS["generate"])

        # 解析 provider
        provider = LLMProvider(overrides.get("provider") or app.llm_provider)

        # 按 provider 选择 API key
        api_key_map = {
            LLMProvider.ANTHROPIC: app.anthropic_api_key,
            LLMProvider.OPENAI: app.openai_api_key,
            LLMProvider.GEMINI: app.google_api_key,
            LLMProvider.OPENAI_COMPATIBLE: app.openai_api_key,
        }

        base = {
            "provider": provider,
            "model": app.llm_model,
            "temperature": preset.get("temperature", app.llm_temperature),
            "max_tokens": preset.get("max_tokens", app.llm_max_tokens),
            "api_key": api_key_map.get(provider),
            "base_url": app.llm_base_url,
            "reasoning_effort": app.llm_reasoning_effort,
        }

        # CLI overrides 优先级最高
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)
