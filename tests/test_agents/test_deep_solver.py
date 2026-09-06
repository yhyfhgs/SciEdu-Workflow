"""DeepSolveEngine — solve-deep 多轮推理 + 降级路径测试

mock generate_multiturn / generate_json 验证 DeepSolveEngine 的编排与结果解析，
不真调 LLM、不依赖真实 tool schema。
"""

from __future__ import annotations

from typing import Any

import pytest

from sciforge.agents.qgen.deep_solver import DeepSolveEngine, _extract_json
from sciforge.core.models import (
    ConversationMessage,
    Discipline,
    Question,
    ToolCall,
)


class _ToolLLM:
    """支持 tools 的 mock：generate_multiturn 直接返回预设对话历史"""

    supports_tools = True

    def __init__(self, history: list[ConversationMessage]) -> None:
        self._history = history

    async def generate_multiturn(self, **kwargs: Any) -> list[ConversationMessage]:
        return self._history

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


class _NoToolLLM:
    """不支持 tools 的 mock：走单轮降级（generate_json）"""

    supports_tools = False

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    async def generate_json(self, prompt: str, **kw: Any) -> Any:
        return self._response

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


def _q() -> Question:
    return Question(discipline=Discipline.MATH, content="计算 $\\int_0^1 x\\,dx$。")


@pytest.mark.asyncio
async def test_solve_deep_multiturn_parses_final_json() -> None:
    history = [
        ConversationMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(tool_name="execute_python", arguments={"code": "print(1/2)"})],
        ),
        ConversationMessage(
            role="assistant",
            content=(
                '{"solution_latex":"积分值为 1/2，详细推导略。",'
                '"confidence":"high","methods_used":["定积分"],'
                '"step_count":3,"self_check":"代入端点复核通过"}'
            ),
        ),
    ]
    engine = DeepSolveEngine(_ToolLLM(history), Discipline.MATH)  # type: ignore[arg-type]
    sol = await engine.solve_deep(_q())
    assert sol is not None
    assert sol.confidence == "high"
    assert "1/2" in sol.solution_latex
    assert sol.methods_used == ["定积分"]
    assert sol.step_count == 3
    assert sol.self_check == "代入端点复核通过"


@pytest.mark.asyncio
async def test_solve_deep_extracts_json_from_prose() -> None:
    history = [
        ConversationMessage(
            role="assistant",
            content='经过多步推理，结论如下：\n{"solution_latex":"sol","confidence":"low"}\n完毕。',
        ),
    ]
    engine = DeepSolveEngine(_ToolLLM(history), Discipline.MATH)  # type: ignore[arg-type]
    sol = await engine.solve_deep(_q())
    assert sol is not None
    assert sol.confidence == "low"


@pytest.mark.asyncio
async def test_solve_deep_empty_returns_none() -> None:
    history = [ConversationMessage(role="assistant", content="")]
    engine = DeepSolveEngine(_ToolLLM(history), Discipline.MATH)  # type: ignore[arg-type]
    sol = await engine.solve_deep(_q())
    assert sol is None


@pytest.mark.asyncio
async def test_solve_deep_does_not_publish_unstructured_tool_narration() -> None:
    history = [
        ConversationMessage(
            role="assistant",
            content="我先尝试若干内部计算步骤，但没有按约定给出结构化最终答案。",
        ),
    ]
    engine = DeepSolveEngine(_ToolLLM(history), Discipline.MATH)  # type: ignore[arg-type]

    assert await engine.solve_deep(_q()) is None


@pytest.mark.asyncio
async def test_solve_deep_no_tools_fallback() -> None:
    llm = _NoToolLLM({"solution_latex": "降级单轮深度解答。", "confidence": "medium"})
    engine = DeepSolveEngine(llm, Discipline.MATH)  # type: ignore[arg-type]
    sol = await engine.solve_deep(_q())
    assert sol is not None
    assert sol.solution_latex == "降级单轮深度解答。"
    assert sol.confidence == "medium"


def test_extract_json_helper() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}
    assert _extract_json('prefix {"a": 2} suffix') == {"a": 2}
    assert _extract_json("no json here") == {}
    assert _extract_json("") == {}
    assert _extract_json("[1, 2, 3]") == {}  # 非 dict 顶层 → 空
