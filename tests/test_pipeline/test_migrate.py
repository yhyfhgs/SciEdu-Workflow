"""Tests for pipeline.migrate module."""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import FileExtractionState
from sciforge.pipeline.migrate import (
    _enum_legacy_mds,
    build_hash_to_file_map,
    discover_legacy_checkpoints,
    migrate_legacy_checkpoint,
)
from sciforge.pipeline.resume_state import (
    compute_file_hash,
    load_state,
    recognize_dir,
    save_state,
    state_dir,
)


@pytest.fixture
def pdf_file(tmp_path: Path) -> Path:
    """一个真 PDF，用 fitz 创建；失败时跳过测试"""
    pytest.importorskip("fitz")
    import fitz

    pdf = tmp_path / "raw" / "book.pdf"
    pdf.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(16):
        doc.new_page()
    doc.save(str(pdf))
    doc.close()
    return pdf


def _touch(p: Path, content: str = "data") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _setup_legacy(
    checkpoint_dir: Path,
    file_hash: str,
    batches: list[tuple[int, int, str]],
) -> Path:
    """在 <checkpoint>/<hash>/vlm/ 创建 legacy batch 文件"""
    vlm = state_dir(checkpoint_dir, file_hash) / "vlm"
    vlm.mkdir(parents=True, exist_ok=True)
    for ps, pe, content in batches:
        _touch(vlm / f"page_{ps:03d}-{pe:03d}.md", content)
    return vlm


# ── discover_legacy_checkpoints ────────────────────


def test_discover_empty_checkpoint_dir(tmp_path: Path) -> None:
    assert discover_legacy_checkpoints(tmp_path / "nothere") == []


def test_discover_finds_only_vlm_with_pages(tmp_path: Path) -> None:
    ckpt = tmp_path / "ckpt"
    # has pages
    _setup_legacy(ckpt, "h1", [(0, 7, "a")])
    # empty vlm dir (no page_*.md)
    (state_dir(ckpt, "h2") / "vlm").mkdir(parents=True, exist_ok=True)
    # has new-format only (no vlm)
    new_only = recognize_dir(ckpt, "h3", 0, "x")
    _touch(new_only / "page_001-005.md", "new")

    results = discover_legacy_checkpoints(ckpt)
    hashes = [h for h, _ in results]
    assert hashes == ["h1"]


# ── _enum_legacy_mds ──────────────────────────────


def test_enum_legacy_sorted(tmp_path: Path) -> None:
    vlm = tmp_path / "vlm"
    vlm.mkdir()
    _touch(vlm / "page_008-015.md", "b")
    _touch(vlm / "page_000-007.md", "a")
    _touch(vlm / "garbage.md", "noise")

    entries = _enum_legacy_mds(vlm)
    assert [(ps, pe) for ps, pe, _ in entries] == [(0, 7), (8, 15)]


# ── build_hash_to_file_map ─────────────────────────


def test_build_hash_map_matches_pdf(pdf_file: Path) -> None:
    raw_dir = pdf_file.parent
    mapping = build_hash_to_file_map(raw_dir)
    h = compute_file_hash(pdf_file)
    assert mapping[h] == pdf_file


