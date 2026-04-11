"""LLM 配置与提供者枚举"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig


class LLMProvider(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    OPENAI_COMPATIBLE = "openai_compatible"


class TaskLLMConfig(BaseModel):
    """单个任务的 LLM 覆写配置，None 字段继承全局默认"""

    provider: LLMProvider | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    max_retries: int | None = None
    extra_body: dict[str, Any] | None = None


class LLMConfig(BaseModel):
    """LLM 调用配置，支持任务预设和多级覆写"""

    provider: LLMProvider = LLMProvider.ANTHROPIC
    model: str = "claude-sonnet-4-20250514"
    temperature: float = Field(ge=0.0, le=2.0, default=0.7)
    max_tokens: int = Field(ge=1, le=65536, default=16384)
    api_key: str | None = None
    base_url: str | None = None
    reasoning_effort: str | None = None
    timeout: int = 120
    max_retries: int = 3
    extra_body: dict[str, Any] | None = None

    PRESETS: ClassVar[dict[str, dict]] = {
        "generate": {
            "temperature": 0.7,
            "max_tokens": 16384,
        },
        "solve": {
            "temperature": 0.0,
            "max_tokens": 16384,
        },
        "review": {
            "temperature": 0.0,
            "max_tokens": 8192,
        },
        "tag": {
            "temperature": 0.0,
            "max_tokens": 4096,
        },
        "repair": {
            "temperature": 0.0,
            "max_tokens": 8192,
        },
        "split": {
            "temperature": 0.2,
            "max_tokens": 8192,
        },
        "process_item": {
            "temperature": 0.1,
            "max_tokens": 8192,
        },
        "infer_relations": {
            "temperature": 0.3,
            "max_tokens": 8192,
        },
        "solve_deep": {
            "temperature": 0.0,
            "max_tokens": 32768,
        },
        "improve": {
            "temperature": 0.3,
            "max_tokens": 16384,
        },
        "numerical_verify": {
            "temperature": 0.0,
            "max_tokens": 8192,
        },
        "vlm_convert": {
            "temperature": 0.0,
            "max_tokens": 8192,
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

        优先级: CLI overrides > per-task 覆写 > 任务预设(temperature/max_tokens) > AppConfig/env
        """
        preset = cls.PRESETS.get(task, cls.PRESETS["generate"])

        # 获取 per-task 覆写（来自 TOML 文件或 MATHFORGE_TASK_* 环境变量）
        task_ovr = app.task_llm.get(task)

        # 解析 provider（优先级：CLI > task_override > global）
        provider = LLMProvider(
            overrides.get("provider")
            or (task_ovr.provider if task_ovr and task_ovr.provider else None)
            or app.llm_provider
        )

        # 按 provider 选择 API key
        api_key_map = {
            LLMProvider.ANTHROPIC: app.anthropic_api_key,
            LLMProvider.OPENAI: app.openai_api_key,
            LLMProvider.GEMINI: app.google_api_key,
            LLMProvider.OPENAI_COMPATIBLE: app.openai_api_key,
        }

        base: dict = {
            "provider": provider,
            "model": (task_ovr.model if task_ovr and task_ovr.model else None) or app.llm_model,
            "temperature": preset.get("temperature", app.llm_temperature),
            "max_tokens": preset.get("max_tokens", app.llm_max_tokens),
            "api_key": (task_ovr.api_key if task_ovr and task_ovr.api_key else None)
            or api_key_map.get(provider),
            "base_url": (task_ovr.base_url if task_ovr and task_ovr.base_url else None)
            or app.llm_base_url,
            "reasoning_effort": (
                task_ovr.reasoning_effort if task_ovr and task_ovr.reasoning_effort else None
            )
            or app.llm_reasoning_effort,
        }

        # task_override 中的 temperature/max_tokens/timeout/max_retries/extra_body 覆盖 preset
        if task_ovr:
            for fld in ("temperature", "max_tokens", "timeout", "max_retries"):
                val = getattr(task_ovr, fld, None)
                if val is not None:
                    base[fld] = val
            if task_ovr.extra_body:
                base["extra_body"] = task_ovr.extra_body

        # CLI overrides 优先级最高
        base.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**base)
