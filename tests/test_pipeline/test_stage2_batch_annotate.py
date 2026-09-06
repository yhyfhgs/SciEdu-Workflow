"""REC-04 — Stage2Annotate 批量打标（N 条合 1 次 LLM 调用 + 降级逐条）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline, FileExtractionState, ReviewedItem
from sciforge.core.sentinel import SENTINEL
from sciforge.pipeline.llm_schemas import SingleItemExtractLLM
from sciforge.pipeline.stages.stage2_annotate import Stage2Annotate

_ANN = {
    "subject": "test:测试",
    "taxonomy_path": "math.algebra",
    "tags": ["math:数学", "algebra:代数", "group:群"],
    "difficulty": 5,
    "question_type": None,
    "methods": [],
}


class _BatchLLM:
    """批量请求返回 N 条标注、单条请求返回单条标注；记录调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, prompt: str, **kw: Any) -> dict[str, Any]:
        self.calls += 1
        if '"index"' in prompt:  # 批量 prompt（items_json 每条带 index）
            n = max(1, prompt.count('"index"'))
            return {"annotations": [dict(_ANN) for _ in range(n)]}
        return dict(_ANN)  # 单条 prompt

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


class _BadBatchLLM:
    """始终返回空 annotations → 触发长度不符 → 降级逐条。"""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, prompt: str, **kw: Any) -> dict[str, Any]:
        self.calls += 1
        # 批量请求返回空（触发降级）；逐条请求返回单条标注
        if '"annotations"' in prompt or '"index"' in prompt:
            return {"annotations": []}
        return dict(_ANN)

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


async def _drive(stage: Stage2Annotate, items: list[ReviewedItem]) -> list[int]:
    state = FileExtractionState(file_path="t.md", file_hash="")
    in_q: asyncio.Queue[object] = asyncio.Queue()
    out_q: asyncio.Queue[object] = asyncio.Queue()
    for it in items:
        await in_q.put(it)
    await in_q.put(SENTINEL)
    await stage.run(in_q, out_q, state)
    out: list[int] = []
    while not out_q.empty():
        msg = out_q.get_nowait()
        if msg is not SENTINEL:
            out.append(msg.item_id)  # type: ignore[attr-defined]
    return out


@pytest.mark.asyncio
async def test_batch_annotate_one_call_for_n_items() -> None:
    """同 block 的 3 条 → 1 次 LLM 调用产出 3 条标注，顺序保持。"""
    llm = _BatchLLM()
    stage = Stage2Annotate(
        llm_client=llm,  # type: ignore[arg-type]
        config=AppConfig(stage2_annotate_concurrency=1, stage2_annotate_batch=True),
        checkpoint_root=None,
        discipline=Discipline.MATH,
    )
    emitted = await _drive(stage, [_reviewed(i, block_index=0) for i in range(3)])
    assert emitted == [0, 1, 2]
    assert llm.calls == 1, f"3 条应合并为 1 次调用，实际 {llm.calls}"


@pytest.mark.asyncio
async def test_batch_splits_by_block_index() -> None:
    """不同 block_index 各成一组 → 2 次调用。"""
    llm = _BatchLLM()
    stage = Stage2Annotate(
        llm_client=llm,  # type: ignore[arg-type]
        config=AppConfig(stage2_annotate_concurrency=1, stage2_annotate_batch=True),
        checkpoint_root=None,
        discipline=Discipline.MATH,
    )
    items = [_reviewed(0, 0), _reviewed(1, 0), _reviewed(2, 1)]
    emitted = await _drive(stage, items)
    assert emitted == [0, 1, 2]
    assert llm.calls == 2, f"两个 block 应 2 次调用，实际 {llm.calls}"


@pytest.mark.asyncio
async def test_batch_respects_batch_size_cap() -> None:
    """batch_size=2 时 5 条同 block → 3 组（2+2+1）。"""
    llm = _BatchLLM()
    stage = Stage2Annotate(
        llm_client=llm,  # type: ignore[arg-type]
        config=AppConfig(
            stage2_annotate_concurrency=1,
            stage2_annotate_batch=True,
            stage2_annotate_batch_size=2,
        ),
        checkpoint_root=None,
        discipline=Discipline.MATH,
    )
    emitted = await _drive(stage, [_reviewed(i, 0) for i in range(5)])
    assert emitted == [0, 1, 2, 3, 4]
    assert llm.calls == 3, f"5 条按 2/批应 3 次调用，实际 {llm.calls}"


@pytest.mark.asyncio
async def test_batch_length_mismatch_degrades_to_per_item() -> None:
    """批量返回长度不符 → 降级逐条，仍产出全部条目。"""
    llm = _BadBatchLLM()
    stage = Stage2Annotate(
        llm_client=llm,  # type: ignore[arg-type]
        config=AppConfig(
            stage2_annotate_concurrency=1,
            stage2_annotate_batch=True,
            stage2_annotate_max_retries=1,
            extract_retry_base_delay=0.01,
        ),
        checkpoint_root=None,
        discipline=Discipline.MATH,
    )
    emitted = await _drive(stage, [_reviewed(i, 0) for i in range(3)])
    assert emitted == [0, 1, 2]  # 降级后仍无丢失


@pytest.mark.asyncio
async def test_batch_disabled_falls_back_to_per_item() -> None:
    """stage2_annotate_batch=False → 逐条调用（每条 1 次）。"""
    llm = _BatchLLM()
    stage = Stage2Annotate(
        llm_client=llm,  # type: ignore[arg-type]
        config=AppConfig(stage2_annotate_concurrency=1, stage2_annotate_batch=False),
        checkpoint_root=None,
        discipline=Discipline.MATH,
    )
    emitted = await _drive(stage, [_reviewed(i, 0) for i in range(3)])
    assert emitted == [0, 1, 2]
    assert llm.calls == 3, f"关闭批量应逐条 3 次调用，实际 {llm.calls}"
