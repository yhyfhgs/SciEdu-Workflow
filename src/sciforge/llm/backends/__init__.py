"""LLM 后端协议与实现"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):
    """LLM 后端协议，每个提供者实现此接口"""

    async def generate(self, prompt: str, system: str, **kwargs: Any) -> str: ...
    def generate_sync(self, prompt: str, system: str, **kwargs: Any) -> str: ...


@runtime_checkable
class ToolCallingBackend(Protocol):
    """支持 tool calling 的 LLM 后端协议

    与 LLMBackend 并列，后端可同时实现两个协议。
    使用 messages 列表而非单一 prompt，支持多轮对话。
    """

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
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
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """同步 tool calling，返回格式同上"""
        ...


@runtime_checkable
class VisionBackend(Protocol):
    """支持多模态图像输入的 LLM 后端协议

    与 LLMBackend 并列，后端可同时实现两个协议。
    接收原始图像 bytes，各后端内部处理 base64 编码。
    """

    async def generate_vision(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        """异步多模态生成：图像 + 文本 → 文本"""
        ...

    def generate_vision_sync(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        """同步多模态生成"""
        ...
