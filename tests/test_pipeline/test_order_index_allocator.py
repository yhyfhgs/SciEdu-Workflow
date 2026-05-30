"""OrderIndexAllocator — 并发分配正确性测试"""

from __future__ import annotations

import asyncio

import pytest

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline
from sciforge.pipeline.orchestrator import OrderIndexAllocator
from sciforge.pipeline.stages.stage1_extract import Stage1Extract


class _DummyLLM:
    async def generate_json(self, *args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        return {}


@pytest.mark.asyncio
async def test_reserve_basic_monotonic() -> None:
    """单线程多次 reserve：连续递增"""
    alloc = OrderIndexAllocator(start=100)
    a = await alloc.reserve(3)
    b = await alloc.reserve(2)
    c = await alloc.reserve(0)  # count=0 应至少推进 1

    assert a == 100
    assert b == 103  # 100 + 3
    assert c == 105  # 103 + 2
    assert alloc.current == 106  # 105 + max(0, 1)


@pytest.mark.asyncio
async def test_reserve_zero_count_advances_by_one() -> None:
    """reserve(0) 语义：返回起点，counter 至少推进 1"""
    alloc = OrderIndexAllocator(start=0)
    for expected in range(5):
        got = await alloc.reserve(0)
        assert got == expected


@pytest.mark.asyncio
async def test_concurrent_reserves_no_overlap() -> None:
    """并发 reserve：分配的 ID range 完全不重叠"""
    alloc = OrderIndexAllocator(start=0)

    # 100 个并发 task，每个 reserve 随机大小 1-10
    async def one(size: int) -> list[int]:
        start = await alloc.reserve(size)
        return list(range(start, start + size))

    sizes = [i % 10 + 1 for i in range(100)]
    results = await asyncio.gather(*[one(s) for s in sizes])

    # 展平所有 id
    all_ids = [x for sub in results for x in sub]
    total_expected = sum(sizes)

    assert len(all_ids) == total_expected
    assert len(set(all_ids)) == total_expected  # 无重复
    # 紧凑性：所有 id 构成连续区间
    assert sorted(all_ids) == list(range(total_expected))


@pytest.mark.asyncio
async def test_many_concurrent_tasks_see_unique_starts() -> None:
    """1000 个并发 reserve(1) 必须返回 1000 个不同的起点"""
    alloc = OrderIndexAllocator(start=500)
    starts = await asyncio.gather(*[alloc.reserve(1) for _ in range(1000)])
    assert len(set(starts)) == 1000
    assert sorted(starts) == list(range(500, 1500))


@pytest.mark.asyncio
async def test_stage1_uses_shared_allocator_by_batch_size() -> None:
    """Stage1 目录并发路径必须按实际 batch 大小动态预留 ID。"""
    alloc = OrderIndexAllocator(start=0)
    cfg = AppConfig()
    stages = [
        Stage1Extract(
            llm_client=_DummyLLM(),  # type: ignore[arg-type]
            config=cfg,
            checkpoint_root=None,
            discipline=Discipline.MATH,
            order_index_allocator=alloc,
        )
        for _ in range(2)
    ]

    first, second = await asyncio.gather(
        stages[0]._allocate_item_ids(3),
        stages[1]._allocate_item_ids(2),
    )
    all_ids = first + second
    assert len(all_ids) == 5
    assert len(set(all_ids)) == 5
    assert sorted(all_ids) == [0, 1, 2, 3, 4]