def test_build_hash_map_ignores_non_pdf(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    md = raw / "chap.md"
    md.write_text("foo", encoding="utf-8")
    mapping = build_hash_to_file_map(raw)
    assert mapping == {}


# ── migrate_legacy_checkpoint ──────────────────────


def test_migrate_basic_0based_to_1based(tmp_path: Path, pdf_file: Path) -> None:
    ckpt = tmp_path / "ckpt"
    file_hash = compute_file_hash(pdf_file)
    # legacy 0-based：0-7, 8-15（共 16 页覆盖 PDF）
    _setup_legacy(
        ckpt,
        file_hash,
        [(0, 7, "chunk0"), (8, 15, "chunk1")],
    )

    results = migrate_legacy_checkpoint(checkpoint_dir=ckpt, raw_data_dir=pdf_file.parent)
    assert len(results) == 1
    r = results[0]
    assert r.status == "migrated"
    assert r.base_offset == 1  # 0-based → 需要 +1
    assert r.batches_migrated == 2

    # 新目录有迁移后的文件
    new_dir = recognize_dir(ckpt, file_hash, 0, pdf_file.stem)
    assert (new_dir / "page_001-008.md").exists()
    assert (new_dir / "page_009-016.md").exists()
    assert (new_dir / "page_001-008.md").read_text(encoding="utf-8") == "chunk0"
    assert (new_dir / "page_009-016.md").read_text(encoding="utf-8") == "chunk1"

    # state.json 被创建
    state = load_state(ckpt, file_hash)
    assert state is not None
    assert state.task0_completed is True
    assert state.vlm_batches_done == [(0, 1, 8), (0, 9, 16)]
    assert state.current_block_idx == 0
    assert state.saved_item_count == 0


def test_migrate_orphan_no_matching_file(tmp_path: Path) -> None:
    ckpt = tmp_path / "ckpt"
    raw = tmp_path / "raw"
    raw.mkdir()
    # 没有对应的 Raw Data PDF
    _setup_legacy(ckpt, "orphan_hash", [(0, 7, "a")])

    results = migrate_legacy_checkpoint(checkpoint_dir=ckpt, raw_data_dir=raw)
    assert len(results) == 1
    assert results[0].status == "orphan"


def test_migrate_dry_run_does_not_write(tmp_path: Path, pdf_file: Path) -> None:
    ckpt = tmp_path / "ckpt"
    file_hash = compute_file_hash(pdf_file)
    _setup_legacy(ckpt, file_hash, [(0, 7, "a"), (8, 15, "b")])

    results = migrate_legacy_checkpoint(
        checkpoint_dir=ckpt, raw_data_dir=pdf_file.parent, dry_run=True
    )
    assert results[0].status == "would_migrate"
    assert results[0].batches_migrated == 2

    # 新目录不应有文件
    new_dir = recognize_dir(ckpt, file_hash, 0, pdf_file.stem)
    assert not any(new_dir.glob("page_*.md"))
    # state.json 不被写
    assert load_state(ckpt, file_hash) is None


def test_migrate_idempotent_skips_when_new_has_files(tmp_path: Path, pdf_file: Path) -> None:
    ckpt = tmp_path / "ckpt"
    file_hash = compute_file_hash(pdf_file)
    _setup_legacy(ckpt, file_hash, [(0, 7, "a"), (8, 15, "b")])

    # 先跑一次
    r1 = migrate_legacy_checkpoint(checkpoint_dir=ckpt, raw_data_dir=pdf_file.parent)
    assert r1[0].status == "migrated"

    # 再跑应跳过
    r2 = migrate_legacy_checkpoint(checkpoint_dir=ckpt, raw_data_dir=pdf_file.parent)
    assert r2[0].status == "skipped"
    assert "already migrated" in r2[0].error


def test_migrate_force_overrides_idempotent_skip(tmp_path: Path, pdf_file: Path) -> None:
    ckpt = tmp_path / "ckpt"
    file_hash = compute_file_hash(pdf_file)
    _setup_legacy(ckpt, file_hash, [(0, 7, "a"), (8, 15, "b")])

    migrate_legacy_checkpoint(checkpoint_dir=ckpt, raw_data_dir=pdf_file.parent)

    # force=True 应重新迁移
    r2 = migrate_legacy_checkpoint(checkpoint_dir=ckpt, raw_data_dir=pdf_file.parent, force=True)
    assert r2[0].status == "migrated"


def test_migrate_skips_when_task1_has_progress(tmp_path: Path, pdf_file: Path) -> None:
    """如果 state 中 Task1 已有进度，不应被 migrate 清空"""
    ckpt = tmp_path / "ckpt"
    file_hash = compute_file_hash(pdf_file)
    _setup_legacy(ckpt, file_hash, [(0, 7, "a")])

    # 预先写一个有 Task1 进度的 state
    state = FileExtractionState(
        file_path=str(pdf_file),
        file_hash=file_hash,
        saved_item_count=42,
        current_block_idx=5,
    )
    save_state(ckpt, state)

    results = migrate_legacy_checkpoint(checkpoint_dir=ckpt, raw_data_dir=pdf_file.parent)
    assert results[0].status == "skipped"
    assert "Task1 has progress" in results[0].error

    # state 应保持不变
    after = load_state(ckpt, file_hash)
    assert after is not None
    assert after.saved_item_count == 42
    assert after.current_block_idx == 5


def test_migrate_delete_legacy_flag(tmp_path: Path, pdf_file: Path) -> None:
    ckpt = tmp_path / "ckpt"
    file_hash = compute_file_hash(pdf_file)
    legacy_dir = _setup_legacy(ckpt, file_hash, [(0, 7, "a"), (8, 15, "b")])
    assert legacy_dir.exists()

    migrate_legacy_checkpoint(
        checkpoint_dir=ckpt,
        raw_data_dir=pdf_file.parent,
        delete_legacy=True,
    )

    assert not legacy_dir.exists()


def test_migrate_preserves_empty_md_files(tmp_path: Path, pdf_file: Path) -> None:
    """空 md 文件应被迁移（保持连续性判断）"""
    ckpt = tmp_path / "ckpt"
    file_hash = compute_file_hash(pdf_file)
    _setup_legacy(
        ckpt,
        file_hash,
        [(0, 7, ""), (8, 15, "content"), (15, 15, "")],  # 两端都是空
    )

    results = migrate_legacy_checkpoint(checkpoint_dir=ckpt, raw_data_dir=pdf_file.parent)
    assert results[0].status == "migrated"

    new_dir = recognize_dir(ckpt, file_hash, 0, pdf_file.stem)
    assert (new_dir / "page_001-008.md").exists()
    assert (new_dir / "page_001-008.md").read_text(encoding="utf-8") == ""
    assert (new_dir / "page_009-016.md").read_text(encoding="utf-8") == "content"
    assert (new_dir / "page_016-016.md").read_text(encoding="utf-8") == ""
