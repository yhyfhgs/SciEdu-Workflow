"""Tests for orchestrator._is_cache_complete and replay producer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sciforge.core.models import (
    AnnotatedItem,
    DirectoryProgress,
    ExtractedBatch,
    ExtractedSingleItem,
    FileExtractionState,
    MdBlockSource,
    ReviewedAnnotatedItem,
    ReviewedItem,
)
from sciforge.core.sentinel import SENTINEL
from sciforge.pipeline.llm_schemas import SingleAnnotationLLM, SingleItemExtractLLM
from sciforge.pipeline.orchestrator import PipelineOrchestrator
from sciforge.pipeline.resume_state import recognize_dir, state_dir


class _FakeConfig:
    def __init__(self, checkpoint_dir: Path) -> None:
        self.checkpoint_dir = checkpoint_dir
        self.data_dir = checkpoint_dir.parent / "data"
        self.pages_per_batch = 5
        self.vlm_dpi = 200
        self.vlm_max_concurrent = 1
        self.vlm_max_retries = 1
        self.pipeline_queue_size = 4
        self.extract_window_lines = 200
        self.extract_max_window_lines = 1000
        self.max_extract_retries = 3
        self.extract_retry_base_delay = 0.0
        self.tag_tree_max_depth = 4
        self.tag_tree_max_nodes = 50
        self.max_concurrent_files = 1
        self.max_concurrent_llm_calls = 1
        self.checkpoint_flush_interval = 3.0
        self.llm_log_dir = checkpoint_dir.parent / "llm_logs"
        self.llm_log_interval = 10
        self.pipeline_stage_queue_size = 2
        self.pipeline_annotate_queue_size = 4
        self.extract_window_expand_step = 500
        self.extract_empty_force_limit = 2
        self.stage1_extract_max_retries = 3
        self.stage1_review_enabled = True
        self.stage2_review_enabled = True
        self.stage1_review_max_retries = 2
        self.stage2_annotate_max_retries = 3
        self.stage2_review_max_retries = 2
        self.extract_review_enabled = True
        self.extract_stage1_review_enabled = True
        self.annotate_stage2_review_enabled = True
        self.task2_max_retries = 3
        self.retry_feedback_enabled = True
        self.pipeline_debug = False


def _make_orchestrator(checkpoint_dir: Path) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        config=_FakeConfig(checkpoint_dir),  # type: ignore[arg-type]
    )


def _touch(p: Path, content: str = "data") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _mk_extract_payload(item_id: int, source_file: str) -> ExtractedSingleItem:
    item = SingleItemExtractLLM(
        category="knowledge",
        item_type="theorem",
        number=str(item_id),
        corrected_content=f"content {item_id}",
        corrected_proof=f"proof {item_id}",
        corrected_solution=None,
        solution_status="complete",
        start_line=item_id,
        end_line=item_id,
    )
    return ExtractedSingleItem(
        item_id=item_id,
        item=item,
        window_lines=[f"{item_id:>7d} | line {item_id}\n"],
        window_line_range=(item_id, item_id),
        window_last_line=item_id,
        block_index=0,
        source_file=source_file,
        page_start=1,
        page_end=1,
    )


def _mk_reviewed_payload(item_id: int, source_file: str) -> ReviewedItem:
    extracted = _mk_extract_payload(item_id, source_file)
    return ReviewedItem(
        item_id=item_id,
        item=extracted.item,
        block_index=extracted.block_index,
        source_file=extracted.source_file,
        page_start=extracted.page_start,
        page_end=extracted.page_end,
    )


def _mk_annotation(item_id: int) -> SingleAnnotationLLM:
    return SingleAnnotationLLM(
        subject="algebra:代数",
        taxonomy_path="math.algebra",
        tags=["math:数学", "algebra:代数", f"item_{item_id}:条目{item_id}"],
        difficulty=5,
        question_type=None,
        methods=[],
    )


def _mk_annotated_payload(item_id: int, source_file: str) -> AnnotatedItem:
    reviewed = _mk_reviewed_payload(item_id, source_file)
    return AnnotatedItem(
        item_id=item_id,
        item=reviewed.item,
        annotation=_mk_annotation(item_id),
        block_index=reviewed.block_index,
        source_file=reviewed.source_file,
        page_start=reviewed.page_start,
        page_end=reviewed.page_end,
    )


def _mk_reviewed_annotated_payload(item_id: int, source_file: str) -> ReviewedAnnotatedItem:
    annotated = _mk_annotated_payload(item_id, source_file)
    return ReviewedAnnotatedItem(
        item_id=item_id,
        item=annotated.item,
        annotation=annotated.annotation,
        block_index=annotated.block_index,
        source_file=annotated.source_file,
        page_start=annotated.page_start,
        page_end=annotated.page_end,
    )


# ── _is_cache_complete ─────────────────────────────


def test_is_cache_complete_task0_flag_short_circuits(tmp_path: Path) -> None:
    """state.task0_completed=True 时直接返回 True，不扫文件"""
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="abc", task0_completed=True)

    # 即使没文件，也应返回 True
    assert orch._is_cache_complete([tmp_path / "nonexistent.pdf"], state) is True


def test_is_cache_complete_md_single_batch(tmp_path: Path) -> None:
    md = tmp_path / "chap.md"
    md.write_text("# foo\ncontent\n", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="h1")

    # 无缓存
    assert orch._is_cache_complete([md], state) is False

    # 创建单个 batch（MD 预期）
    new_dir = recognize_dir(ckpt, "h1", 0, md.stem)
    _touch(new_dir / "page_001-005.md", "hello")
    assert orch._is_cache_complete([md], state) is True

    # 多个 batch 则不算完整（MD 预期只有 1 个 batch）
    _touch(new_dir / "page_006-010.md", "more")
    assert orch._is_cache_complete([md], state) is False


def test_is_cache_complete_pdf_1based_complete(tmp_path: Path) -> None:
    pytest.importorskip("fitz")
    import fitz

    # 创建一个 10 页的临时 PDF
    pdf = tmp_path / "book.pdf"
    doc = fitz.open()
    for _ in range(10):
        doc.new_page()
    doc.save(str(pdf))
    doc.close()

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="p1")

    new_dir = recognize_dir(ckpt, "p1", 0, pdf.stem)
    # 1-based: [1,5], [6,10]
    _touch(new_dir / "page_001-005.md", "a")
    _touch(new_dir / "page_006-010.md", "b")
    assert orch._is_cache_complete([pdf], state) is True


def test_is_cache_complete_pdf_0based_legacy(tmp_path: Path) -> None:
    pytest.importorskip("fitz")
    import fitz

    pdf = tmp_path / "legacy.pdf"
    doc = fitz.open()
    for _ in range(8):
        doc.new_page()
    doc.save(str(pdf))
    doc.close()

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="p2")

    # legacy: <hash>/vlm/page_NNN-NNN.md, 0-based
    legacy = state_dir(ckpt, "p2") / "vlm"
    legacy.mkdir(parents=True, exist_ok=True)
    _touch(legacy / "page_000-003.md", "a")
    _touch(legacy / "page_004-007.md", "b")
    # 应识别为 0-based 且完整覆盖 [0,7]（= 8 页）
    assert orch._is_cache_complete([pdf], state) is True


def test_is_cache_complete_pdf_incomplete(tmp_path: Path) -> None:
    pytest.importorskip("fitz")
    import fitz

    pdf = tmp_path / "partial.pdf"
    doc = fitz.open()
    for _ in range(10):
        doc.new_page()
    doc.save(str(pdf))
    doc.close()

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="p3")

    new_dir = recognize_dir(ckpt, "p3", 0, pdf.stem)
    # 只识别前 5 页，缺后 5 页
    _touch(new_dir / "page_001-005.md", "a")
    assert orch._is_cache_complete([pdf], state) is False


def test_is_cache_complete_pdf_gap_in_range(tmp_path: Path) -> None:
    pytest.importorskip("fitz")
    import fitz

    pdf = tmp_path / "gappy.pdf"
    doc = fitz.open()
    for _ in range(10):
        doc.new_page()
    doc.save(str(pdf))
    doc.close()

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="p4")

    new_dir = recognize_dir(ckpt, "p4", 0, pdf.stem)
    # [1,3] 和 [6,10]，中间 4-5 缺失
    _touch(new_dir / "page_001-003.md", "a")
    _touch(new_dir / "page_006-010.md", "b")
    assert orch._is_cache_complete([pdf], state) is False


def test_is_cache_complete_pdf_missing_file_returns_false(tmp_path: Path) -> None:
    """PDF 不存在且 state 没 task0_completed 时保守返回 False"""
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="p5")
    fake_pdf = tmp_path / "never-existed.pdf"

    new_dir = recognize_dir(ckpt, "p5", 0, fake_pdf.stem)
    _touch(new_dir / "page_001-005.md", "cached but pdf missing")
    assert orch._is_cache_complete([fake_pdf], state) is False


# ── _task0_replay_from_cache ────────────────────────


@pytest.mark.asyncio
async def test_replay_from_cache_pushes_blocks_in_order(tmp_path: Path) -> None:
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="rp1")
    pdf = tmp_path / "book.pdf"
    pdf.touch()  # 仅文件名被用

    new_dir = recognize_dir(ckpt, "rp1", 0, pdf.stem)
    _touch(new_dir / "page_001-005.md", "chunk A")
    _touch(new_dir / "page_006-010.md", "chunk B")
    _touch(new_dir / "page_011-015.md", "chunk C")

    queue: asyncio.Queue[object] = asyncio.Queue()
    from sciforge.pipeline.orchestrator import PipelineResult

    result = PipelineResult()

    await orch._task0_replay_from_cache([pdf], state, queue, result)

    # 期望 3 个 block + 1 个 SENTINEL
    items: list[Any] = []
    while not queue.empty():
        items.append(queue.get_nowait())

    assert len(items) == 4
    blocks = [m for m in items if isinstance(m, MdBlockSource)]
    assert len(blocks) == 3
    assert [b.md_text for b in blocks] == ["chunk A", "chunk B", "chunk C"]
    assert [b.block_index for b in blocks] == [0, 1, 2]
    assert items[-1] is SENTINEL
    assert result.blocks_pushed == 3
    assert state.task0_completed is True


@pytest.mark.asyncio
async def test_replay_skips_empty_md(tmp_path: Path) -> None:
    """空 md 被跳过（不推入 queue），但 SENTINEL 仍被推入"""
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)
    state = FileExtractionState(file_hash="rp2")
    pdf = tmp_path / "with_empty.pdf"
    pdf.touch()

    new_dir = recognize_dir(ckpt, "rp2", 0, pdf.stem)
    _touch(new_dir / "page_001-005.md", "good")
    _touch(new_dir / "page_006-010.md", "")  # 空
    _touch(new_dir / "page_011-015.md", "also good")

    queue: asyncio.Queue[object] = asyncio.Queue()
    from sciforge.pipeline.orchestrator import PipelineResult

    result = PipelineResult()
    await orch._task0_replay_from_cache([pdf], state, queue, result)

    items: list[Any] = []
    while not queue.empty():
        items.append(queue.get_nowait())

    blocks = [m for m in items if isinstance(m, MdBlockSource)]
    assert len(blocks) == 2
    assert [b.md_text for b in blocks] == ["good", "also good"]
    # block_index 连续：0, 1（空 md 不计数）
    assert [b.block_index for b in blocks] == [0, 1]


# ── 多 md 书本（logical_document = list[Path]）─────


@pytest.mark.asyncio
async def test_replay_multi_md_book_block_index_continuous(
    tmp_path: Path,
) -> None:
    """多 md 分卷作为一本书时，block_index 必须跨 file 全局连续"""
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)

    # 构造一本书的 3 个分卷 md（模拟 MarkdownRecognizer 产生的缓存）
    vol1 = tmp_path / "vol_part_1_10.md"
    vol2 = tmp_path / "vol_part_11_20.md"
    vol3 = tmp_path / "vol_part_21_30.md"
    vol1.write_text("vol1 text", encoding="utf-8")
    vol2.write_text("vol2 text", encoding="utf-8")
    vol3.write_text("vol3 text", encoding="utf-8")

    from sciforge.pipeline.resume_state import compute_file_hash

    file_hash = compute_file_hash([vol1, vol2, vol3])
    state = FileExtractionState(file_path=str(vol1), file_hash=file_hash, task0_completed=True)

    # 每个分卷在 recognize/file_NNN_<stem>/ 下有独立的 page_001-001.md
    # （模拟 MarkdownRecognizer 的单 batch 输出）
    for idx, vol in enumerate([vol1, vol2, vol3]):
        new_dir = recognize_dir(ckpt, file_hash, idx, vol.stem)
        _touch(new_dir / "page_001-001.md", f"cached {vol.stem}")

    queue: asyncio.Queue[object] = asyncio.Queue()
    from sciforge.pipeline.orchestrator import PipelineResult

    result = PipelineResult()
    await orch._task0_replay_from_cache([vol1, vol2, vol3], state, queue, result)

    items: list[Any] = []
    while not queue.empty():
        items.append(queue.get_nowait())

    blocks = [m for m in items if isinstance(m, MdBlockSource)]
    assert len(blocks) == 3
    # block_index 跨 file 连续
    assert [b.block_index for b in blocks] == [0, 1, 2]
    # 每个 block 的 file_path 是对应的源文件
    assert [Path(b.file_path).name for b in blocks] == [
        "vol_part_1_10.md",
        "vol_part_11_20.md",
        "vol_part_21_30.md",
    ]
    # 内容分别是对应 md 文件的缓存
    assert [b.md_text for b in blocks] == [
        "cached vol_part_1_10",
        "cached vol_part_11_20",
        "cached vol_part_21_30",
    ]
    assert result.blocks_pushed == 3


def test_is_cache_complete_multi_md_all_cached(tmp_path: Path) -> None:
    """多 md 分卷所有卷都有单 batch 缓存时，应识别为完整"""
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)

    vol1 = tmp_path / "vol_part_1_10.md"
    vol2 = tmp_path / "vol_part_11_20.md"
    vol1.write_text("v1", encoding="utf-8")
    vol2.write_text("v2", encoding="utf-8")

    from sciforge.pipeline.resume_state import compute_file_hash

    file_hash = compute_file_hash([vol1, vol2])
    state = FileExtractionState(file_hash=file_hash)

    # 无缓存 → False
    assert orch._is_cache_complete([vol1, vol2], state) is False

    # 只缓存卷 1 → 卷 2 不完整 → False
    d1 = recognize_dir(ckpt, file_hash, 0, vol1.stem)
    _touch(d1 / "page_001-001.md", "c1")
    assert orch._is_cache_complete([vol1, vol2], state) is False

    # 两卷都缓存 → True
    d2 = recognize_dir(ckpt, file_hash, 1, vol2.stem)
    _touch(d2 / "page_001-001.md", "c2")
    assert orch._is_cache_complete([vol1, vol2], state) is True


def test_is_cache_complete_multi_md_first_ok_second_missing(
    tmp_path: Path,
) -> None:
    """第一个分卷缓存完整但第二个分卷没缓存 → 整体不完整"""
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)

    vol1 = tmp_path / "a_part_1_5.md"
    vol2 = tmp_path / "a_part_6_10.md"
    vol1.write_text("v1", encoding="utf-8")
    vol2.write_text("v2", encoding="utf-8")

    from sciforge.pipeline.resume_state import compute_file_hash

    file_hash = compute_file_hash([vol1, vol2])
    state = FileExtractionState(file_hash=file_hash)

    # 只给 vol1 缓存
    d1 = recognize_dir(ckpt, file_hash, 0, vol1.stem)
    _touch(d1 / "page_001-001.md", "c1")

    assert orch._is_cache_complete([vol1, vol2], state) is False


def test_compute_file_hash_multi_md_stable_ordering(tmp_path: Path) -> None:
    """多 md 书本的 hash 对同一顺序稳定，不同顺序不同"""
    from sciforge.pipeline.resume_state import compute_file_hash

    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    c = tmp_path / "c.md"
    a.write_text("alpha", encoding="utf-8")
    b.write_text("beta", encoding="utf-8")
    c.write_text("gamma", encoding="utf-8")

    h1 = compute_file_hash([a, b, c])
    h2 = compute_file_hash([a, b, c])
    assert h1 == h2  # 稳定

    h3 = compute_file_hash([c, b, a])
    assert h1 != h3  # 顺序敏感


@pytest.mark.asyncio
async def test_process_directory_reprocesses_stale_completed_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    md = raw / "chap.md"
    md.write_text("hello", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)

    from sciforge.pipeline.resume_state import compute_file_hash, save_state

    file_hash = compute_file_hash(md)
    orch._save_directory_progress(
        raw,
        DirectoryProgress(dir_path=str(raw), completed_files={str(md): file_hash}),
    )
    save_state(
        ckpt,
        FileExtractionState(
            file_path=str(md),
            file_hash=file_hash,
            task0_completed=True,
            current_block_idx=1,
        ),
    )

    calls: list[str] = []
    from sciforge.pipeline.orchestrator import PipelineResult

    async def fake_process_file(item: Path | list[Path], **_kwargs: object) -> PipelineResult:
        calls.append(str(item[0]) if isinstance(item, list) else str(item))
        return PipelineResult(file_path=str(md), file_hash=file_hash, completed=True)

    monkeypatch.setattr(orch, "process_file", fake_process_file)

    results = await orch.process_directory(raw, max_concurrent_files=1)

    assert len(results) == 1
    assert calls == [str(md)]
    progress = orch._load_directory_progress(raw)
    assert progress.completed_files[str(md)] == file_hash


@pytest.mark.asyncio
async def test_process_directory_does_not_mark_incomplete_as_completed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    md = raw / "chap.md"
    md.write_text("hello", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)

    from sciforge.pipeline.resume_state import compute_file_hash

    file_hash = compute_file_hash(md)
    from sciforge.pipeline.orchestrator import PipelineResult

    async def fake_process_file(_item: Path | list[Path], **_kwargs: object) -> PipelineResult:
        return PipelineResult(file_path=str(md), file_hash=file_hash, completed=False)

    monkeypatch.setattr(orch, "process_file", fake_process_file)

    results = await orch.process_directory(raw, max_concurrent_files=1)

    assert len(results) == 1
    assert results[0].completed is False
    progress = orch._load_directory_progress(raw)
    assert str(md) not in progress.completed_files
    assert progress.failed_files[str(md)] == "incomplete"


@pytest.mark.asyncio
async def test_process_file_replays_pending_items_from_each_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)

    md = tmp_path / "resume.md"
    md.write_text("resume content", encoding="utf-8")

    from sciforge.pipeline.resume_state import compute_file_hash, recognize_dir, save_state

    file_hash = compute_file_hash(md)
    state = FileExtractionState(
        file_path=str(md),
        file_hash=file_hash,
        task0_completed=True,
        current_block_idx=1,
        line_offset=3,
        line_buffer_carry=["      4 | line 4\n"],
        next_item_id=4,
    )
    save_state(ckpt, state)

    rec_dir = recognize_dir(ckpt, file_hash, 0, md.stem)
    _touch(rec_dir / "page_001-001.md", "cached block\n")

    items_root = ckpt / file_hash / "items"
    for stage_dir, payload in (
        ("stage1", _mk_extract_payload(3, str(md))),
        ("stage1_review", _mk_reviewed_payload(2, str(md))),
        ("stage2", _mk_annotated_payload(1, str(md))),
    ):
        stage_path = items_root / stage_dir
        stage_path.mkdir(parents=True, exist_ok=True)
        (stage_path / f"item_{payload.item_id:06d}.json").write_text(
            payload.model_dump_json(indent=2),
            encoding="utf-8",
        )

    seen: dict[str, list[int]] = {
        "stage1_review": [],
        "stage2_annotate": [],
        "stage2_review": [],
        "stage3_persist": [],
    }

    class _DummyLLM:
        def enable_pipeline_logging(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _FakeStage1Extract:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def run(
            self,
            in_queue: asyncio.Queue[object],
            out_queue: asyncio.Queue[object],
            state: FileExtractionState,
        ) -> None:
            while True:
                msg = await in_queue.get()
                if msg is SENTINEL:
                    break
            state.stage1_completed = True
            await out_queue.put(SENTINEL)

    class _FakeStage1Review:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def run(
            self,
            in_queue: asyncio.Queue[object],
            out_queue: asyncio.Queue[object],
            state: FileExtractionState,
        ) -> None:
            while True:
                msg = await in_queue.get()
                if msg is SENTINEL:
                    break
                assert isinstance(msg, ExtractedBatch)
                for item_id, item in zip(msg.item_ids, msg.items, strict=True):
                    seen["stage1_review"].append(item_id)
                    state.stage1_reviewed_count += 1
                    await out_queue.put(
                        ReviewedItem(
                            item_id=item_id,
                            item=item,
                            block_index=msg.block_index,
                            source_file=msg.source_file,
                            page_start=msg.page_start,
                            page_end=msg.page_end,
                        )
                    )
            await out_queue.put(SENTINEL)

    class _FakeStage2Annotate:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def run(
            self,
            in_queue: asyncio.Queue[object],
            out_queue: asyncio.Queue[object],
            state: FileExtractionState,
        ) -> None:
            while True:
                msg = await in_queue.get()
                if msg is SENTINEL:
                    break
                assert isinstance(msg, ReviewedItem)
                seen["stage2_annotate"].append(msg.item_id)
                state.stage2_annotated_count += 1
                await out_queue.put(
                    AnnotatedItem(
                        item_id=msg.item_id,
                        item=msg.item,
                        annotation=_mk_annotation(msg.item_id),
                        block_index=msg.block_index,
                        source_file=msg.source_file,
                        page_start=msg.page_start,
                        page_end=msg.page_end,
                    )
                )
            await out_queue.put(SENTINEL)

    class _FakeStage2Review:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def run(
            self,
            in_queue: asyncio.Queue[object],
            out_queue: asyncio.Queue[object],
            state: FileExtractionState,
        ) -> None:
            while True:
                msg = await in_queue.get()
                if msg is SENTINEL:
                    break
                assert isinstance(msg, AnnotatedItem)
                seen["stage2_review"].append(msg.item_id)
                state.stage2_reviewed_count += 1
                await out_queue.put(
                    ReviewedAnnotatedItem(
                        item_id=msg.item_id,
                        item=msg.item,
                        annotation=msg.annotation,
                        block_index=msg.block_index,
                        source_file=msg.source_file,
                        page_start=msg.page_start,
                        page_end=msg.page_end,
                    )
                )
            await out_queue.put(SENTINEL)

    class _FakeStage3Persist:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def run(
            self,
            in_queue: asyncio.Queue[object],
            state: FileExtractionState,
        ) -> None:
            while True:
                msg = await in_queue.get()
                if msg is SENTINEL:
                    break
                assert isinstance(msg, ReviewedAnnotatedItem)
                seen["stage3_persist"].append(msg.item_id)
                state.stage3_persisted_count += 1
                state.last_persisted_item_id = msg.item_id
            state.stage2_completed = True
            state.stage3_completed = True

    monkeypatch.setattr("sciforge.pipeline.orchestrator.Stage1Extract", _FakeStage1Extract)
    monkeypatch.setattr("sciforge.pipeline.orchestrator.Stage1Review", _FakeStage1Review)
    monkeypatch.setattr("sciforge.pipeline.orchestrator.Stage2Annotate", _FakeStage2Annotate)
    monkeypatch.setattr("sciforge.pipeline.orchestrator.Stage2Review", _FakeStage2Review)
    monkeypatch.setattr("sciforge.pipeline.orchestrator.Stage3Persist", _FakeStage3Persist)

    orch = PipelineOrchestrator(
        config=_FakeConfig(ckpt),  # type: ignore[arg-type]
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
    )

    result = await orch.process_file(md)

    assert seen["stage1_review"] == [3]
    assert seen["stage2_annotate"] == [2, 3]
    assert seen["stage2_review"] == [1, 2, 3]
    assert seen["stage3_persist"] == [1, 2, 3]
    assert result.completed is True
    assert result.items_saved == 3


# ── drain consumer（--skip-llm 时防止 producer 死锁）─


@pytest.mark.asyncio
async def test_drain_queue_chain_does_not_deadlock(tmp_path: Path) -> None:
    """无 LLM 场景下，drain chain 必须把 source 吃干净，再把 SENTINEL 转发给下游"""
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)

    q_md: asyncio.Queue[object] = asyncio.Queue(maxsize=4)
    q_raw: asyncio.Queue[object] = asyncio.Queue()
    q_rev: asyncio.Queue[object] = asyncio.Queue()

    async def producer() -> None:
        for i in range(10):
            await q_md.put(
                MdBlockSource(
                    file_path="/tmp/x.md",
                    page_start=i,
                    page_end=i,
                    md_path="/tmp/x.md",
                    md_text=f"block{i}",
                    block_index=i,
                )
            )
        await q_md.put(SENTINEL)

    prod = asyncio.create_task(producer())
    drain = asyncio.create_task(orch._drain_queue_chain(q_md, [q_raw, q_rev]))

    await asyncio.wait_for(asyncio.gather(prod, drain), timeout=2.0)
    # 下游应该都收到了 SENTINEL
    assert q_raw.get_nowait() is SENTINEL
    assert q_rev.get_nowait() is SENTINEL


@pytest.mark.asyncio
async def test_drain_queue_chain_handles_empty_stream(tmp_path: Path) -> None:
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    orch = _make_orchestrator(ckpt)

    q_md: asyncio.Queue[object] = asyncio.Queue()
    q_raw: asyncio.Queue[object] = asyncio.Queue()
    await q_md.put(SENTINEL)
    await asyncio.wait_for(orch._drain_queue_chain(q_md, [q_raw]), timeout=1.0)
    assert q_raw.get_nowait() is SENTINEL
