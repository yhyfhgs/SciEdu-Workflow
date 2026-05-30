"""Tests for pipeline.audit module."""

from __future__ import annotations

from pathlib import Path

from sciforge.core.models import FileExtractionState
from sciforge.pipeline.audit import (
    FileAuditEntry,
    _is_batches_complete_for_pdf,
    audit_directory,
    format_audit_report,
)
from sciforge.pipeline.resume_state import (
    compute_file_hash,
    recognize_dir,
    save_state,
)


def _touch(p: Path, content: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ── _is_batches_complete_for_pdf ───────────────────


def test_batches_complete_1based_full() -> None:
    assert _is_batches_complete_for_pdf([(1, 5), (6, 10)], 10) is True


def test_batches_complete_0based_full() -> None:
    assert _is_batches_complete_for_pdf([(0, 4), (5, 9)], 10) is True


def test_batches_complete_1based_missing_tail() -> None:
    assert _is_batches_complete_for_pdf([(1, 5)], 10) is False


def test_batches_complete_1based_gap() -> None:
    assert _is_batches_complete_for_pdf([(1, 3), (6, 10)], 10) is False


def test_batches_complete_empty_returns_false() -> None:
    assert _is_batches_complete_for_pdf([], 10) is False


def test_batches_complete_wrong_base() -> None:
    """ps=2 既不是 0-based 也不是 1-based 的合理开头"""
    assert _is_batches_complete_for_pdf([(2, 6), (7, 10)], 10) is False


# ── audit_directory 完整流程 ────────────────────────


def test_audit_empty_raw_data(tmp_path: Path) -> None:
    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    db = tmp_path / "nonexistent.db"
    raw = tmp_path / "raw"
    raw.mkdir()
    entries = audit_directory(raw_data_dir=raw, checkpoint_dir=ckpt, db_path=db)
    assert entries == []


def test_audit_single_tex_no_checkpoint(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    tex = raw / "paper.tex"
    tex.write_text(r"\section{foo}", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()

    entries = audit_directory(raw_data_dir=raw, checkpoint_dir=ckpt, db_path=tmp_path / "x.db")
    assert len(entries) == 1
    e = entries[0]
    assert e.logical_name == "paper.tex"
    assert e.task0_status in ("no_checkpoint", "not_started")
    assert e.pipeline_status == "not_started"
    assert e.saved_items == 0


def test_audit_md_task0_done_via_cache_scan(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    md = raw / "chap.md"
    md.write_text("hello", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    file_hash = compute_file_hash(md)
    new_dir = recognize_dir(ckpt, file_hash, 0, md.stem)
    _touch(new_dir / "page_001-001.md", "hello")

    entries = audit_directory(raw_data_dir=raw, checkpoint_dir=ckpt, db_path=tmp_path / "x.db")
    assert len(entries) == 1
    e = entries[0]
    assert e.task0_status == "done"  # cache scan 识别为完整
    assert e.task0_batches_done == 1
    assert e.pipeline_status == "not_started"


def test_audit_task0_done_via_state_flag(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    md = raw / "chap.md"
    md.write_text("hello", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    file_hash = compute_file_hash(md)
    # state 设置 task0_completed=True，即使新目录没文件也应识别为 done
    save_state(
        ckpt,
        FileExtractionState(file_path=str(md), file_hash=file_hash, task0_completed=True),
    )

    entries = audit_directory(raw_data_dir=raw, checkpoint_dir=ckpt, db_path=tmp_path / "x.db")
    assert entries[0].task0_status == "done"


def test_audit_task1_in_progress_from_state(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    md = raw / "chap.md"
    md.write_text("hello", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    file_hash = compute_file_hash(md)
    save_state(
        ckpt,
        FileExtractionState(
            file_path=str(md),
            file_hash=file_hash,
            task0_completed=True,
            current_block_idx=1,
            stage3_persisted_count=7,
        ),
    )

    entries = audit_directory(raw_data_dir=raw, checkpoint_dir=ckpt, db_path=tmp_path / "x.db")
    e = entries[0]
    assert e.pipeline_status == "in_progress"
    assert e.saved_items == 7
    assert e.current_block == 1


def test_audit_task1_completed_phase(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    md = raw / "chap.md"
    md.write_text("hello", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()
    file_hash = compute_file_hash(md)
    save_state(
        ckpt,
        FileExtractionState(
            file_path=str(md),
            file_hash=file_hash,
            phase="completed",
            task0_completed=True,
            stage3_persisted_count=12,
        ),
    )

    entries = audit_directory(raw_data_dir=raw, checkpoint_dir=ckpt, db_path=tmp_path / "x.db")
    assert entries[0].pipeline_status == "done"
    assert entries[0].saved_items == 12


def test_audit_groups_part_files(tmp_path: Path) -> None:
    """_group_part_files 应把 _part_N_M.md 聚合成一个 logical_document"""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "book_part_1_10.md").write_text("vol1", encoding="utf-8")
    (raw / "book_part_11_20.md").write_text("vol2", encoding="utf-8")
    (raw / "book_part_21_30.md").write_text("vol3", encoding="utf-8")
    (raw / "other.md").write_text("standalone", encoding="utf-8")

    ckpt = tmp_path / "ckpt"
    ckpt.mkdir()

    entries = audit_directory(raw_data_dir=raw, checkpoint_dir=ckpt, db_path=tmp_path / "x.db")
    # 预期 2 个 logical_document：book (3 卷) + other (1 卷)
    assert len(entries) == 2
    book_entry = next(e for e in entries if "book" in e.logical_name)
    assert len(book_entry.files_in_doc) == 3
    assert "×3" in book_entry.logical_name
    other = next(e for e in entries if "other" in e.logical_name)
    assert len(other.files_in_doc) == 1


# ── format_audit_report ────────────────────────────


def test_format_report_contains_headers(tmp_path: Path) -> None:
    entries = [
        FileAuditEntry(
            logical_name="foo.md",
            files_in_doc=["foo.md"],
            file_hash="abc1234567890def",
            task0_status="done",
            task0_batches_done=1,
            task0_batches_expected=1,
            pipeline_status="not_started",
            saved_items=0,
        ),
    ]
    report = format_audit_report(entries, tmp_path, tmp_path / "ckpt", tmp_path / "db")
    assert "# Pipeline 进度盘点报告" in report
    assert "## 概览" in report
    assert "## 详细进度" in report
    assert "foo.md" in report
    assert "abc12345" in report  # hash 前 8 位


def test_format_report_escapes_pipe_in_name(tmp_path: Path) -> None:
    """logical_name 含 | 时需要转义，不破坏 markdown 表格"""
    entries = [
        FileAuditEntry(
            logical_name="weird | name.md",
            files_in_doc=["weird | name.md"],
            task0_status="done",
        )
    ]
    report = format_audit_report(entries, tmp_path, tmp_path / "ckpt", tmp_path / "db")
    # 应该转义：\|
    assert "weird \\| name.md" in report
