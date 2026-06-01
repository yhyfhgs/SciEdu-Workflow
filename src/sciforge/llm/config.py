"""LLM 配置与提供者枚举

两层 schema：
- [providers.*] — 命名连接池（type/api_key/base_url/default_model），可同时定义任意多个
- [tasks.*]     — 任务级配置，通过 provider_ref 引用 provider 别名，加任务级参数覆写

解析顺序（高→低）：
    CLI overrides
    > SCIFORGE_TASK_<TASK>_<FIELD> 环境变量
    > [tasks.<task>] TOML 字段
    > providers[provider_ref] 的默认值
    > LLMConfig.PRESETS[task] 的 temperature/max_tokens
    > LLMConfig 字段默认值
"""

from __future__ import annotations

import os
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
    ANTHROPIC_COMPATIBLE = "anthropic_compatible"


_ENV_API_KEY_MAP: dict[LLMProvider, str] = {
    LLMProvider.ANTHROPIC: "ANTHROPIC_API_KEY",
    LLMProvider.OPENAI: "OPENAI_API_KEY",
    LLMProvider.GEMINI: "GOOGLE_API_KEY",
}


def _resolve_env_api_key(ptype: LLMProvider) -> str | None:
    """按 provider 类型 fallback 到标准 vendor 环境变量；*_COMPATIBLE 不 fallback。"""
    env_name = _ENV_API_KEY_MAP.get(ptype)
    return os.environ.get(env_name) if env_name else None


class ProviderConfig(BaseModel):
    """命名的 LLM 连接配置（TOML [providers.<name>] 的一条）"""

    type: LLMProvider
    api_key: str | None = None
    base_url: str | None = None
    default_model: str | None = None
    timeout: int = 120
    max_retries: int = 3

    # Provider 级默认参数（tasks 可继续覆盖）
    temperature: float | None = None
    max_tokens: int | None = None


class TaskLLMConfig(BaseModel):
    """任务级 LLM 覆写配置，None 字段表示继承 provider / preset / 默认

    `provider_ref` 指向 AppConfig.providers 的 key；未指定则使用 default_provider_ref。
    """

    provider_ref: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None

    # 扩展生成参数（按需；不支持的 provider 会静默丢弃）
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    seed: int | None = None
    response_format: dict[str, Any] | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    thinking: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    metadata: dict[str, Any] | None = None
    cache_control: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None


