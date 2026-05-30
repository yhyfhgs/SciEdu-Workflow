"""ReorderBuffer — 乱序进入、有序释放、gap 跳过"""

from __future__ import annotations

import asyncio

import pytest

from sciforge.pipeline.reorder_buffer import ReorderBuffer


async def _drain(q: asyncio.Queue[object]) -> list[object]:
    out: list[object] = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


@pytest.mark.asyncio
async def test_in_order_passthrough() -> None:
    q: asyncio.Queue[object] = asyncio.Queue()
    buf = ReorderBuffer(q, start_id=0)
    await buf.put(0, "a")
    await buf.put(1, "b")
    await buf.put(2, "c")
    assert await _drain(q) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_out_of_order_restored() -> None:
    q: asyncio.Queue[object] = asyncio.Queue()
    buf = ReorderBuffer(q, start_id=0)
    # 乱序：先放 id=2、id=0、id=1
    await buf.put(2, "c")
    assert q.qsize() == 0  # 不能释放（next_id=0）
    await buf.put(0, "a")
    assert q.qsize() == 1  # 释放 "a"
    await buf.put(1, "b")
    # 此刻应连续释放 b（next_id=1→2）、c（next_id=2→3）
    assert await _drain(q) == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_skip_allows_advance() -> None:
    """Stage1Review rejected 的 id 调 skip，不能阻塞下游"""
    q: asyncio.Queue[object] = asyncio.Queue()
    buf = ReorderBuffer(q, start_id=0)

    await buf.put(0, "a")
    await buf.skip(1)  # 标记 id=1 跳过
    await buf.put(2, "c")

    assert await _drain(q) == ["a", "c"]


@pytest.mark.asyncio
async def test_skip_before_put_then_fill_gap() -> None:
    """skip 若早于 put 到达时同样生效"""
    q: asyncio.Queue[object] = asyncio.Queue()
    buf = ReorderBuffer(q, start_id=0)

    await buf.skip(1)
    await buf.put(0, "a")
    # 此时 next_id=0 → 释放 a，skipped 中已有 1 → next_id 推到 2
    assert await _drain(q) == ["a"]

    await buf.put(2, "c")
    assert await _drain(q) == ["c"]


@pytest.mark.asyncio
async def test_close_flushes_residuals() -> None:
    """close 时任何 pending 按 id 升序冲刷"""
    q: asyncio.Queue[object] = asyncio.Queue()
    buf = ReorderBuffer(q, start_id=0)
    await buf.put(5, "e")
    await buf.put(3, "c")
    # next_id=0 没来 → pending 中有 3, 5
    await buf.close()
    assert await _drain(q) == ["c", "e"]


@pytest.mark.asyncio
async def test_concurrent_workers_preserve_order() -> None:
    """N worker 并发 put 后下游 queue 严格按 id 递增"""
    q: asyncio.Queue[object] = asyncio.Queue()
    buf = ReorderBuffer(q, start_id=0)

    async def worker(item_id: int) -> None:
        await asyncio.sleep((50 - item_id) * 0.0001)  # 反向延迟：id 越大越早到
        await buf.put(item_id, item_id)

    await asyncio.gather(*[worker(i) for i in range(50)])

    result = await _drain(q)
    assert result == list(range(50))
