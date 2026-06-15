"""统一的异步重试工具

提供:
- with_retry: 通用异步重试装饰器（指数退避）
- call_llm_json_with_retry: LLM JSON 调用带重试 + 输出验证（弱校验，boolean）
- call_llm_json_with_schema_retry: LLM JSON 调用带 Pydantic schema 强校验 + 重试
- call_llm_json_with_schema_retry_with_feedback: 每次重试时把上一次的错误反馈注入 prompt
- RetryExhausted: 重试用尽异常
"""

from __future__ import annotations

import asyncio
import json as _json_mod
import random
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from sciforge.core.exceptions import LLMError, LLMParseError, LLMRateLimitError

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


class RetryExhausted(Exception):  # noqa: N818 — 语义为"重试已用尽"，不强加 Error 后缀
    """达到最大重试次数后仍然失败"""

    def __init__(self, attempts: int, last_exc: BaseException | None) -> None:
        self.attempts = attempts
        self.last_exc = last_exc
        super().__init__(f"retry exhausted after {attempts} attempts: {last_exc!r}")


def _compute_retry_delay(base_delay: float, attempt: int, exc: BaseException) -> float:
    """根据错误类型计算下一次重试等待时间（含抖动）。

    REC-24：加 +0~25% 随机抖动，避免目录并发（max_concurrent_files）下多个失败请求在
    同一时刻齐刷刷重试形成 thundering herd、加剧限流。
    """
    delay = float(base_delay * (2 ** (attempt - 1)))
    msg = str(exc).lower()
    if "429" in msg or "rate limit" in msg or "rate_limit" in msg:
        delay *= 2.0
    return float(delay * (1.0 + random.uniform(0.0, 0.25)))


# ── 429 / 速率限制专用等待策略 ───────────────────────────────

# 遇 429 时的基础等待秒数（若服务端未给 Retry-After）
# minimax 5 小时 4500 次配额场景：建议用较长基线，避免无效快速重试
DEFAULT_RATE_LIMIT_BASE_SLEEP = 300.0  # 5 分钟
DEFAULT_RATE_LIMIT_MAX_SLEEP = 1800.0  # 单次最长 30 分钟
DEFAULT_RATE_LIMIT_TOTAL_BUDGET = 6 * 3600.0  # 同一请求累计最多等 6 小时


async def _sleep_for_rate_limit(
    exc: LLMRateLimitError,
    hit_count: int,
    *,
    base_sleep: float = DEFAULT_RATE_LIMIT_BASE_SLEEP,
    max_sleep: float = DEFAULT_RATE_LIMIT_MAX_SLEEP,
    stage_name: str = "",
) -> float:
    """遇到 429 时长睡等待 quota 恢复。

    策略：
    1. 若异常带 ``retry_after_seconds``（Retry-After header）→ 至少等那么久
    2. 否则按指数退避：base * 2 ** (hit_count - 1)，封顶 max_sleep
    3. 返回实际睡眠秒数
    """
    sleep_s = float(base_sleep * (2 ** max(hit_count - 1, 0)))
    retry_after = exc.retry_after_seconds
    if retry_after is not None and retry_after > sleep_s:
        sleep_s = float(retry_after)
    sleep_s = min(sleep_s, max_sleep)

    logger.warning(
        "llm_rate_limit_sleep",
        stage=stage_name,
        hit_count=hit_count,
        sleep_sec=round(sleep_s, 1),
        retry_after=retry_after,
        reason=str(exc)[:200],
    )
    await asyncio.sleep(sleep_s)
    return sleep_s


async def with_retry(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    on_retry: Callable[[int, BaseException], None] | None = None,
    rate_limit_base_sleep: float = DEFAULT_RATE_LIMIT_BASE_SLEEP,
    rate_limit_max_sleep: float = DEFAULT_RATE_LIMIT_MAX_SLEEP,
    rate_limit_total_budget: float = DEFAULT_RATE_LIMIT_TOTAL_BUDGET,
    **kwargs: Any,
) -> Any:
    """异步重试包装

    429 / LLMRateLimitError 不计入 max_attempts，走长睡策略（同
    ``call_llm_json_with_schema_retry_with_feedback``），等 quota 恢复。

    Args:
        fn: 待调用的协程函数
        max_attempts: 最大尝试次数 (含首次)
        base_delay: 初始等待秒数；第 N 次重试 sleep base_delay * (2 ** (N-1))
        on_retry: 每次重试前的回调 (attempt_index_1based, exception)
        rate_limit_*: 429 长睡参数
    """
    last_exc: BaseException | None = None
    attempt = 0
    rate_limit_hits = 0
    rate_limit_total_slept = 0.0
    while attempt < max_attempts:
        attempt += 1
        try:
            return await fn(*args, **kwargs)
        except LLMRateLimitError as rle:
            # 429：不计 attempt，长睡
            rate_limit_hits += 1
            attempt -= 1
            last_exc = rle
            if on_retry:
                try:
                    on_retry(attempt + 1, rle)
                except Exception:  # noqa: BLE001
                    pass
            slept = await _sleep_for_rate_limit(
                rle,
                rate_limit_hits,
                base_sleep=rate_limit_base_sleep,
                max_sleep=rate_limit_max_sleep,
            )
            rate_limit_total_slept += slept
            if rate_limit_total_slept > rate_limit_total_budget:
                logger.error(
                    "llm_rate_limit_budget_exhausted",
                    total_slept_sec=round(rate_limit_total_slept, 1),
                    budget_sec=round(rate_limit_total_budget, 1),
                    hits=rate_limit_hits,
                )
                raise RetryExhausted(attempt, rle) from rle
            continue
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if on_retry:
                try:
                    on_retry(attempt, exc)
                except Exception:  # noqa: BLE001
                    pass
            if attempt >= max_attempts:
                break
            await asyncio.sleep(_compute_retry_delay(base_delay, attempt, exc))
    raise RetryExhausted(max_attempts, last_exc)


