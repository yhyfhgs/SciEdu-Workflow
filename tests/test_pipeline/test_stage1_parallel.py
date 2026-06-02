"""Stage1Extract 单文档内并行提取路径（intra_doc_parallel_extract）测试。

覆盖你的最高优先级「不漏提取」：
- 多 section 段间并发 → 条目按文档(段)顺序、item_id 连续、无丢失。
- 超大 section 跨多窗口 → carry-aware 滑窗不在窗口边界切断条目。
- resume：stage1_completed 已为真 → 不重复提取、drain 队列。
- 默认 streaming 路径不受影响（见 test_stage1_extract.py）。
"""

from __future__ import annotations

import asyncio
import re as _re
from pathlib import Path
from typing import Any

import pytest

from sciforge.config.settings import AppConfig
from sciforge.core.models import (
    Discipline,
    ExtractedBatch,
    FileExtractionState,
    MdBlockSource,
)
from sciforge.core.sentinel import SENTINEL
from sciforge.pipeline.stages.stage1_extract import Stage1Extract


class _MarkerLLM:
    """对窗口里每个 `NNN | ITEMk` 标记返回一条 item（单行条目）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def generate_json(self, prompt: str, **kw: Any) -> dict[str, Any]:
        self.calls += 1
        found = sorted(
            {(int(m.group(1)), m.group(2)) for m in _re.finditer(r"(\d+)\s*\|\s*ITEM(\d+)", prompt)}
        )
        if not found:
            return {
                "items": [],
                "stop_reason": "no_items_found",
                "last_consumed_line": 0,
                "issues": [],
            }
        items = [
            {
                "category": "knowledge",
                "item_type": "theorem",
                "number": num,
                "corrected_content": f"body {num}",
                "corrected_proof": "P.",
                "corrected_solution": None,
                "solution_status": "complete",
                "start_line": ln,
                "end_line": ln,
            }
            for ln, num in found
        ]
        return {
            "items": items,
            "stop_reason": "all_complete",
            "last_consumed_line": items[-1]["end_line"],
            "issues": [],
        }

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


def _cfg(**over: Any) -> AppConfig:
    base: dict[str, Any] = dict(
        intra_doc_parallel_extract=True,
        intra_doc_min_segment_lines=5,
        intra_doc_segment_concurrency=4,
        extract_window_lines=500,
        extract_max_window_lines=3000,
        extract_window_expand_step=500,
        extract_empty_force_limit=1,
        stage1_extract_max_retries=2,
        extract_retry_base_delay=0.01,
        empty_window_fast_skip=False,  # 测试内容非标准标记，关 fast-skip 走 LLM
    )
    base.update(over)
    return AppConfig(**base)


async def _run(stage: Stage1Extract, block: MdBlockSource, state: FileExtractionState) -> list[Any]:
    q_md: asyncio.Queue[Any] = asyncio.Queue()
    q_raw: asyncio.Queue[Any] = asyncio.Queue()
    await q_md.put(block)
    await q_md.put(SENTINEL)
    await stage.run(q_md, q_raw, state)
    out: list[Any] = []
    while not q_raw.empty():
        out.append(q_raw.get_nowait())
    return out


def test_parallel_multi_segment_order_and_completeness() -> None:
    """3 个 section → 段间并发 → 条目按文档顺序、item_id 连续、无丢失。"""
    lines = [
        "# Section A",  # 0
        "ITEM1",
        "ITEM2",
        "f",
        "f",
        "f",
        "f",
        "# Section B",  # 7
        "ITEM3",
        "ITEM4",
        "f",
        "f",
        "f",
        "f",
        "# Section C",  # 14
        "ITEM5",
        "ITEM6",
        "ITEM7",
        "f",
        "f",
        "f",
    ]
    block = MdBlockSource(
        file_path="t.md",
        page_start=1,
        page_end=1,
        md_path="t.md",
        md_text="\n".join(lines) + "\n",
        block_index=0,
        section_boundaries=[(0, 1, "A"), (7, 1, "B"), (14, 1, "C")],
    )
    llm = _MarkerLLM()
    state = FileExtractionState(file_path="t.md", file_hash="")
    stage = Stage1Extract(
        llm_client=llm, config=_cfg(), checkpoint_root=None, discipline=Discipline.MATH
    )  # type: ignore[arg-type]

    out = asyncio.run(_run(stage, block, state))
    batches = [b for b in out if isinstance(b, ExtractedBatch)]
    assert out[-1] is SENTINEL

    # 3 段 → 3 个 batch，按文档顺序
    assert len(batches) == 3
    all_ids = [i for b in batches for i in b.item_ids]
    all_nums = [it.number for b in batches for it in b.items]
    # item_id 全局连续 0..6，order_index=item_id 不变量
    assert all_ids == list(range(7)), all_ids
    # 条目按文档顺序、无丢失（7 条全在）
    assert all_nums == ["1", "2", "3", "4", "5", "6", "7"], all_nums
    assert state.stage1_extracted_count == 7
    assert state.stage1_completed is True


def test_parallel_large_section_carry_no_loss() -> None:
    """单个超大 section 跨多个 base_window → carry-aware 不在窗口边界丢条目。"""
    # 30 行无 section 边界 → 1 段；base_window=5 → 段内需 ~6 窗口滑动
    n = 30
    lines: list[str] = []
    expected_nums: list[str] = []
    for i in range(n):
        if i % 4 == 1:  # 在 1,5,9,...,29 行放标记，跨越多个 5 行窗口
            k = i
            lines.append(f"ITEM{k}")
            expected_nums.append(str(k))
        else:
            lines.append("filler")
    block = MdBlockSource(
        file_path="t.md",
        page_start=1,
        page_end=1,
        md_path="t.md",
        md_text="\n".join(lines) + "\n",
        block_index=0,
        section_boundaries=[],  # 无边界 → 单段
    )
    llm = _MarkerLLM()
    state = FileExtractionState(file_path="t.md", file_hash="")
    stage = Stage1Extract(
        llm_client=llm,  # type: ignore[arg-type]
        config=_cfg(
            extract_window_lines=5, extract_max_window_lines=10, extract_window_expand_step=5
        ),
        checkpoint_root=None,
        discipline=Discipline.MATH,
    )

    out = asyncio.run(_run(stage, block, state))
    batches = [b for b in out if isinstance(b, ExtractedBatch)]
    got_nums = [it.number for b in batches for it in b.items]
    got_ids = [i for b in batches for i in b.item_ids]

    assert got_nums == expected_nums, f"漏条目：期望 {expected_nums}，实际 {got_nums}"
    assert got_ids == list(range(len(expected_nums))), got_ids  # id 连续
    assert state.stage1_completed is True


def test_parallel_resume_skips_when_completed(tmp_path: Path) -> None:
    """stage1_completed 已为真 → 不重复调 LLM，仅 drain 队列并放行 SENTINEL。"""
    block = MdBlockSource(
        file_path="t.md",
        page_start=1,
        page_end=1,
        md_path="t.md",
        md_text="ITEM1\nITEM2\n",
        block_index=0,
        section_boundaries=[],
    )
    llm = _MarkerLLM()
    state = FileExtractionState(file_path="t.md", file_hash="")
    state.stage1_completed = True  # 模拟已完成
    stage = Stage1Extract(
        llm_client=llm, config=_cfg(), checkpoint_root=None, discipline=Discipline.MATH
    )  # type: ignore[arg-type]

    out = asyncio.run(_run(stage, block, state))
    assert llm.calls == 0  # 未调用 LLM
    assert [b for b in out if isinstance(b, ExtractedBatch)] == []
    assert out[-1] is SENTINEL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
