"""schema v9 生成元数据字段 + v8→v9 增量迁移测试

验证：
- 生成元数据字段（is_generated/generator_model/generated_at/generation_session_id/tenant_id）
  能正确 save → load 往返
- 旧 v8 库打开时自动增量 ALTER 升级到 v9（不删库、保留旧数据）
- 迁移幂等（重复打开不报错）
- v8 之前的旧库无迁移路径 → 仍抛 SchemaVersionMismatch（保留既有行为）
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from sciforge.core.models import Discipline, Question
from sciforge.storage.sqlite_backend import SchemaVersionMismatch, SQLiteStorageBackend

# v8 questions 表定义（无生成元数据列），用于构造"旧库"
_V8_QUESTIONS = """
CREATE TABLE questions (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL DEFAULT 'math',
    question_type TEXT NOT NULL DEFAULT 'proof',
    question_form TEXT DEFAULT '',
    number TEXT DEFAULT '',
    number_canonical TEXT DEFAULT '',
    seq_chapter INTEGER, seq_section INTEGER, seq_index INTEGER,
    content TEXT NOT NULL DEFAULT '',
    solution_content TEXT,
    difficulty INTEGER NOT NULL DEFAULT 5,
    subject TEXT NOT NULL DEFAULT '',
    taxonomy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    source_file TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT DEFAULT ''
);
"""

# v8 knowledge_points 表（v9 未改 kp 表）；需完整列，否则 schema.sql 的 idx_kp_* 索引建不出
_V8_KP = """
CREATE TABLE knowledge_points (
    id TEXT PRIMARY KEY,
    discipline TEXT NOT NULL DEFAULT 'math',
    kp_type TEXT NOT NULL DEFAULT 'theorem',
    number TEXT DEFAULT '',
    number_canonical TEXT DEFAULT '',
    seq_chapter INTEGER, seq_section INTEGER, seq_index INTEGER,
    content TEXT NOT NULL DEFAULT '',
    proof_content TEXT,
    difficulty INTEGER NOT NULL DEFAULT 5,
    subject TEXT NOT NULL DEFAULT '',
    taxonomy_path TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    source_file TEXT NOT NULL DEFAULT '',
    start_line INTEGER NOT NULL DEFAULT 0,
    end_line INTEGER NOT NULL DEFAULT 0,
    order_index INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT DEFAULT ''
);
"""


def _make_old_db(path: Path, version: int) -> None:
    """构造一个指定 user_version 的旧库（含 v8 questions + knowledge_points 表 + 一条数据）"""
    conn = sqlite3.connect(str(path))
    # _init_schema 以 knowledge_points 表是否存在来判断"非空旧库"
    conn.executescript(_V8_QUESTIONS + _V8_KP)
    conn.execute(
        "INSERT INTO questions (id, discipline, question_type, content, difficulty) "
        "VALUES ('q-old', 'math', 'proof', '旧题内容', 5)"
    )
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()


def test_generation_metadata_roundtrip(tmp_path: Path) -> None:
    backend = SQLiteStorageBackend(tmp_path / "new.db")
    try:
        ts = datetime(2026, 6, 15, 12, 0, 0)
        q = Question(
            discipline=Discipline.MATH,
            question_type="proof",
            content="生成的题目内容，长度足够通过结构校验。",
            is_generated=True,
            generator_model="claude-opus-4-8",
            generated_at=ts,
            generation_session_id="sess-1",
            tenant_id="tenant-A",
        )
        backend.save_question(q)
        loaded = backend.load_question(q.id)
        assert loaded.is_generated is True
        assert loaded.generator_model == "claude-opus-4-8"
        assert loaded.generated_at == ts
        assert loaded.generation_session_id == "sess-1"
        assert loaded.tenant_id == "tenant-A"
    finally:
        backend.close()


def test_defaults_for_non_generated(tmp_path: Path) -> None:
    backend = SQLiteStorageBackend(tmp_path / "new.db")
    try:
        q = Question(discipline=Discipline.MATH, content="手动录入的题目内容足够长。")
        backend.save_question(q)
        loaded = backend.load_question(q.id)
        assert loaded.is_generated is False
        assert loaded.generator_model == ""
        assert loaded.generated_at is None
        assert loaded.generation_session_id == ""
        assert loaded.tenant_id == ""
    finally:
        backend.close()


def test_v8_to_v9_incremental_migration(tmp_path: Path) -> None:
    """旧 v8 库打开 → 自动 ALTER 链式升级到当前版本(v10)，不删库、保留旧数据"""
    db = tmp_path / "v8.db"
    _make_old_db(db, version=8)

    backend = SQLiteStorageBackend(db)
    try:
        assert backend._conn.execute("PRAGMA user_version").fetchone()[0] == 10
        # 旧数据保留，新列以默认值可读
        old = backend.load_question("q-old")
        assert old.content == "旧题内容"
        assert old.is_generated is False
        assert old.tenant_id == ""
        # REC-19：v10 结构化题型列迁移后可写可读
        assert old.options == []
        assert old.correct_option is None
        # 迁移后能写入并读回带生成元数据的新题
        q = Question(
            discipline=Discipline.MATH,
            content="迁移后写入的新题内容足够长。",
            is_generated=True,
            tenant_id="t1",
        )
        backend.save_question(q)
        assert backend.load_question(q.id).tenant_id == "t1"
    finally:
        backend.close()


def test_migration_idempotent_rerun(tmp_path: Path) -> None:
    """已迁移到当前版本的库再次打开不报错、版本仍为 v10"""
    db = tmp_path / "v8.db"
    _make_old_db(db, version=8)
    SQLiteStorageBackend(db).close()  # 首次迁移
    backend = SQLiteStorageBackend(db)  # 二次打开
    try:
        assert backend._conn.execute("PRAGMA user_version").fetchone()[0] == 10
    finally:
        backend.close()


def test_pre_v8_still_requires_restart(tmp_path: Path) -> None:
    """v8 之前的旧库无迁移路径 → 抛 SchemaVersionMismatch（保留既有行为）"""
    db = tmp_path / "v7.db"
    _make_old_db(db, version=7)
    with pytest.raises(SchemaVersionMismatch):
        SQLiteStorageBackend(db)
