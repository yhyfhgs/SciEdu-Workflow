"""LLM backend 抽象基类

集中提供：
- `_resolve_params()` — 合并 config 字段 + 调用 kwargs，按 provider 能力矩阵过滤
- `_encode_image()`   — 图片 base64 编码辅助
- `_raise_mapped()`   — 底层 SDK 异常 → LLMRateLimitError / LLMTimeoutError / LLMError 归一化

子类只需实现 `_to_sdk_params()` 把中性参数映射到各自 SDK 的参数名。
"""

from __future__ import annotations

import base64
from typing import Any, NoReturn

import structlog

from sciforge.core.exceptions import LLMError, LLMRateLimitError, LLMTimeoutError
from sciforge.llm.capabilities import ALL_OPTIONAL_PARAMS, filter_params
from sciforge.llm.config import LLMConfig

logger = structlog.get_logger()


class BaseBackend:
    """Backend 共享基础设施"""

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    # ── 参数解析 ──────────────────────────────────────────

    def _resolve_params(self, **overrides: Any) -> dict[str, Any]:
        """合并 config 字段与调用 kwargs，按 provider 能力矩阵过滤

        返回的字典是"中性"命名（top_p / max_tokens / stop_sequences ...）；
        子类的 `_to_sdk_params()` 负责映射到实际 SDK 参数名。

        None 值会被静默剔除（由 filter_params 处理）。
        """
        merged: dict[str, Any] = {}
        for key in ALL_OPTIONAL_PARAMS:
            if key in overrides and overrides[key] is not None:
                merged[key] = overrides[key]
            else:
                val = getattr(self.config, key, None)
                if val is not None:
                    merged[key] = val

        accepted, dropped = filter_params(self.config.provider, merged)

        if dropped:
            logger.debug(
                "llm_params_dropped",
                provider=self.config.provider.value,
                model=self.config.model,
                dropped=dropped,
            )

        return accepted

    # 子类可覆写：把中性字段映射到各自 SDK 的参数命名
    def _to_sdk_params(self, neutral: dict[str, Any]) -> dict[str, Any]:
        return dict(neutral)

    # ── 图片编码 ──────────────────────────────────────────

    @staticmethod
    def _encode_image(image_data: bytes) -> str:
        """bytes → base64 字符串"""
        return base64.b64encode(image_data).decode()

    # ── 错误映射 ──────────────────────────────────────────

    @staticmethod
    def _raise_mapped(exc: Exception) -> NoReturn:
        """底层异常归一化：

        - HTTP 429 / rate_limit / quota_exceeded → LLMRateLimitError（可恢复）
        - timeout → LLMTimeoutError
        - 其他 → LLMError
        """
        # 优先走 SDK 明确的 status_code（比字符串匹配可靠）
        status = getattr(exc, "status_code", None)
        retry_after: float | None = None
        # 尝试从 HTTP 响应头取 Retry-After
        resp = getattr(exc, "response", None)
        if resp is not None:
            headers = getattr(resp, "headers", None)
            if headers is not None:
                for key in ("retry-after", "Retry-After", "x-ratelimit-reset"):
                    val = (
                        headers.get(key) if hasattr(headers, "get") else None
                    )
                    if val:
                        try:
                            retry_after = float(val)
                            break
                        except (TypeError, ValueError):
                            pass

        if status == 429:
            raise LLMRateLimitError(str(exc), retry_after_seconds=retry_after) from exc

        msg_lower = str(exc).lower()
        # 次级识别：SDK 异常类名
        exc_cls = type(exc).__name__.lower()
        if (
            "ratelimit" in exc_cls
            or "quotaexceeded" in exc_cls
            or "429" in msg_lower
            or "rate_limit" in msg_lower
            or "rate limit" in msg_lower
            or "quota" in msg_lower
            or "too many requests" in msg_lower
            or "insufficient_quota" in msg_lower
        ):
            raise LLMRateLimitError(str(exc), retry_after_seconds=retry_after) from exc

        if "timeout" in msg_lower or "timed out" in msg_lower:
            raise LLMTimeoutError(str(exc)) from exc

        raise LLMError(str(exc)) from exc
