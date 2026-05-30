"""429 / LLMRateLimitError 处理测试

覆盖：
1. _raise_mapped 把 status_code=429 / 含 'rate limit' 的异常映射为 LLMRateLimitError
2. call_llm_json_with_schema_retry_with_feedback 遇 429 不计 attempt 且长睡
3. with_retry 遇 429 不计 attempt 且长睡
4. 累计等待超 budget 时 raise RetryExhausted
5. is_state_complete 把 failed_blocks 纳入判定
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from sciforge.core.exceptions import (
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from sciforge.core.models import FileExtractionState
from sciforge.llm.backends.base import BaseBackend
from sciforge.pipeline.resume_state import is_state_complete
from sciforge.pipeline.retry import (
    RetryExhausted,
    call_llm_json_with_schema_retry_with_feedback,
    with_retry,
)

# ── _raise_mapped ─────────────────────────────────────────


class _FakeStatusError(Exception):
    def __init__(self, msg: str, status_code: int | None = None, retry_after: str | None = None):
        super().__init__(msg)
        if status_code is not None:
            self.status_code = status_code  # type: ignore[attr-defined]
        if retry_after is not None:
            self.response = type("R", (), {"headers": {"retry-after": retry_after}})()  # type: ignore[attr-defined]


def test_raise_mapped_429_status_code_to_rate_limit() -> None:
    exc = _FakeStatusError("too many requests", status_code=429)
    with pytest.raises(LLMRateLimitError):
        BaseBackend._raise_mapped(exc)


def test_raise_mapped_parses_retry_after_header() -> None:
    exc = _FakeStatusError("too many requests", status_code=429, retry_after="60")
    try:
        BaseBackend._raise_mapped(exc)
    except LLMRateLimitError as rle:
        assert rle.retry_after_seconds == 60.0
    else:
        pytest.fail("expected LLMRateLimitError")


def test_raise_mapped_quota_keyword_to_rate_limit() -> None:
    exc = Exception("insufficient_quota: token quota exceeded")
    with pytest.raises(LLMRateLimitError):
        BaseBackend._raise_mapped(exc)


def test_raise_mapped_rate_limit_keyword() -> None:
    exc = Exception("Error: rate_limit_exceeded, retry later")
    with pytest.raises(LLMRateLimitError):
        BaseBackend._raise_mapped(exc)


def test_raise_mapped_timeout_still_timeout() -> None:
    exc = Exception("Request timed out after 120s")
    with pytest.raises(LLMTimeoutError):
        BaseBackend._raise_mapped(exc)


def test_raise_mapped_generic_to_llm_error() -> None:
    exc = Exception("Authentication failed")
    with pytest.raises(LLMError) as ei:
        BaseBackend._raise_mapped(exc)
    # 不能是 subtype
    assert type(ei.value) is LLMError


# ── retry 行为 ────────────────────────────────────────────


class _DummyModel(BaseModel):
    value: int


class _ScriptedLLM:
    """按预设序列抛异常 / 返回 JSON 的 fake LLM"""

    def __init__(self, plan: list[Any]) -> None:
        self.plan = list(plan)
        self.calls = 0

    async def generate_json(self, prompt: str, **kw: Any) -> Any:
        self.calls += 1
        if not self.plan:
            raise RuntimeError("plan exhausted")
        item = self.plan.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.mark.asyncio
async def test_rate_limit_does_not_count_as_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 异常被长睡 + 继续重试，不消耗 max_attempts 额度"""
    # monkeypatch asyncio.sleep 避免真睡
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("sciforge.pipeline.retry.asyncio.sleep", fake_sleep)

    # 前 3 次 429，第 4 次成功
    llm = _ScriptedLLM([
        LLMRateLimitError("hit 1"),
        LLMRateLimitError("hit 2"),
        LLMRateLimitError("hit 3"),
        {"value": 42},
    ])

    result = await call_llm_json_with_schema_retry_with_feedback(
        llm,  # type: ignore[arg-type]
        prompt_builder=lambda _fb: "dummy prompt",
        model=_DummyModel,
        max_attempts=2,  # 只允许 2 次 attempts，但 429 不计入
        base_delay=0.01,
        rate_limit_base_sleep=1.0,
        rate_limit_max_sleep=10.0,
        rate_limit_total_budget=60.0,
    )

    assert result.value == 42
    assert llm.calls == 4  # 尽管 max_attempts=2，4 次都执行（前 3 次 429 不计）
    # 3 次 429 → 3 次长睡
    assert len(sleeps) == 3
    # 指数退避：1, 2, 4
    assert sleeps[0] == pytest.approx(1.0)
    assert sleeps[1] == pytest.approx(2.0)
    assert sleeps[2] == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_rate_limit_respects_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry-After header 值应被优先使用（若大于 base）"""
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("sciforge.pipeline.retry.asyncio.sleep", fake_sleep)

    llm = _ScriptedLLM([
        LLMRateLimitError("hit", retry_after_seconds=120.0),
        {"value": 1},
    ])

    result = await call_llm_json_with_schema_retry_with_feedback(
        llm,  # type: ignore[arg-type]
        prompt_builder=lambda _fb: "p",
        model=_DummyModel,
        max_attempts=2,
        rate_limit_base_sleep=1.0,  # base 只 1s
        rate_limit_max_sleep=600.0,
        rate_limit_total_budget=3600.0,
    )

    assert result.value == 1
    # 应睡 retry_after (120s) 而非 base (1s)
    assert sleeps[0] == pytest.approx(120.0)


@pytest.mark.asyncio
async def test_rate_limit_max_sleep_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    """单次睡眠不应超过 max_sleep"""
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("sciforge.pipeline.retry.asyncio.sleep", fake_sleep)

    llm = _ScriptedLLM([
        LLMRateLimitError("hit", retry_after_seconds=99999.0),  # 妄图睡大
        {"value": 1},
    ])

    await call_llm_json_with_schema_retry_with_feedback(
        llm,  # type: ignore[arg-type]
        prompt_builder=lambda _fb: "p",
        model=_DummyModel,
        max_attempts=1,
        rate_limit_base_sleep=1.0,
        rate_limit_max_sleep=30.0,  # 封顶 30s
        rate_limit_total_budget=60.0,
    )
    assert sleeps[0] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_rate_limit_budget_exhausted_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """累计睡眠超预算时 raise RetryExhausted"""

    async def fake_sleep(_s: float) -> None:
        pass

    monkeypatch.setattr("sciforge.pipeline.retry.asyncio.sleep", fake_sleep)

    # 连续 429，base_sleep 大，预算小 → 很快耗尽
    llm = _ScriptedLLM([LLMRateLimitError(f"hit {i}") for i in range(20)])

    with pytest.raises(RetryExhausted):
        await call_llm_json_with_schema_retry_with_feedback(
            llm,  # type: ignore[arg-type]
            prompt_builder=lambda _fb: "p",
            model=_DummyModel,
            max_attempts=100,
            rate_limit_base_sleep=10.0,
            rate_limit_max_sleep=100.0,
            rate_limit_total_budget=50.0,  # 只容 ~3 次
        )


@pytest.mark.asyncio
async def test_with_retry_handles_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """with_retry（VLM 路径）遇 429 也要长睡 + 不计 attempt"""
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    monkeypatch.setattr("sciforge.pipeline.retry.asyncio.sleep", fake_sleep)

    events: list[str] = []

    async def flaky() -> str:
        events.append("call")
        if len(events) < 3:
            raise LLMRateLimitError("hit")
        return "ok"

    out = await with_retry(
        flaky,
        max_attempts=2,  # 只 2 个 attempt，但 429 不计
        base_delay=0.01,
        rate_limit_base_sleep=1.0,
        rate_limit_max_sleep=10.0,
        rate_limit_total_budget=30.0,
    )
    assert out == "ok"
    assert events.count("call") == 3
    assert len(sleeps) == 2  # 2 次 429 → 2 次长睡


# ── is_state_complete 测试 ────────────────────────────────


def test_is_state_complete_rejects_failed_blocks() -> None:
    """failed_blocks 非空 → 不算 complete，保证下次 resume 重跑"""
    state = FileExtractionState(
        file_path="x.pdf",
        file_hash="abc",
        task0_completed=True,
        stage1_completed=True,
        stage3_completed=True,
        failed_batches=[],
        failed_blocks=[3, 5, 7],  # 有失败块
    )
    assert is_state_complete(state) is False


def test_is_state_complete_rejects_failed_batches() -> None:
    """failed_batches 非空 → 不算 complete"""
    state = FileExtractionState(
        file_path="x.pdf",
        file_hash="abc",
        task0_completed=True,
        stage1_completed=True,
        stage3_completed=True,
        failed_batches=[(0, 1, 5)],
        failed_blocks=[],
    )
    assert is_state_complete(state) is False


def test_is_state_complete_rejects_phase_completed_if_failed() -> None:
    """即使 phase='completed' 标记，有 failed 也降级为未完成"""
    state = FileExtractionState(
        file_path="x.pdf",
        file_hash="abc",
        phase="completed",
        task0_completed=True,
        stage1_completed=True,
        stage3_completed=True,
        failed_blocks=[1],
    )
    assert is_state_complete(state) is False


def test_is_state_complete_true_when_clean() -> None:
    state = FileExtractionState(
        file_path="x.pdf",
        file_hash="abc",
        task0_completed=True,
        stage1_completed=True,
        stage3_completed=True,
        failed_batches=[],
        failed_blocks=[],
    )
    assert is_state_complete(state) is True


def test_is_state_complete_false_for_none() -> None:
    assert is_state_complete(None) is False
