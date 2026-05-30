"""Stage1Extract 单条滑动窗口 smoke tests（mock LLM）"""

from __future__ import annotations

import asyncio
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


class _ScriptedLLM:
    """按顺序吐出预设响应的 fake LLM"""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def generate_json(self, prompt: str, **kw: Any) -> Any:
        self.call_count += 1
        if not self._responses:
            return _no_items()
        return self._responses.pop(0)

    def enable_pipeline_logging(self, *a: Any, **kw: Any) -> None:
        pass


def _make_state() -> FileExtractionState:
    return FileExtractionState(file_path="test.md", file_hash="hash123")


def _make_config(window: int = 500, max_window: int = 3000) -> AppConfig:
    return AppConfig(
        extract_window_lines=window,
        extract_max_window_lines=max_window,
        extract_window_expand_step=window,
        extract_empty_force_limit=1,
        stage1_extract_max_retries=2,
        extract_retry_base_delay=0.01,
        # 测试内容为人造纯文本，无真实条目标记；关闭 fast-skip 以测试 LLM 流程
        empty_window_fast_skip=False,
    )


def _mk_item(start: int, end: int, item_type: str = "theorem") -> dict:
    return {
        "items": [
            {
                "category": "knowledge",
                "item_type": item_type,
                "number": f"{start}",
                "corrected_content": "Body.",
                "corrected_proof": "Proof.",
                "corrected_solution": None,
                "solution_status": "complete",
                "start_line": start,
                "end_line": end,
            }
        ],
        "stop_reason": "all_complete",
        "last_consumed_line": end,
        "issues": [],
    }


def _no_items() -> dict[str, object]:
    return {
        "items": [],
        "stop_reason": "no_items_found",
        "last_consumed_line": 0,
        "issues": [],
    }


class TestStage1ExtractSingle:
    def test_single_item_emitted(self, tmp_path: Path) -> None:
        # window 500 行文本；LLM 返回一条 item，然后都 null
        llm = _ScriptedLLM([_mk_item(1, 100)])
        config = _make_config(window=500)
        state = _make_state()

        async def run() -> list[object]:
            stage = Stage1Extract(
                llm_client=llm,  # type: ignore[arg-type]
                config=config,
                checkpoint_root=tmp_path,
                discipline=Discipline.MATH,
            )
            q_md: asyncio.Queue[object] = asyncio.Queue()
            q_raw: asyncio.Queue[object] = asyncio.Queue()

            md_text = "\n".join(f"line {i}" for i in range(1, 501)) + "\n"
            await q_md.put(
                MdBlockSource(
                    file_path="t.md",
                    page_start=1,
                    page_end=1,
                    md_path=str(tmp_path / "page.md"),
                    md_text=md_text,
                    block_index=0,
                )
            )
            await q_md.put(SENTINEL)
            await stage.run(q_md, q_raw, state)

            results: list[object] = []
            while not q_raw.empty():
                results.append(q_raw.get_nowait())
            return results

        results = asyncio.run(run())
        # 至少有 1 个 ExtractedBatch + 1 条 SENTINEL
        emitted = [r for r in results if isinstance(r, ExtractedBatch)]
        assert len(emitted) == 1
        assert emitted[0].item_ids == [0]
        assert state.next_item_id == 1
        assert state.stage1_extracted_count == 1
        # 最后是 SENTINEL
        assert results[-1] is SENTINEL


