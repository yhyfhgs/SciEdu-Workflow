"""BaseAgent 抽象类单元测试"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from sciforge.agents import BaseAgent
from sciforge.core.models import Discipline


class _DummyIn(BaseModel):
    value: int


class _DummyOut(BaseModel):
    doubled: int


class _DummyAgent(BaseAgent[_DummyIn, _DummyOut]):
    name = "dummy"
    prompt_file = ""  # 不加载 prompt

    async def run(self, input: _DummyIn) -> _DummyOut:
        self._log_start(value=input.value)
        result = _DummyOut(doubled=input.value * 2)
        self._log_end(doubled=result.doubled)
        return result


def test_cannot_instantiate_without_name():
    """没设置 `name` 类属性的子类应在 __init__ 中抛错"""

    class _NoName(BaseAgent[_DummyIn, _DummyOut]):
        async def run(self, input: _DummyIn) -> _DummyOut:
            return _DummyOut(doubled=0)

    with pytest.raises(ValueError, match="name"):
        _NoName(llm=MagicMock())


def test_dummy_agent_default_math():
    agent = _DummyAgent(llm=MagicMock())
    assert agent.name == "dummy"
    assert agent.discipline == Discipline.MATH
    assert agent.discipline_spec.display_name == "数学"


def test_dummy_agent_physics():
    agent = _DummyAgent(llm=MagicMock(), discipline=Discipline.PHYSICS)
    assert agent.discipline == Discipline.PHYSICS
    assert agent.discipline_spec.display_name == "物理"


async def test_dummy_agent_run():
    agent = _DummyAgent(llm=MagicMock())
    out = await agent.run(_DummyIn(value=5))
    assert isinstance(out, _DummyOut)
    assert out.doubled == 10


def test_empty_prompt_file_returns_empty_string():
    agent = _DummyAgent(llm=MagicMock())
    assert agent._prompt_template == ""


class _WithPromptAgent(BaseAgent[_DummyIn, _DummyOut]):
    name = "with_prompt"
    prompt_file = "pipeline/extract_stage1_initial.txt"  # 真实 prompt 文件

    async def run(self, input: _DummyIn) -> _DummyOut:
        return _DummyOut(doubled=input.value * 2)


def test_prompt_file_loaded():
    """设置 prompt_file 后，_load_prompt 应加载实际文件"""
    agent = _WithPromptAgent(llm=MagicMock())
    assert agent._prompt_template  # 非空
    # 默认数学，应含数学专家字样
    assert len(agent._prompt_template) > 100


async def test_call_llm_json_uses_retry():
    """_call_llm_json 应调用 pipeline.retry.call_llm_json_with_retry"""
    agent = _DummyAgent(llm=MagicMock())
    # Mock LLMClient 的 generate_json 接口
    agent.llm = MagicMock()
    agent.llm.generate_json = AsyncMock(return_value={"ok": True})

    result = await agent._call_llm_json(prompt="test prompt", max_attempts=1)
    assert result == {"ok": True}
    agent.llm.generate_json.assert_called_once()
