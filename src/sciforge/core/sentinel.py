"""Pipeline 5 阶段共享的 SENTINEL 单例，供 asyncio.Queue 通知流结束"""

from __future__ import annotations


class _Sentinel:
    """Producer 通知 Consumer 流结束的单例类型"""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<SENTINEL>"


SENTINEL = _Sentinel()

__all__ = ["SENTINEL", "_Sentinel"]
