"""WP-44 风险触发 Evaluator 多轮——provider 约束(C8) + 触发判定 + 工具路由。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sciforge.core.models import ConversationMessage, ToolCall, ToolResult
from sciforge.llm.config import LLMProvider
from sciforge.pipeline.extract_evaluator import (
    _tool_executor,
    build_review_prompt,
    run_evaluator,
    should_trigger_evaluator,
)


class _FakeConfig:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider


class _FakeLLM:
    def __init__(
        self, provider: LLMProvider, history: list[ConversationMessage] | None = None
    ) -> None:
        self.config = _FakeConfig(provider)
        self.generate_multiturn = AsyncMock(return_value=history or [])


# ── should_trigger_evaluator（I.6）────────────────────────


def test_below_threshold_not_triggered() -> None:
    assert should_trigger_evaluator(0.1, threshold=0.3) is False


def test_at_or_above_threshold_triggered() -> None:
    assert should_trigger_evaluator(0.3, threshold=0.3) is True
    assert should_trigger_evaluator(0.9, threshold=0.3) is True


def test_zero_risk_never_triggered() -> None:
    assert should_trigger_evaluator(0.0) is False


# ── build_review_prompt ───────────────────────────────────


def test_review_prompt_contains_window_and_summary() -> None:
    prompt = build_review_prompt("定理 3.1 内容。", "已提取: 定理3.1")
    assert "定理 3.1 内容。" in prompt
    assert "已提取: 定理3.1" in prompt


# ── provider 约束（C8）──────────────────────────────────


@pytest.mark.asyncio
async def test_openai_skips_without_calling_multiturn() -> None:
    """OpenAI 不支持 multiturn（C8）→ ran=False，且不消耗任何 LLM 调用。"""
    llm = _FakeLLM(LLMProvider.OPENAI)
    verdict = await run_evaluator("审核请求", llm=llm)
    assert verdict.ran is False
    assert "不支持" in verdict.skip_reason
    llm.generate_multiturn.assert_not_called()


@pytest.mark.asyncio
async def test_gemini_skips_without_calling_multiturn() -> None:
    llm = _FakeLLM(LLMProvider.GEMINI)
    verdict = await run_evaluator("审核请求", llm=llm)
    assert verdict.ran is False
    llm.generate_multiturn.assert_not_called()


@pytest.mark.asyncio
async def test_anthropic_runs_multiturn() -> None:
    history = [
        ConversationMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(tool_name="find_item_markers", arguments={})],
        ),
        ConversationMessage(role="tool", tool_results=[ToolResult(call_id="1", output="{}")]),
        ConversationMessage(role="assistant", content="审核通过，未发现问题"),
    ]
    llm = _FakeLLM(LLMProvider.ANTHROPIC, history)
    verdict = await run_evaluator("审核请求", llm=llm, max_turns=3)
    assert verdict.ran is True
    llm.generate_multiturn.assert_called_once()
    call_kwargs = llm.generate_multiturn.call_args.kwargs
    assert call_kwargs["max_turns"] == 3
    assert call_kwargs["tool_executor"] is not None


@pytest.mark.asyncio
async def test_anthropic_compatible_also_runs() -> None:
    llm = _FakeLLM(
        LLMProvider.ANTHROPIC_COMPATIBLE, [ConversationMessage(role="assistant", content="ok")]
    )
    verdict = await run_evaluator("审核请求", llm=llm)
    assert verdict.ran is True


# ── verdict 摘要提取 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_verdict_summary_from_last_assistant_message() -> None:
    history = [
        ConversationMessage(role="assistant", content="第一轮想法"),
        ConversationMessage(role="assistant", content="最终结论：审核通过"),
    ]
    llm = _FakeLLM(LLMProvider.ANTHROPIC, history)
    verdict = await run_evaluator("审核请求", llm=llm)
    assert verdict.summary == "最终结论：审核通过"
    assert verdict.turns_used == 2


@pytest.mark.asyncio
async def test_verdict_counts_tool_calls() -> None:
    history = [
        ConversationMessage(
            role="assistant",
            tool_calls=[
                ToolCall(tool_name="find_item_markers", arguments={}),
                ToolCall(tool_name="check_pairing", arguments={}),
            ],
        ),
        ConversationMessage(role="tool", tool_results=[]),
        ConversationMessage(role="assistant", content="完成"),
    ]
    llm = _FakeLLM(LLMProvider.ANTHROPIC, history)
    verdict = await run_evaluator("审核请求", llm=llm)
    assert verdict.tool_calls_made == 2


# ── _tool_executor 路由 ───────────────────────────────────


def test_tool_executor_routes_to_dispatcher() -> None:
    call = ToolCall(
        tool_name="find_item_markers",
        arguments={"text": "定理 3.1 内容。", "line_base": 1},
        call_id="c1",
    )
    result = _tool_executor(call)
    assert result.is_error is False
    assert "theorem" in result.output


def test_tool_executor_unknown_tool_returns_error() -> None:
    call = ToolCall(tool_name="not_a_tool", arguments={}, call_id="c1")
    result = _tool_executor(call)
    assert result.is_error is True
    assert result.call_id == "c1"
