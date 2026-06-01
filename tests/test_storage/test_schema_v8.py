"""Schema v8（Phase 0/1）：新字段往返 + 写入去重 + CHECK 约束 + 版本拦截"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sciforge.core.models import Discipline, KnowledgePoint, Question
from sciforge.storage.sqlite_backend import SchemaVersionMismatch, SQLiteStorageBackend


@pytest.fixture
def backend(tmp_path: Path) -> SQLiteStorageBackend:
    be = SQLiteStorageBackend(tmp_path / "v8.db")
    yield be
    be.close()


def test_new_fields_roundtrip(backend: SQLiteStorageBackend) -> None:
    q = Question(
        number="例 3.2",
        content="计算 $\\int_0^1 x^2 dx$",
        discipline=Discipline.MATH,
        question_form="choice",
    )
    backend.save_batch([], [q])
    got = backend.load_question(q.id)
    assert got.number_canonical == "3.2"
    assert got.seq_chapter == 3 and got.seq_index == 2
    assert got.question_form == "choice"


def test_kp_seq_roundtrip(backend: SQLiteStorageBackend) -> None:
    kp = KnowledgePoint(number="定理 3.1.2", content="设 f 连续")
    backend.save_batch([kp], [])
    got = backend.load_knowledge_point(kp.id)
    assert got.number_canonical == "3.1.2"
    assert (got.seq_chapter, got.seq_section, got.seq_index) == (3, 1, 2)


def test_write_time_dedup(backend: SQLiteStorageBackend) -> None:
    """相同内容（不同 id）重复入库 → content_hash UNIQUE 去重，仅留一行。"""
    q1 = Question(number="例 5", content="同一题内容 A", discipline=Discipline.MATH)
    q2 = Question(number="例 5", content="同一题内容 A", discipline=Discipline.MATH)
    assert q1.id != q2.id
    backend.save_batch([], [q1])
    backend.save_batch([], [q2])
    assert backend.question_count() == 1


def test_find_duplicates(backend: SQLiteStorageBackend) -> None:
    q = Question(content="可去重内容", discipline=Discipline.MATH)
    backend.save_batch([], [q])
    assert backend.find_duplicates("可去重内容", "question")
    assert not backend.find_duplicates("不存在的内容", "question")


def test_discipline_check_constraint(backend: SQLiteStorageBackend) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        backend._conn.execute(
            "INSERT INTO questions(id,discipline,question_type,content) "
            "VALUES('x','astrology','proof','c')"
        )
        backend._conn.commit()


def test_numbering_rows(backend: SQLiteStorageBackend) -> None:
    kp = KnowledgePoint(
        number="定理 3.1", content="t", subject="analysis:分析", source_file="book.md"
    )
    backend.save_batch([kp], [])
    rows = backend.numbering_rows("book.md", "math")
    assert len(rows) == 1
    subject, ch, sec, idx, canon, kind, _id = rows[0]
    assert ch == 3 and idx == 1 and kind == "kp" and canon == "3.1"


def test_old_version_rejected(tmp_path: Path) -> None:
    old = tmp_path / "old.db"
    conn = sqlite3.connect(old)
    conn.execute("PRAGMA user_version = 7")
    conn.execute("CREATE TABLE knowledge_points (id TEXT PRIMARY KEY)")
    conn.commit()
    conn.close()
    with pytest.raises(SchemaVersionMismatch):
        SQLiteStorageBackend(old)
