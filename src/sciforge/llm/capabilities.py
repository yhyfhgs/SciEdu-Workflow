"""LLM Provider 参数能力矩阵

定义每个 provider 实际支持的生成参数集合，用于在 backend 调用 SDK 前
过滤掉不支持的参数（静默丢弃 + DEBUG 日志）。

这样 TOML 配置里可以统一声明所有参数，backend 自动按自身能力裁剪，
用户不必为每个 provider 维护一份不同的字段子集。
"""

from __future__ import annotations

from typing import Any

import structlog

from sciforge.llm.config import LLMProvider

logger = structlog.get_logger()


PROVIDER_CAPABILITIES: dict[LLMProvider, frozenset[str]] = {
    LLMProvider.ANTHROPIC: frozenset(
        {
            "temperature",
            "max_tokens",
            "top_p",
            "top_k",
            "stop_sequences",
            "thinking",
            "metadata",
            "cache_control",
            "service_tier",
            "extra_body",
        }
    ),
    LLMProvider.ANTHROPIC_COMPATIBLE: frozenset(
        {
            "temperature",
            "max_tokens",
            "top_p",
            "top_k",
            "stop_sequences",
            # thinking：Anthropic 格式网关（MiniMax / Kimi / DeepSeek 等）多支持。
            # 关键用途：MiniMax-M3 等推理模型用 thinking={"type":"disabled"} 关思考，
            # 提取/标注这类高频抄写任务可提速 3×+（实测 38s→11s，thinking 4276→0 字）。
            "thinking",
            "extra_body",
        }
    ),
    LLMProvider.OPENAI: frozenset(
        {
            "temperature",
            "max_tokens",
            "top_p",
            "stop_sequences",
            "seed",
            "response_format",
            "frequency_penalty",
            "presence_penalty",
            "reasoning_effort",
            "service_tier",
            "extra_body",
        }
    ),
    LLMProvider.OPENAI_COMPATIBLE: frozenset(
        {
            "temperature",
            "max_tokens",
            "top_p",
            "stop_sequences",
            "seed",
            "response_format",
            "frequency_penalty",
            "presence_penalty",
            "reasoning_effort",
            "extra_body",
        }
    ),
    LLMProvider.GEMINI: frozenset(
        {
            "temperature",
            "max_tokens",
            "top_p",
            "top_k",
            "stop_sequences",
            "seed",
            "response_format",
        }
    ),
}


ALL_OPTIONAL_PARAMS: frozenset[str] = frozenset(
    {
        "temperature",
        "max_tokens",
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
    }
)


def filter_params(
    provider: LLMProvider, params: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """按 provider 能力矩阵过滤参数

    Args:
        provider: 目标 provider 枚举
        params: 待过滤的参数字典，None 值会被直接忽略

    Returns:
        (accepted, dropped)
        - accepted: 该 provider 支持的非 None 参数
        - dropped: 被丢弃的参数名列表（用于 DEBUG 日志）
    """
    supported = PROVIDER_CAPABILITIES.get(provider, frozenset())
    accepted: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in params.items():
        if value is None:
            continue
        if key in supported:
            accepted[key] = value
        else:
            dropped.append(key)
    return accepted, dropped
