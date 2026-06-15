"""Stage2 并发路径 + 上游 gap 回归测试。

背景：Stage1Review 拒绝条目会在 item_id 序列里留下 gap（如 0,1,3,4 缺 2）。
Stage2Annotate / Stage2Review 在 concurrency>1 时用 ReorderBuffer 恢复顺序，
buffer 的 _next_id 游标必须被显式 skip() 跨过缺失 id，否则会**卡在缺失 id 上**，
把后续所有条目积压到 _pending、直到 close() 才一次性 flush——streaming 失效、
大文档内存暴涨。分发器须对每个 gap 主动 buf.skip()。

这两个测试分别验证：
- correctness：含 gap 的输入仍按 item_id 升序、无丢失地流出。
- streaming：gap 之后的条目在 SENTINEL/close 之前就被释放（不被卡住）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline, FileExtractionState, ReviewedItem
from sciforge.core.sentinel import SENTINEL
from sciforge.pipeline.llm_schemas import SingleItemExtractLLM
from sciforge.pipeline.stages.stage2_annotate import Stage2Annotate


class _ValidAnnotationLLM:
    """对任意条目都返回一份通过 SingleAnnotationLLM 校验的标注。"""

    async def generate_json(self, prompt: str, **kw: Any) -> dict[str, Any]:
        return {
            "subject": "test:测试",
            "taxonomy_path": "math.algebra",
            "tags": ["math:数学", "algebra:代数", "group:群"],
            "difficulty": 5,
            "question_type": None,
            "methods": [],
        }

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


def _reviewed(item_id: int, block_index: int = 0) -> ReviewedItem:
    return ReviewedItem(
        item_id=item_id,
        item=SingleItemExtractLLM(
            category="knowledge",
            item_type="theorem",
            number=str(item_id),
            corrected_content="Body.",
            corrected_proof="Proof.",
            corrected_solution=None,
            solution_status="complete",
            start_line=item_id * 10 + 1,
            end_line=item_id * 10 + 5,
        ),
        block_index=block_index,
        source_file="t.md",
    )


def _make_stage(concurrency: int = 2) -> Stage2Annotate:
    config = AppConfig(
        stage2_annotate_concurrency=concurrency,
        stage2_annotate_max_retries=1,
        extract_retry_base_delay=0.01,
    )
    return Stage2Annotate(
        llm_client=_ValidAnnotationLLM(),  # type: ignore[arg-type]
        config=config,
        checkpoint_root=None,  # 不落盘
        discipline=Discipline.MATH,
    )


@pytest.mark.asyncio
async def test_concurrent_gap_preserves_order_no_loss() -> None:
    """输入 item_ids=[0,1,3,4]（缺 2，模拟 Stage1Review 拒绝）→ 输出有序无丢失。"""
    stage = _make_stage(concurrency=2)
    state = FileExtractionState(file_path="t.md", file_hash="")

    in_q: asyncio.Queue[object] = asyncio.Queue()
    out_q: asyncio.Queue[object] = asyncio.Queue()
    for iid in (0, 1, 3, 4):
        await in_q.put(_reviewed(iid))
    await in_q.put(SENTINEL)

    await stage.run(in_q, out_q, state)

    emitted: list[int] = []
    while not out_q.empty():
        msg = out_q.get_nowait()
        if msg is SENTINEL:
            continue
        emitted.append(msg.item_id)  # type: ignore[attr-defined]

    assert emitted == [0, 1, 3, 4], f"应按升序无丢失流出，实际 {emitted}"


@pytest.mark.asyncio
async def test_concurrent_gap_does_not_stall_streaming() -> None:
    """gap 之后的条目须在 SENTINEL/close 之前就被释放（否则 buffer 卡死 → 超时）。

    REC-04：批量打标按 block_index 成组，组在 block 边界冲刷。用不同 block_index 触发
    各组在 SENTINEL 前冲刷，验证 gap（缺 1）被分发器在读取时 skip()、不卡 ReorderBuffer。
    """
    stage = _make_stage(concurrency=2)
    state = FileExtractionState(file_path="t.md", file_hash="")

    in_q: asyncio.Queue[object] = asyncio.Queue()
    out_q: asyncio.Queue[object] = asyncio.Queue()

    task = asyncio.create_task(stage._run_concurrent(in_q, out_q, state))
    # id=0(block0) → id=2(block1，缺 1) 触发 flush group[0] → id=3(block2) 触发 flush group[2]。
    # 若分发器不在读 id=2 时补 skip(1)，id=2 会永远卡在 _pending，下面的轮询将超时。
    await in_q.put(_reviewed(0, block_index=0))
    await in_q.put(_reviewed(2, block_index=1))
    await in_q.put(_reviewed(3, block_index=2))

    got: list[int] = []
    for _ in range(200):  # 最多等 ~2s
        try:
            msg = out_q.get_nowait()
            if msg is not SENTINEL:
                got.append(msg.item_id)  # type: ignore[attr-defined]
        except asyncio.QueueEmpty:
            await asyncio.sleep(0.01)
        if len(got) >= 2:
            break

    assert got == [0, 2], f"gap 后的 id=2 应在 close 前被释放，实际 {got}"

    await in_q.put(SENTINEL)
    await asyncio.wait_for(task, timeout=2.0)
