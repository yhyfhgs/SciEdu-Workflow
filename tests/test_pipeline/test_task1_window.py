"""Tests for Task1Extractor._process_windows sliding-window logic.

覆盖：
- 单窗口正常消费
- 跨 block 的 carry 与行号连续性
- incomplete 触发动态窗口扩展
- final incomplete force-consume（避免死锁）
- truncation_line off-by-one fix 验证
- cut_idx 超出 window 时退化为整窗消费
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sciforge.core.models import FileExtractionState, MdBlockSource
from sciforge.pipeline import task1_extractor as te_mod
from sciforge.pipeline.task1_extractor import SENTINEL, Task1Extractor


class _FakeLLMClient:
    """占位 LLM client；不被真的调用，`call_llm_json_with_retry` 被 monkeypatch"""


class _FakeConfig:
    def __init__(
        self,
        *,
        extract_window_lines: int = 10,
        extract_max_window_lines: int = 40,
    ) -> None:
        self.extract_window_lines = extract_window_lines
        self.extract_max_window_lines = extract_max_window_lines
        self.max_extract_retries = 3
        self.extract_retry_base_delay = 0.0
        self.tag_tree_max_depth = 4
        self.tag_tree_max_nodes = 50


class _FakeStorage:
    def __init__(self) -> None:
        self.saved: list[tuple[list[Any], list[Any]]] = []

    def save_batch(self, kps: list[Any], questions: list[Any]) -> int:
        self.saved.append((list(kps), list(questions)))
        return len(kps) + len(questions)


def _make_extractor(
    responses: list[dict[str, Any]],
    *,
    base_window: int = 10,
    max_window: int = 40,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Task1Extractor, list[dict[str, Any]], _FakeStorage]:
    config = _FakeConfig(extract_window_lines=base_window, extract_max_window_lines=max_window)
    storage = _FakeStorage()
    extractor = Task1Extractor(
        llm_client=_FakeLLMClient(),  # type: ignore[arg-type]
        config=config,  # type: ignore[arg-type]
        storage=storage,  # type: ignore[arg-type]
        checkpoint_root=Path("/tmp/fake_checkpoint"),
    )

    call_log: list[dict[str, Any]] = []
    idx = {"i": 0}
    # 给 call_log 暴露窗口信息（prompt 是拼接好的 window 行）
    last_window_lines: list[str] = []

    async def fake_call(llm: Any, prompt: str, **kwargs: Any) -> dict[str, Any]:
        i = idx["i"]
        idx["i"] += 1
        call_log.append(
            {
                "idx": i,
                "prompt_len": len(prompt),
                "window_lines": list(last_window_lines),
            }
        )
        if i >= len(responses):
            raise RuntimeError(f"no more fake responses (idx={i})")
        return responses[i]

    monkeypatch.setattr(te_mod, "call_llm_json_with_retry", fake_call)

    # 拦截 _extract_and_classify 以记录当前 window 内容
    orig_extract = extractor._extract_and_classify

    async def traced_extract(window: list[str]) -> dict[str, Any]:
        nonlocal last_window_lines
        last_window_lines = list(window)
        return await orig_extract(window)

    extractor._extract_and_classify = traced_extract  # type: ignore[method-assign]

    # 跳过真实存储调用（storage 的 save_batch 会被 asyncio.to_thread 包）
    # 我们保留 storage fake，它的 save_batch 是同步的
    return extractor, call_log, storage


def _make_block(*, start_line: int, num_lines: int, block_index: int = 0) -> MdBlockSource:
    """构造一个 block，其 md_text 是 num_lines 行简单文本"""
    md_text = "".join(f"line{start_line + i}\n" for i in range(num_lines))
    return MdBlockSource(
        file_path="/tmp/foo.md",
        page_start=start_line,
        page_end=start_line + num_lines - 1,
        md_path=f"/tmp/foo/page_{start_line:03d}-{start_line + num_lines - 1:03d}.md",
        md_text=md_text,
        block_index=block_index,
    )


async def _feed_and_run(
    extractor: Task1Extractor,
    blocks: list[MdBlockSource],
    state: FileExtractionState,
) -> list[Any]:
    """把 blocks 推到一个 queue，再加 SENTINEL，跑 extractor.run() 消费"""
    queue: asyncio.Queue[object] = asyncio.Queue()
    for b in blocks:
        await queue.put(b)
    await queue.put(SENTINEL)
    stored = []
    async for s in extractor.run(queue, state):
        stored.append(s)
    return stored


# ── 基本消费 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_window_normal_consume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 10 行 window，block 正好 10 行；LLM 返回 truncation_line=10（全部消费）
    extractor, call_log, _ = _make_extractor(
        responses=[{"truncation_line": 10, "items": []}],
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    block = _make_block(start_line=1, num_lines=10)

    await _feed_and_run(extractor, [block], state)

    assert len(call_log) == 1
    assert extractor.line_offset == 10
    assert extractor.line_buffer == []


@pytest.mark.asyncio
async def test_truncation_line_off_by_one_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trunc=N 表示 1..N 是完整的，下一窗口从 N+1 开始；consumed 应包含行 N"""
    # 15 行 block，window=10；第一次 LLM 返回 trunc=7 (行 1-7 完整)
    # 预期：consumed=7, buffer 剩 8 行 (行 8-15), line_offset=7
    # 然后 is_last=True, buffer<10, window 取剩余 8 行, LLM 返回 trunc=15 (全部)
    # 预期：consumed=8, line_offset=15, buffer=[]
    extractor, call_log, _ = _make_extractor(
        responses=[
            {"truncation_line": 7, "items": []},
            {"truncation_line": 15, "items": []},
        ],
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    block = _make_block(start_line=1, num_lines=15)

    await _feed_and_run(extractor, [block], state)

    assert len(call_log) == 2
    # 第二次 LLM 看到的应该是剩余 8 行（行 8..15）
    second_window = call_log[1]["window_lines"]
    assert len(second_window) == 8
    assert "8" in second_window[0]  # 第一行行号应该是 8
    assert extractor.line_offset == 15
    assert extractor.line_buffer == []


# ── 跨 block carry ─────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_block_line_numbers_continuous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block_A 10 行 → consume 5 行；Block_B 10 行到达后，buffer 首行号应该是 6"""
    extractor, call_log, _ = _make_extractor(
        responses=[
            {"truncation_line": 5, "items": []},  # Block_A: 消费 5 行
            {"truncation_line": 15, "items": []},  # Block_A 剩 5 + Block_B 5 行（10 行 window）
            {"truncation_line": 20, "items": []},  # 剩 5 行 (行 16-20), is_last
        ],
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")

    blocks = [
        _make_block(start_line=1, num_lines=10, block_index=0),
        _make_block(start_line=11, num_lines=10, block_index=1),
    ]
    await _feed_and_run(extractor, blocks, state)

    # 第一次窗口：行 1-10
    first_window = call_log[0]["window_lines"]
    assert "1" in first_window[0].split("|")[0]
    assert "10" in first_window[9].split("|")[0]

    # 第二次窗口：应该是行 6-15（Block_A 剩 5 + Block_B 前 5）
    second_window = call_log[1]["window_lines"]
    assert "6" in second_window[0].split("|")[0]
    assert "15" in second_window[9].split("|")[0]

    assert extractor.line_offset == 20
    assert extractor.line_buffer == []


# ── Incomplete 动态扩窗 ─────────────────────────────────


@pytest.mark.asyncio
async def test_incomplete_expands_window_when_buffer_has_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """buffer=30 行，window=10；incomplete → 扩到 20 → 仍 incomplete → 扩到 30"""
    extractor, call_log, _ = _make_extractor(
        responses=[
            {"truncation_line": "incomplete", "items": []},  # 10 行，incomplete
            {"truncation_line": "incomplete", "items": []},  # 20 行，incomplete
            {"truncation_line": 30, "items": []},  # 30 行，成功
        ],
        base_window=10,
        max_window=40,
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    block = _make_block(start_line=1, num_lines=30)

    await _feed_and_run(extractor, [block], state)

    assert len(call_log) == 3
    assert len(call_log[0]["window_lines"]) == 10
    assert len(call_log[1]["window_lines"]) == 20
    assert len(call_log[2]["window_lines"]) == 30


@pytest.mark.asyncio
async def test_incomplete_no_infinite_loop_when_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """最严重的 bug：is_last=True, buffer 固定，incomplete 不应反复调 LLM"""
    # buffer=15 行 <= base_window=10（严格来说不，15>10，但等一下）
    # 实际情况：block 5 行（比 base_window 小），is_last=True
    # 第一次 window=5 行（全部），LLM incomplete
    # 无法获取更多（buffer 没有更多，is_last=True）
    # 应该 force consume，**不应扩窗再次调 LLM**
    extractor, call_log, _ = _make_extractor(
        responses=[
            {"truncation_line": "incomplete", "items": []},
        ],
        base_window=10,
        max_window=40,
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    block = _make_block(start_line=1, num_lines=5)

    await _feed_and_run(extractor, [block], state)

    # 只应调一次 LLM（无法扩窗 + is_last → force consume）
    assert len(call_log) == 1
    assert extractor.line_offset == 5
    assert extractor.line_buffer == []


@pytest.mark.asyncio
async def test_incomplete_wait_next_block_then_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Block_A 10 行 incomplete → 等 Block_B → 扩窗看到更多内容 → 成功"""
    extractor, call_log, _ = _make_extractor(
        responses=[
            # Block_A 10 行：窗口是 10 行，incomplete
            # buffer_has_more=False (10 行 == window), can_wait_next=True
            # → 扩窗到 20，但 buffer<20，break 等 Block_B
            {"truncation_line": "incomplete", "items": []},
            # Block_B 到达，buffer=20；target=20，window 取 20 行
            # 返回 trunc=20 成功
            {"truncation_line": 20, "items": []},
        ],
        base_window=10,
        max_window=40,
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    blocks = [
        _make_block(start_line=1, num_lines=10, block_index=0),
        _make_block(start_line=11, num_lines=10, block_index=1),
    ]
    await _feed_and_run(extractor, blocks, state)

    assert len(call_log) == 2
    assert len(call_log[0]["window_lines"]) == 10
    assert len(call_log[1]["window_lines"]) == 20  # 扩窗后看到完整 20 行
    assert extractor.line_offset == 20


@pytest.mark.asyncio
async def test_incomplete_expand_reaches_max_then_force(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一直 incomplete 直到 max_window，然后 is_last 时 force consume

    Block 30 行到达时 is_last=False：
    1. window=10, incomplete → 扩到 20
    2. window=20, incomplete → 扩到 30
    3. window=30 (== max), incomplete → 无法扩大且 is_last=False → break 等下一个 block
    Sentinel 到达，is_last=True：
    4. window=30, incomplete → 无法扩大且 is_last=True → force consume
    """
    extractor, call_log, _ = _make_extractor(
        responses=[
            {"truncation_line": "incomplete", "items": []},
            {"truncation_line": "incomplete", "items": []},
            {"truncation_line": "incomplete", "items": []},
            {"truncation_line": "incomplete", "items": []},
        ],
        base_window=10,
        max_window=30,
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    block = _make_block(start_line=1, num_lines=30)

    await _feed_and_run(extractor, [block], state)

    # 4 次 LLM 调用：3 次在 block 到达时 (10/20/30)，1 次在 sentinel 到达时 force consume
    assert len(call_log) == 4
    assert extractor.line_offset == 30  # 整窗被消费
    assert extractor.line_buffer == []


# ── 边界与鲁棒性 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_window_reset_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功消费后窗口大小应复位为 base_window"""
    extractor, call_log, _ = _make_extractor(
        responses=[
            {"truncation_line": "incomplete", "items": []},  # 扩到 20
            {"truncation_line": 15, "items": []},  # 20 行 window，成功消费 15 行
            # 剩 15 行，再取 window_size=base(10)
            {"truncation_line": 25, "items": []},  # 10 行 window，成功消费 10 行
            # 剩 5 行, is_last → window 5 行
            {"truncation_line": 30, "items": []},  # 5 行 window，成功消费
        ],
        base_window=10,
        max_window=40,
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    block = _make_block(start_line=1, num_lines=30)

    await _feed_and_run(extractor, [block], state)

    # 验证窗口大小变化：10 → 20（incomplete expand）→ 成功后复位 10 → 10 → 5
    assert len(call_log[0]["window_lines"]) == 10
    assert len(call_log[1]["window_lines"]) == 20
    assert len(call_log[2]["window_lines"]) == 10
    assert len(call_log[3]["window_lines"]) == 5
    assert extractor.line_offset == 30


@pytest.mark.asyncio
async def test_cut_idx_out_of_window_falls_back_to_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM 返回的 truncation_line 在 buffer 外时，应该消费整个 window"""
    extractor, call_log, _ = _make_extractor(
        responses=[
            # trunc=9999 超出任何行号；应退化为消费整个 window(10)
            {"truncation_line": 9999, "items": []},
        ],
        base_window=10,
        max_window=40,
        monkeypatch=monkeypatch,
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    block = _make_block(start_line=1, num_lines=10)

    await _feed_and_run(extractor, [block], state)

    assert extractor.line_offset == 10
    assert extractor.line_buffer == []


@pytest.mark.asyncio
async def test_empty_block_is_sentinel_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只推 SENTINEL，应该立即返回不调用 LLM"""
    extractor, call_log, _ = _make_extractor(
        responses=[], base_window=10, max_window=40, monkeypatch=monkeypatch
    )
    state = FileExtractionState(file_path="/tmp/foo.md", file_hash="h")
    await _feed_and_run(extractor, [], state)
    assert call_log == []
    assert extractor.line_offset == 0