class LLMConfig(BaseModel):
    """具体的 backend 构造配置：已完成多层合并的最终参数

    backend 在 `_resolve_params` 里按 provider 能力矩阵过滤这些字段，
    只把支持的参数传入底层 SDK。
    """

    provider: LLMProvider = LLMProvider.ANTHROPIC
    model: str = "claude-sonnet-4-20250514"
    temperature: float = Field(ge=0.0, le=2.0, default=0.7)
    # 上限是对**输出 token**的 sanity 边界（≠ 上下文窗口）。设为 1M 以覆盖大输出模型
    # （如 MiniMax-M3 支持 512K 输出）；具体上限由各 provider 按模型强制（超限返回 4xx）。
    max_tokens: int = Field(ge=1, le=1_000_000, default=16384)
    api_key: str | None = None
    base_url: str | None = None
    timeout: int = 120
    max_retries: int = 3

    # 扩展参数（所有 Optional，None 表示不传给 SDK）
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] | None = None
    seed: int | None = None
    response_format: dict[str, Any] | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    thinking: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    metadata: dict[str, Any] | None = None
    cache_control: dict[str, Any] | None = None
    extra_body: dict[str, Any] | None = None

    PRESETS: ClassVar[dict[str, dict[str, Any]]] = {
        "generate": {"temperature": 0.7, "max_tokens": 16384},
        "solve": {"temperature": 0.0, "max_tokens": 16384},
        "solve_deep": {"temperature": 0.0, "max_tokens": 32768},
        "review": {"temperature": 0.0, "max_tokens": 8192},
        "tag": {"temperature": 0.0, "max_tokens": 4096},
        "repair": {"temperature": 0.0, "max_tokens": 8192},
        "split": {"temperature": 0.2, "max_tokens": 8192},
        "process_item": {"temperature": 0.1, "max_tokens": 8192},
        "infer_relations": {"temperature": 0.3, "max_tokens": 8192},
        "improve": {"temperature": 0.3, "max_tokens": 16384},
        "numerical_verify": {"temperature": 0.0, "max_tokens": 8192},
        "vlm_convert": {"temperature": 0.0, "max_tokens": 8192},
        "extract_and_classify": {"temperature": 0.0, "max_tokens": 32768},
    }

    @classmethod
    def for_task(cls, task: str, **overrides: Any) -> LLMConfig:
        """按任务预设构造（不依赖 AppConfig，主要用于单元测试/直接调用）"""
        preset = cls.PRESETS.get(task, cls.PRESETS["generate"])
        config = {**preset, **{k: v for k, v in overrides.items() if v is not None}}
        return cls(**config)

    @classmethod
    def from_app_config(
        cls,
        app: AppConfig,
        task: str = "generate",
        **overrides: Any,
    ) -> LLMConfig:
        """从 AppConfig 解析最终 LLMConfig

        优先级:
            CLI overrides
            > task_llm[task] 字段
            > providers[provider_ref] 字段
            > PRESETS[task] (temperature/max_tokens)
            > LLMConfig 字段默认
        """
        preset = cls.PRESETS.get(task, cls.PRESETS["generate"])
        task_cfg = app.task_llm.get(task)

        # 1. 解析 provider_ref
        provider_ref: str | None = (
            overrides.get("provider_ref")
            or (task_cfg.provider_ref if task_cfg else None)
            or app.default_provider_ref
        )

        # 2. 引用了 provider_ref 但找不到 → 立即报错（避免静默回退掩盖配置错误）
        if provider_ref and provider_ref not in app.providers:
            available = list(app.providers.keys())
            raise ValueError(
                f"task {task!r} 引用的 provider_ref={provider_ref!r} 未在 "
                f"[providers.*] 中定义；已定义的 providers: "
                f"{available if available else '(无)'}"
            )

        provider_cfg: ProviderConfig | None = (
            app.providers.get(provider_ref) if provider_ref else None
        )

        # 3. 选择 provider type (+ provider 默认值)
        if provider_cfg is not None:
            provider_type = provider_cfg.type
            base_url = provider_cfg.base_url
            api_key = provider_cfg.api_key
            default_model = provider_cfg.default_model
            timeout = provider_cfg.timeout
            max_retries = provider_cfg.max_retries
            prov_temperature = provider_cfg.temperature
            prov_max_tokens = provider_cfg.max_tokens
        else:
            # 无 providers 定义（纯 CLI / 裸跑）→ 从 overrides 和默认值拿
            raw_provider = overrides.get("provider")
            provider_type = LLMProvider(raw_provider) if raw_provider else LLMProvider.ANTHROPIC
            base_url = None
            api_key = None
            default_model = None
            timeout = 120
            max_retries = 3
            prov_temperature = None
            prov_max_tokens = None

        # 3. api_key fallback 到标准 vendor 环境变量（*_COMPATIBLE 除外）
        if api_key is None:
            api_key = _resolve_env_api_key(provider_type)

        # 4. 解析 model
        model = (
            overrides.get("model")
            or (task_cfg.model if task_cfg else None)
            or default_model
            or cls.model_fields["model"].default
        )

        # 5. 解析 temperature (task > preset > provider > 默认)
        if task_cfg is not None and task_cfg.temperature is not None:
            temperature = task_cfg.temperature
        elif "temperature" in preset:
            temperature = preset["temperature"]
        elif prov_temperature is not None:
            temperature = prov_temperature
        else:
            temperature = 0.7

        # 6. 解析 max_tokens (task > preset > provider > 默认)
        if task_cfg is not None and task_cfg.max_tokens is not None:
            max_tokens = task_cfg.max_tokens
        elif "max_tokens" in preset:
            max_tokens = preset["max_tokens"]
        elif prov_max_tokens is not None:
            max_tokens = prov_max_tokens
        else:
            max_tokens = 16384

        base: dict[str, Any] = {
            "provider": provider_type,
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "api_key": api_key,
            "base_url": base_url,
            "timeout": timeout,
            "max_retries": max_retries,
        }

        # 7. 扩展参数从 task_cfg 拉取（None 保持缺省）
        if task_cfg is not None:
            for fld in (
                "top_p",
                "top_k",
                "stop_sequences",
                "seed",
                "response_format",
                "frequency_penalty",
                "presence_penalty",
                "thinking",
                "reasoning_effort",
                "service_tier",
                "metadata",
                "cache_control",
                "extra_body",
            ):
                val = getattr(task_cfg, fld, None)
                if val is not None:
                    base[fld] = val

        # 8. CLI overrides 覆盖一切（但 provider_ref 已在步骤 1 消费掉）
        for key, value in overrides.items():
            if value is None or key == "provider_ref":
                continue
            if key == "provider":
                base["provider"] = LLMProvider(value) if isinstance(value, str) else value
            else:
                base[key] = value

        return cls(**base)
