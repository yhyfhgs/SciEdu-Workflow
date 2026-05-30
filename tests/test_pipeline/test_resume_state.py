"""Tests for resume_state module."""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import FileExtractionState
from sciforge.pipeline.resume_state import (
    compute_file_hash,
    enum_cached_batches_for_file,
    is_state_complete,
    load_state,
    recognize_dir,
    save_state,
    state_dir,
)


@pytest.fixture
def checkpoint_root(tmp_path: Path) -> Path:
    root = tmp_path / "checkpoint"
    root.mkdir()
    return root


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── compute_file_hash ────────────────────────────────


def test_compute_file_hash_single_file_stable(tmp_path: Path) -> None:
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello world")
    h1 = compute_file_hash(f)
    h2 = compute_file_hash(f)
    assert h1 == h2
    assert len(h1) == 16


def test_compute_file_hash_differs_by_content(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"alpha")
    b.write_bytes(b"beta")
    assert compute_file_hash(a) != compute_file_hash(b)


def test_compute_file_hash_multi_file_order_matters(tmp_path: Path) -> None:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"alpha")
    b.write_bytes(b"beta")
    h_ab = compute_file_hash([a, b])
    h_ba = compute_file_hash([b, a])
    assert h_ab != h_ba


# ── state save/load ──────────────────────────────────


def test_save_and_load_state_roundtrip(checkpoint_root: Path) -> None:
    state = FileExtractionState(
        file_path="/tmp/foo.pdf",
        file_hash="abc123",
        task0_completed=True,
        vlm_batches_done=[(0, 1, 5), (0, 6, 10)],
        current_block_idx=3,
        line_offset=500,
        line_buffer_carry=["    501 | hello"],
        next_item_id=42,
        tag_tree_json="{}",
        stage3_persisted_count=40,
    )
    save_state(checkpoint_root, state)

    loaded = load_state(checkpoint_root, "abc123")
    assert loaded is not None
    assert loaded.task0_completed is True
    assert loaded.vlm_batches_done == [(0, 1, 5), (0, 6, 10)]
    assert loaded.current_block_idx == 3
    assert loaded.line_offset == 500
    assert loaded.line_buffer_carry == ["    501 | hello"]
    assert loaded.next_item_id == 42
    assert loaded.stage3_persisted_count == 40


def test_load_state_missing_returns_none(checkpoint_root: Path) -> None:
    assert load_state(checkpoint_root, "nonexistent") is None


def test_load_state_autofixes_completed_phase(checkpoint_root: Path) -> None:
    state = FileExtractionState(
        file_path="/tmp/foo.pdf",
        file_hash="done123",
        phase="running",
        task0_completed=True,
        stage1_completed=True,
        stage3_completed=True,
        stage3_persisted_count=5,
    )
    save_state(checkpoint_root, state)

    loaded = load_state(checkpoint_root, "done123")
    assert loaded is not None
    assert loaded.phase == "completed"
    assert is_state_complete(loaded) is True

    reloaded = load_state(checkpoint_root, "done123")
    assert reloaded is not None
    assert reloaded.phase == "completed"


# ── enum_cached_batches_for_file ──────────────────────


def test_enum_new_format_basic(checkpoint_root: Path) -> None:
    file_hash = "h1"
    file_stem = "book"
    new_dir = recognize_dir(checkpoint_root, file_hash, 0, file_stem)
    _touch(new_dir / "page_001-005.md", "content1")
    _touch(new_dir / "page_006-010.md", "content2")

    batches = enum_cached_batches_for_file(checkpoint_root, file_hash, 0, file_stem)
    assert [(ps, pe) for ps, pe, _ in batches] == [(1, 5), (6, 10)]


def test_enum_sorted_by_page_start(checkpoint_root: Path) -> None:
    """文件系统 glob 顺序不稳定，结果必须排序"""
    file_hash = "h1"
    file_stem = "book"
    new_dir = recognize_dir(checkpoint_root, file_hash, 0, file_stem)
    # 乱序创建
    _touch(new_dir / "page_006-010.md", "b")
    _touch(new_dir / "page_001-005.md", "a")
    _touch(new_dir / "page_011-015.md", "c")

    batches = enum_cached_batches_for_file(checkpoint_root, file_hash, 0, file_stem)
    assert [(ps, pe) for ps, pe, _ in batches] == [(1, 5), (6, 10), (11, 15)]


def test_enum_legacy_format_fallback(checkpoint_root: Path) -> None:
    """legacy: <hash>/vlm/page_NNN-NNN.md（0-based）"""
    file_hash = "legacy1"
    legacy_dir = state_dir(checkpoint_root, file_hash) / "vlm"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    _touch(legacy_dir / "page_000-007.md", "chunk0")
    _touch(legacy_dir / "page_008-015.md", "chunk1")

    batches = enum_cached_batches_for_file(checkpoint_root, file_hash, 0, "whatever")
    assert [(ps, pe) for ps, pe, _ in batches] == [(0, 7), (8, 15)]


def test_enum_new_format_takes_priority_over_legacy(checkpoint_root: Path) -> None:
    """如果新目录有内容，不扫 legacy"""
    file_hash = "h2"
    file_stem = "book"
    new_dir = recognize_dir(checkpoint_root, file_hash, 0, file_stem)
    _touch(new_dir / "page_001-005.md", "new1")

    legacy_dir = state_dir(checkpoint_root, file_hash) / "vlm"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    _touch(legacy_dir / "page_000-099.md", "legacy-should-not-appear")

    batches = enum_cached_batches_for_file(checkpoint_root, file_hash, 0, file_stem)
    assert [(ps, pe) for ps, pe, _ in batches] == [(1, 5)]


def test_enum_legacy_ignored_when_file_idx_nonzero(checkpoint_root: Path) -> None:
    """legacy 不支持多分卷，file_idx != 0 时不应扫 legacy"""
    file_hash = "h3"
    legacy_dir = state_dir(checkpoint_root, file_hash) / "vlm"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    _touch(legacy_dir / "page_000-007.md", "legacy")

    batches = enum_cached_batches_for_file(checkpoint_root, file_hash, 1, "someothervolume")
    assert batches == []


def test_enum_empty_returns_empty_list(checkpoint_root: Path) -> None:
    assert enum_cached_batches_for_file(checkpoint_root, "noexist", 0, "x") == []


def test_enum_includes_zero_size_md(checkpoint_root: Path) -> None:
    """空 md 也要被计入（连续性判断需要）"""
    file_hash = "h4"
    file_stem = "book"
    new_dir = recognize_dir(checkpoint_root, file_hash, 0, file_stem)
    _touch(new_dir / "page_001-005.md", "content")
    _touch(new_dir / "page_006-010.md", "")  # empty

    batches = enum_cached_batches_for_file(checkpoint_root, file_hash, 0, file_stem)
    assert len(batches) == 2


def test_enum_ignores_malformed_filenames(checkpoint_root: Path) -> None:
    file_hash = "h5"
    file_stem = "book"
    new_dir = recognize_dir(checkpoint_root, file_hash, 0, file_stem)
    _touch(new_dir / "page_001-005.md", "good")
    _touch(new_dir / "not_a_page.md", "noise")
    _touch(new_dir / "page_abc-xyz.md", "noise2")

    batches = enum_cached_batches_for_file(checkpoint_root, file_hash, 0, file_stem)
    assert [(ps, pe) for ps, pe, _ in batches] == [(1, 5)]