async def call_llm_json_with_retry(
    llm: LLMClient,
    prompt: str,
    *,
    system: str | None = None,
    max_tokens: int | None = None,
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
        kw: dict[str, Any] = {"system": system}
        if max_tokens is not None:
            kw["max_tokens"] = max_tokens
        result = await llm.generate_json(prompt, **kw)
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


def _pretty_validation(ve: ValidationError) -> str:
    """把 Pydantic ValidationError 格式化为可读多行文本（给 LLM 看）"""
    lines: list[str] = []
    for err in ve.errors()[:8]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = err.get("msg", "")
        etype = err.get("type", "")
        lines.append(f"- 字段 {loc} [{etype}]: {msg}")
    return "\n".join(lines) if lines else str(ve)[:500]


async def call_llm_json_with_schema_retry(
    llm: LLMClient,
    prompt: str,
    *,
    model: type[T],
    system: str | None = None,
    max_tokens: int | None = None,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    validation_context: dict[str, Any] | None = None,
) -> T:
    """调用 LLM 的 generate_json 并用 Pydantic 模型强校验 + 重试（兼容旧接口）

    ValidationError 被捕获后作为 ValueError 抛出，触发 with_retry 的指数退避。
    重试耗尽后抛 RetryExhausted，调用方可降级或计入 failed_blocks。
    """

    def _on_retry(i: int, exc: BaseException) -> None:
        logger.info(
            "llm_schema_retry",
            attempt=i,
            max_attempts=max_attempts,
            model=model.__name__,
            error=str(exc)[:300],
        )

    async def _call() -> T:
        kw: dict[str, Any] = {"system": system}
        if max_tokens is not None:
            kw["max_tokens"] = max_tokens
        try:
            result = await llm.generate_json(prompt, **kw)
        except LLMParseError as pe:
            # 把 parse 失败转为 ValueError，触发 with_retry 重试
            raw_head = (pe.raw_response or "")[:200]
            raise ValueError(f"llm output not valid JSON (head={raw_head})") from pe
        if not isinstance(result, dict):
            raise ValueError(f"llm returned non-dict: {type(result).__name__}")
        try:
            return model.model_validate(result, context=validation_context)
        except ValidationError as ve:
            raise ValueError(_pretty_validation(ve)) from ve

    out = await with_retry(
        _call,
        max_attempts=max_attempts,
        base_delay=base_delay,
        on_retry=_on_retry,
    )
    return out  # type: ignore[no-any-return]


async def call_llm_json_with_schema_retry_with_feedback(
    llm: LLMClient,
    prompt_builder: Callable[[str], str],
    *,
    model: type[T],
    system: str | None = None,
    max_tokens: int | None = None,
    max_attempts: int = 3,
    base_delay: float = 2.0,
    validation_context: dict[str, Any] | None = None,
    stage_name: str = "",
    rate_limit_base_sleep: float = DEFAULT_RATE_LIMIT_BASE_SLEEP,
    rate_limit_max_sleep: float = DEFAULT_RATE_LIMIT_MAX_SLEEP,
    rate_limit_total_budget: float = DEFAULT_RATE_LIMIT_TOTAL_BUDGET,
) -> T:
    """带 error feedback 的重试：每次失败将错误可读文本传入 prompt_builder 重建 prompt

    429 / LLMRateLimitError 的处理与其他错误**完全不同**：
    - 不计入 max_attempts（quota 限流是临时性的，不是真的失败）
    - 长睡 rate_limit_base_sleep * 2^(hit_count-1) 或 Retry-After header 秒数
    - 累计睡眠超过 rate_limit_total_budget（默认 6 小时）才放弃，避免无限挂起

    Args:
        prompt_builder: lambda feedback_text: final_prompt
        validation_context: 透传给 model.model_validate 的 context
        stage_name: 调用方 stage 名称（用于指标归因）
        rate_limit_base_sleep: 429 基础睡眠秒数（默认 300s）
        rate_limit_max_sleep: 单次 429 最长睡眠（默认 1800s）
        rate_limit_total_budget: 累计等 429 的总预算（默认 6h）
    """
    last_err_text = ""
    last_exc: BaseException | None = None
    attempt = 0
    rate_limit_hits = 0
    rate_limit_total_slept = 0.0

    while attempt < max_attempts:
        attempt += 1
        prompt = prompt_builder(last_err_text)
        prompt_chars = len(prompt)
        try:
            kw: dict[str, Any] = {"system": system}
            if max_tokens is not None:
                kw["max_tokens"] = max_tokens
            raw = await llm.generate_json(prompt, **kw)
            if not isinstance(raw, dict):
                raise ValueError(f"llm returned non-dict: {type(raw).__name__}")
            result = model.model_validate(raw, context=validation_context)
            # 成功时记录 prompt/output 指标
            output_text = _json_mod.dumps(raw, ensure_ascii=False)
            # REC-08：优先用 backend 上报的真实 token usage（含 cache 命中），回退 chars//4 估算
            usage = getattr(llm, "last_usage", None) or {}
            logger.info(
                "llm_call_metrics",
                stage=stage_name,
                attempt=attempt,
                prompt_chars=prompt_chars,
                prompt_tokens_est=prompt_chars // 4,
                output_chars=len(output_text),
                output_tokens_est=len(output_text) // 4,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
                cache_read_input_tokens=usage.get("cache_read_input_tokens"),
                cache_creation_input_tokens=usage.get("cache_creation_input_tokens"),
                model_schema=model.__name__,
                success=True,
                rate_limit_hits=rate_limit_hits,
                rate_limit_total_slept_sec=round(rate_limit_total_slept, 1),
            )
            return result
        except LLMRateLimitError as rle:
            # 速率限制：不计 attempt，长睡后重试
            rate_limit_hits += 1
            attempt -= 1  # 抵消 attempt 自增，不耗 max_attempts 额度
            last_exc = rle
            slept = await _sleep_for_rate_limit(
                rle,
                rate_limit_hits,
                base_sleep=rate_limit_base_sleep,
                max_sleep=rate_limit_max_sleep,
                stage_name=stage_name,
            )
            rate_limit_total_slept += slept
            if rate_limit_total_slept > rate_limit_total_budget:
                logger.error(
                    "llm_rate_limit_budget_exhausted",
                    stage=stage_name,
                    total_slept_sec=round(rate_limit_total_slept, 1),
                    budget_sec=round(rate_limit_total_budget, 1),
                    hits=rate_limit_hits,
                )
                raise RetryExhausted(attempt, rle) from rle
            continue  # 跳过 _compute_retry_delay，直接开始下轮 attempt
        except ValidationError as ve:
            last_err_text = _pretty_validation(ve)
            last_exc = ve
            logger.info(
                "llm_feedback_retry_validation",
                attempt=attempt,
                max_attempts=max_attempts,
                model=model.__name__,
                error=last_err_text[:300],
            )
        except LLMParseError as pe:
            # LLM 输出不是合法 JSON（常见：先输出 markdown 分析再给 JSON，或完全不给 JSON）
            raw_head = (pe.raw_response or "")[:400]
            last_err_text = (
                "你上一次的输出**不是合法 JSON**，解析失败。"
                "请严格按本 prompt 指定的 schema 直接输出 JSON 对象，**不要**任何前缀/后缀/"
                "markdown 代码块/解释文字（`## 审核` `### Step` 等都是非法的）。"
                "你的上次响应开头片段：\n"
                f"{raw_head}"
            )
            last_exc = pe
            logger.info(
                "llm_feedback_retry_parse",
                attempt=attempt,
                max_attempts=max_attempts,
                model=model.__name__,
                response_head=raw_head[:200],
            )
        except ValueError as ve:
            last_err_text = str(ve)[:500]
            last_exc = ve
            logger.info(
                "llm_feedback_retry_value",
                attempt=attempt,
                max_attempts=max_attempts,
                model=model.__name__,
                error=last_err_text[:300],
            )
        except LLMError as le:
            last_exc = le
            logger.info(
                "llm_feedback_retry_llm",
                attempt=attempt,
                max_attempts=max_attempts,
                model=model.__name__,
                error=str(le)[:300],
            )
        if attempt < max_attempts:
            assert last_exc is not None
            await asyncio.sleep(_compute_retry_delay(base_delay, attempt, last_exc))

    raise RetryExhausted(max_attempts, last_exc)
