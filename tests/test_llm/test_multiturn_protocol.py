"""多轮工具调用必须按 provider 的真实线协议回放。"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sciforge.core.exceptions import ToolCallError
from sciforge.core.models import ToolCall, ToolResult
from sciforge.llm.backends.anthropic import AnthropicBackend
from sciforge.llm.backends.gemini import GeminiBackend
from sciforge.llm.backends.openai_backend import OpenAIBackend
from sciforge.llm.client import LLMClient
from sciforge.llm.config import LLMConfig, LLMProvider
from sciforge.llm.tool_definitions import to_anthropic_format, to_openai_format

_ECHO_TOOL: dict[str, Any] = {
    "name": "echo",
    "description": "回显一个值",
    "input_schema": {
        "type": "object",
        "properties": {"value": {"type": "integer"}},
        "required": ["value"],
    },
}


def _client_with_backend(backend: Any, provider: LLMProvider) -> LLMClient:
    client = object.__new__(LLMClient)
    client.config = LLMConfig(provider=provider, api_key="test")
    client._backend = backend
    client._request_semaphore = None
    client._model_semaphore = None
    return client


def _openai_response(
    calls: list[tuple[str, str, str]],
    *,
    content: str | None = None,
) -> dict[str, Any]:
    tool_calls = [
        SimpleNamespace(
            id=call_id,
            type="function",
            function=SimpleNamespace(name=name, arguments=arguments),
        )
        for call_id, name, arguments in calls
    ]
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason="tool_calls" if calls else "stop",
            )
        ]
    )
    return OpenAIBackend._parse_tool_response(response)


def _anthropic_response(blocks: list[Any], stop_reason: str = "tool_use") -> dict[str, Any]:
    return AnthropicBackend._parse_tool_response(
        SimpleNamespace(content=blocks, stop_reason=stop_reason)
    )


@pytest.mark.asyncio
async def test_openai_replays_assistant_tool_calls_and_parallel_tool_messages() -> None:
    first = _openai_response(
        [
            ("call-1", "echo", '{"value": 1}'),
            ("call-2", "echo", '{"value": 2}'),
        ]
    )
    final = _openai_response([], content="done")
    backend = object.__new__(OpenAIBackend)
    backend.generate_with_tools = AsyncMock(side_effect=[first, final])
    backend.generate_with_tools_sync = MagicMock()
    client = _client_with_backend(backend, LLMProvider.OPENAI)

    barrier = threading.Barrier(2)
    executor_threads: list[str] = []

    def executor(call: ToolCall) -> ToolResult:
        executor_threads.append(threading.current_thread().name)
        barrier.wait(timeout=2)
        return ToolResult(call_id=call.call_id, output=str(call.arguments["value"]))

    history = await client.generate_multiturn(
        [{"role": "user", "content": "echo twice"}],
        tools=[_ECHO_TOOL],
        tool_executor=executor,
    )

    assert [message.role for message in history] == ["assistant", "tool", "assistant"]
    assert [result.output for result in history[1].tool_results] == ["1", "2"]
    assert executor_threads and all(
        name != threading.main_thread().name for name in executor_threads
    )

    second_messages = backend.generate_with_tools.await_args_list[1].kwargs["messages"]
    assert second_messages[1] == first["raw_assistant_message"]
    assert second_messages[1]["tool_calls"][0]["function"]["arguments"] == '{"value": 1}'
    assert second_messages[2:] == [
        {"role": "tool", "tool_call_id": "call-1", "content": "1"},
        {"role": "tool", "tool_call_id": "call-2", "content": "2"},
    ]


@pytest.mark.asyncio
async def test_anthropic_replays_raw_tool_use_blocks_then_user_tool_results() -> None:
    raw_blocks = [
        SimpleNamespace(type="text", text="checking"),
        SimpleNamespace(type="tool_use", id="toolu-1", name="echo", input={"value": 7}),
    ]
    first = _anthropic_response(raw_blocks)
    final = _anthropic_response([SimpleNamespace(type="text", text="done")], stop_reason="end_turn")
    backend = object.__new__(AnthropicBackend)
    backend.generate_with_tools = AsyncMock(side_effect=[first, final])
    backend.generate_with_tools_sync = MagicMock()
    client = _client_with_backend(backend, LLMProvider.ANTHROPIC)

    history = await client.generate_multiturn(
        [{"role": "user", "content": "echo"}],
        tools=[_ECHO_TOOL],
        tool_executor=lambda call: ToolResult(call_id=call.call_id, output="7"),
    )

    assert history[-1].content == "done"
    second_messages = backend.generate_with_tools.await_args_list[1].kwargs["messages"]
    assert second_messages[1]["role"] == "assistant"
    assert second_messages[1]["content"] is raw_blocks
    assert second_messages[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu-1",
                "content": "7",
                "is_error": False,
            }
        ],
    }


@pytest.mark.asyncio
async def test_unknown_tool_and_malformed_arguments_are_returned_without_execution() -> None:
    first = _openai_response(
        [
            ("call-unknown", "delete_everything", "{}"),
            ("call-malformed", "echo", "{not-json"),
        ]
    )
    final = _openai_response([], content="corrected")
    backend = object.__new__(OpenAIBackend)
    backend.generate_with_tools = AsyncMock(side_effect=[first, final])
    backend.generate_with_tools_sync = MagicMock()
    client = _client_with_backend(backend, LLMProvider.OPENAI)
    executor = MagicMock()

    history = await client.generate_multiturn(
        [{"role": "user", "content": "test"}],
        tools=[_ECHO_TOOL],
        tool_executor=executor,
    )

    executor.assert_not_called()
    results = history[1].tool_results
    assert [result.call_id for result in results] == ["call-unknown", "call-malformed"]
    assert all(result.is_error for result in results)
    assert "未知工具" in results[0].output
    assert "工具参数无效" in results[1].output

    second_messages = backend.generate_with_tools.await_args_list[1].kwargs["messages"]
    # 即使 arguments 畸形，也必须原样保留 assistant.tool_calls 才能配对错误结果。
    assert second_messages[1]["tool_calls"][1]["function"]["arguments"] == "{not-json"
    assert [message["tool_call_id"] for message in second_messages[2:]] == [
        "call-unknown",
        "call-malformed",
    ]


@pytest.mark.asyncio
async def test_max_turns_is_hard_bound_even_when_model_keeps_calling_tools() -> None:
    responses = [
        _openai_response([("call-1", "echo", '{"value": 1}')]),
        _openai_response([("call-2", "echo", '{"value": 2}')]),
    ]
    backend = object.__new__(OpenAIBackend)
    backend.generate_with_tools = AsyncMock(side_effect=responses)
    backend.generate_with_tools_sync = MagicMock()
    client = _client_with_backend(backend, LLMProvider.OPENAI)

    history = await client.generate_multiturn(
        [{"role": "user", "content": "keep going"}],
        tools=[_ECHO_TOOL],
        tool_executor=lambda call: ToolResult(call_id=call.call_id, output="ok"),
        max_turns=2,
    )

    assert backend.generate_with_tools.await_count == 2
    assert [message.role for message in history] == ["assistant", "tool", "assistant", "tool"]
    assert history[-1].tool_results[0].call_id == "call-2"


@pytest.mark.asyncio
async def test_missing_tool_call_id_is_rejected_before_execution() -> None:
    backend = object.__new__(OpenAIBackend)
    backend.generate_with_tools = AsyncMock(
        return_value={
            "content": "",
            "tool_calls": [{"id": "", "name": "echo", "arguments": {"value": 1}}],
            "stop_reason": "tool_use",
        }
    )
    backend.generate_with_tools_sync = MagicMock()
    client = _client_with_backend(backend, LLMProvider.OPENAI)

    with pytest.raises(ToolCallError, match="缺少有效 id"):
        await client.generate_multiturn(
            [{"role": "user", "content": "echo"}],
            tools=[_ECHO_TOOL],
            tool_executor=MagicMock(),
        )


@pytest.mark.asyncio
async def test_gemini_explicitly_rejects_lossy_multiturn_tool_protocol() -> None:
    backend = object.__new__(GeminiBackend)
    client = _client_with_backend(backend, LLMProvider.GEMINI)

    assert client.supports_tools is False
    with pytest.raises(ToolCallError, match="thought_signature"):
        await client.generate_multiturn([{"role": "user", "content": "echo"}], tools=[_ECHO_TOOL])


def test_sync_openai_multiturn_keeps_existing_api_and_wire_protocol() -> None:
    first = _openai_response([("call-1", "echo", '{"value": 3}')])
    final = _openai_response([], content="done")
    backend = object.__new__(OpenAIBackend)
    backend.generate_with_tools = AsyncMock()
    backend.generate_with_tools_sync = MagicMock(side_effect=[first, final])
    client = _client_with_backend(backend, LLMProvider.OPENAI)

    history = client.generate_multiturn_sync(
        [{"role": "user", "content": "echo"}],
        tools=[_ECHO_TOOL],
        tool_executor=lambda call: ToolResult(call_id=call.call_id, output="3"),
    )

    assert history[-1].content == "done"
    second_messages = backend.generate_with_tools_sync.call_args_list[1].kwargs["messages"]
    assert second_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "3",
    }


def test_tool_schema_aliases_are_normalized_for_both_provider_families() -> None:
    parameter_style = {
        "name": "echo",
        "description": "echo",
        "parameters": {"type": "object", "properties": {"value": {"type": "integer"}}},
    }

    anthropic_tool = to_anthropic_format([parameter_style])[0]
    openai_tool = to_openai_format([parameter_style])[0]
    assert anthropic_tool["input_schema"] == parameter_style["parameters"]
    assert "parameters" not in anthropic_tool
    assert openai_tool["function"]["parameters"] == parameter_style["parameters"]
