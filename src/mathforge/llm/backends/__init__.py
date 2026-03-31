"""LLM 后端协议与实现"""

from __future__ import annotations

from typing import Protocol


class LLMBackend(Protocol):
    """LLM 后端协议，每个提供者实现此接口"""

    async def generate(self, prompt: str, system: str, **kwargs) -> str: ...
    def generate_sync(self, prompt: str, system: str, **kwargs) -> str: ...
