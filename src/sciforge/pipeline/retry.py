"""统一的异步重试工具

提供:
- with_retry: 通用异步重试装饰器（指数退避）
- call_llm_json_with_retry: LLM JSON 调用带重试 + 输出验证
- RetryExhausted: 重试用尽异常
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()


class RetryExhausted(Exception):  # noqa: N818 — 语义为"重试已用尽"，不强加 Error 后缀
    """达到最大重试次数后仍然失败"""

    def __init__(self, attempts: int, last_exc: BaseException | None) -> None:
        self.attempts = attempts
        self.last_exc = last_exc
        super().__init__(f"retry exhausted after {attempts} attempts: {last_exc!r}")


async def with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    on_retry: Callable[[int, BaseException], None] | None = None,
    **kwargs: Any,
) -> Any:
    """异步重试包装

    Args:
        fn: 待调用的协程函数
        max_attempts: 最大尝试次数 (含首次)
        base_delay: 初始等待秒数；第 N 次重试 sleep base_delay * (2 ** (N-1))
        on_retry: 每次重试前的回调 (attempt_index_1based, exception)
    """
    last_exc: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if on_retry:
                try:
                    on_retry(attempt, exc)
                except Exception:  # noqa: BLE001
                    pass
            if attempt == max_attempts:
                break
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))
    raise RetryExhausted(max_attempts, last_exc)


async def call_llm_json_with_retry(
    llm: LLMClient,
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int = 16384,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    validate: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """调用 LLM 的 generate_json 并附带重试 + 输出验证

    Args:
        validate: 可选校验函数，返回 False 视为失败将触发重试
    """

    def _on_retry(i: int, exc: BaseException) -> None:
        logger.info(
            "llm_retry",
            attempt=i,
            max_attempts=max_attempts,
            error=str(exc)[:200],
        )

    async def _call() -> dict[str, Any]:
        result = await llm.generate_json(prompt, system=system, max_tokens=max_tokens)
        if not isinstance(result, dict):
            raise ValueError(f"llm returned non-dict: {type(result).__name__}")
        if validate is not None and not validate(result):
            raise ValueError("llm output failed validation")
        return result

    out = await with_retry(
        _call,
        max_attempts=max_attempts,
        base_delay=base_delay,
        on_retry=_on_retry,
    )
    assert isinstance(out, dict)
    return out
