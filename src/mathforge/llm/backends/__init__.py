"""LLM 后端协议与实现"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):
    """LLM 后端协议，每个提供者实现此接口"""

    async def generate(self, prompt: str, system: str, **kwargs) -> str: ...
    def generate_sync(self, prompt: str, system: str, **kwargs) -> str: ...


@runtime_checkable
class ToolCallingBackend(Protocol):
    """支持 tool calling 的 LLM 后端协议

    与 LLMBackend 并列，后端可同时实现两个协议。
    使用 messages 列表而非单一 prompt，支持多轮对话。
    """

    async def generate_with_tools(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        **kwargs,
    ) -> dict:
        """异步 tool calling

        Returns:
            {
                "content": str,           # 文本响应
                "tool_calls": [           # 工具调用列表（可能为空）
                    {"id": str, "name": str, "arguments": dict}
                ],
                "stop_reason": str,       # "end_turn" | "tool_use"
            }
        """
        ...

    def generate_with_tools_sync(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict],
        **kwargs,
    ) -> dict:
        """同步 tool calling，返回格式同上"""
        ...
