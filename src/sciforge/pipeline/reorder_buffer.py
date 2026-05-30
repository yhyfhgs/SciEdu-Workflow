"""ReorderBuffer — 并发消费后按 item_id 顺序向下游队列释放

Stage2 各 item 之间无状态依赖，可以 N-worker 并发处理，但 Stage3Persist
要求按 item_id 严格递增入库。并发 worker 处理完后把结果交给 ReorderBuffer，
它维护一个 next_id 游标，按顺序把对应 item 放入下游队列；其余 item 暂存
pending dict，等到它们的 id 轮到时再一次性释放多个。

使用模式：
    buf = ReorderBuffer(q_out, start_id=0)
    # N 个 worker 并发：
    await buf.put(item_id, payload)     # 乱序进入
    # ...
    await buf.close()                    # 所有 worker 完成后调用
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()


class ReorderBuffer:
    """按 item_id 顺序向下游队列释放项目。

    - 支持 N 个 worker 并发 `put()`
    - 内部维护一个 `_next_id` 游标
    - 当 `_next_id` 对应的 item 到达时，连续释放所有可连续的 pending 项
    - 被 Stage1Review 拒绝的 id（gap）通过 `skip(item_id)` 明确跳过，否则会永远阻塞
    """

    def __init__(self, q_out: asyncio.Queue[Any], start_id: int = 0) -> None:
        self._q_out = q_out
        self._next_id = start_id
        self._pending: dict[int, Any] = {}
        self._skipped: set[int] = set()
        self._lock = asyncio.Lock()

    async def put(self, item_id: int, item: Any) -> None:
        """放入一个 (item_id, item) 对；按顺序释放所有可连续的项。"""
        async with self._lock:
            self._pending[item_id] = item
            await self._flush()

    async def skip(self, item_id: int) -> None:
        """标记某个 id 已被上游跳过（Stage1Review rejected、force-consumed 等）。

        允许 `_next_id` 跨过这个 id 继续释放后续项。
        """
        async with self._lock:
            self._skipped.add(item_id)
            await self._flush()

    async def close(self) -> None:
        """所有 worker 完成后调用；flush 剩余 pending 并冲刷空洞（看作 skip）。

        注意：close 后任何残留 pending 都被视为异常，log warning 并按顺序释放。
        """
        async with self._lock:
            if not self._pending:
                return
            # 残留：按 id 升序全部释放（容错，避免永远阻塞）
            logger.warning(
                "reorder_buffer_residual_flush",
                residual_count=len(self._pending),
                next_id=self._next_id,
                min_id=min(self._pending.keys()),
                max_id=max(self._pending.keys()),
            )
            for item_id in sorted(self._pending.keys()):
                await self._q_out.put(self._pending.pop(item_id))
                self._next_id = item_id + 1

    async def _flush(self) -> None:
        """内部方法：释放所有从 _next_id 开始连续可用的项。"""
        while True:
            if self._next_id in self._pending:
                await self._q_out.put(self._pending.pop(self._next_id))
                self._skipped.discard(self._next_id)
                self._next_id += 1
            elif self._next_id in self._skipped:
                self._skipped.discard(self._next_id)
                self._next_id += 1
            else:
                break