class TestForceConsume:
    def test_force_consume_on_empty_max_window(self, tmp_path: Path) -> None:
        """连续 LLM 返回 null → 扩窗口 → 达 max → force consume slide=base_window"""
        # max_window=1000, base=500, expand=500, empty_force_limit=1
        config = _make_config(window=500, max_window=1000)
        # 所有响应都是 null
        llm = _ScriptedLLM([_no_items()] * 10)
        state = _make_state()

        async def run() -> list[object]:
            stage = Stage1Extract(
                llm_client=llm,  # type: ignore[arg-type]
                config=config,
                checkpoint_root=tmp_path,
                discipline=Discipline.MATH,
            )
            q_md: asyncio.Queue[object] = asyncio.Queue()
            q_raw: asyncio.Queue[object] = asyncio.Queue()

            md_text = "\n".join(f"line {i}" for i in range(1, 2001)) + "\n"
            await q_md.put(
                MdBlockSource(
                    file_path="t.md",
                    page_start=1,
                    page_end=1,
                    md_path=str(tmp_path / "page.md"),
                    md_text=md_text,
                    block_index=0,
                )
            )
            await q_md.put(SENTINEL)
            await stage.run(q_md, q_raw, state)
            results: list[object] = []
            while not q_raw.empty():
                results.append(q_raw.get_nowait())
            return results

        results = asyncio.run(run())
        # 没有 item emit，但 stage 没死锁
        emitted = [r for r in results if isinstance(r, ExtractedBatch)]
        assert len(emitted) == 0
        assert results[-1] is SENTINEL
        assert state.next_item_id == 0


class TestFailedBlockCheckpoint:
    def test_retry_exhausted_marks_current_block_failed(self, tmp_path: Path) -> None:
        config = _make_config(window=3, max_window=3)
        config.stage1_extract_max_retries = 1
        llm = _ScriptedLLM([{"not": "valid"}])
        state = _make_state()

        async def run() -> None:
            stage = Stage1Extract(
                llm_client=llm,  # type: ignore[arg-type]
                config=config,
                checkpoint_root=tmp_path,
                discipline=Discipline.MATH,
            )
            q_md: asyncio.Queue[object] = asyncio.Queue()
            q_raw: asyncio.Queue[object] = asyncio.Queue()
            await q_md.put(
                MdBlockSource(
                    file_path="t.md",
                    page_start=1,
                    page_end=1,
                    md_path=str(tmp_path / "page.md"),
                    md_text="line 1\nline 2\nline 3\n",
                    block_index=0,
                )
            )
            await q_md.put(SENTINEL)
            await stage.run(q_md, q_raw, state)

        asyncio.run(run())
        assert state.failed_blocks == [0]


class TestResumeCheckpointPrecision:
    def test_state_checkpointed_with_line_precision_inside_block(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        llm = _ScriptedLLM([_mk_item(1, 3), _no_items(), _no_items()])
        config = _make_config(window=3, max_window=6)
        state = _make_state()
        snapshots: list[FileExtractionState] = []

        async def fake_save_state_locked(
            checkpoint_root: Path | None,
            current_state: FileExtractionState,
            *,
            force: bool = False,
            min_interval_sec: float = 3.0,
        ) -> None:
            del checkpoint_root, force, min_interval_sec
            snapshots.append(current_state.model_copy(deep=True))

        monkeypatch.setattr(
            "sciforge.pipeline.stages.stage1_extract.save_state_locked",
            fake_save_state_locked,
        )

        async def run() -> None:
            stage = Stage1Extract(
                llm_client=llm,  # type: ignore[arg-type]
                config=config,
                checkpoint_root=tmp_path,
                discipline=Discipline.MATH,
            )
            q_md: asyncio.Queue[object] = asyncio.Queue()
            q_raw: asyncio.Queue[object] = asyncio.Queue()

            md_text = "\n".join(f"line {i}" for i in range(1, 7)) + "\n"
            await q_md.put(
                MdBlockSource(
                    file_path="t.md",
                    page_start=1,
                    page_end=1,
                    md_path=str(tmp_path / "page.md"),
                    md_text=md_text,
                    block_index=0,
                )
            )
            await q_md.put(SENTINEL)
            await stage.run(q_md, q_raw, state)

        asyncio.run(run())

        assert any(
            snap.current_block_idx == 1
            and snap.next_item_id == 1
            and snap.line_offset == 3
            and bool(snap.line_buffer_carry)
            and snap.line_buffer_carry[0].split("|", 1)[0].strip() == "4"
            for snap in snapshots
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
