"""schema v14 生成元数据、可追溯字段与增量迁移测试。

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

from sciforge.core.models import Discipline, KnowledgePoint, Question
from sciforge.pipeline.evaluation import evaluate_item_rows
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
    """旧 v8 库打开 → 自动链式升级到当前版本(v14)，不删库、保留旧数据。"""
    db = tmp_path / "v8.db"
    _make_old_db(db, version=8)

    backend = SQLiteStorageBackend(db)
    try:
        assert backend._conn.execute("PRAGMA user_version").fetchone()[0] == 14
        # 旧数据保留，新列以默认值可读
        old = backend.load_question("q-old")
        assert old.content == "旧题内容"
        assert old.is_generated is False
        assert old.tenant_id == ""
        # REC-19：v10 结构化题型列迁移后可写可读
        assert old.options == []
        assert old.correct_option is None
        # v11 §F：字符级溯源列迁移后旧行取默认值（行级）
        assert old.start_char == 0
        assert old.end_char == 0
        assert old.page is None
        assert old.ingestion_status == "accepted"
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
    """已迁移到当前版本的库再次打开不报错、版本仍为 v14。"""
    db = tmp_path / "v8.db"
    _make_old_db(db, version=8)
    SQLiteStorageBackend(db).close()  # 首次迁移
    backend = SQLiteStorageBackend(db)  # 二次打开
    try:
        assert backend._conn.execute("PRAGMA user_version").fetchone()[0] == 14
    finally:
        backend.close()


def test_v10_to_v11_char_span_migration(tmp_path: Path) -> None:
    """v10 库（含 v9/v10 全部列）→ v11 只加字符级溯源列（M1 段，R4）"""
    db = tmp_path / "v10.db"
    _make_old_db(db, version=8)
    SQLiteStorageBackend(db).close()  # 先升满
    # 人工降回 v10 并去掉 v11 列不可行（SQLite DROP COLUMN 限制多），
    # 改为直接验证：v10 段迁移语句幂等（重复执行容忍 duplicate column）
    backend = SQLiteStorageBackend(db)
    try:
        from sciforge.storage.sqlite_backend import _MIGRATIONS

        for stmt in _MIGRATIONS[10]:
            try:
                backend._conn.execute(stmt)
            except Exception as exc:
                assert "duplicate column" in str(exc).lower()
        cols_kp = {r[1] for r in backend._conn.execute("PRAGMA table_info(knowledge_points)")}
        cols_q = {r[1] for r in backend._conn.execute("PRAGMA table_info(questions)")}
        assert {"start_char", "end_char", "page"} <= cols_kp
        assert {"start_char", "end_char", "page"} <= cols_q
    finally:
        backend.close()


def test_v11_to_v12_concept_graph_migration(tmp_path: Path) -> None:
    """M5 迁移创建四张 concept 表及关键索引，重复执行保持幂等。"""
    db = tmp_path / "v11.db"
    _make_old_db(db, version=8)
    backend = SQLiteStorageBackend(db)
    try:
        tables = {
            row[0]
            for row in backend._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"concepts", "concept_aliases", "item_concepts", "concept_relations"} <= tables
        indexes = {
            row[0]
            for row in backend._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_concept_discipline_branch" in indexes
        assert "idx_item_concept_concept" in indexes
        assert backend._conn.execute("PRAGMA user_version").fetchone()[0] == 14
    finally:
        backend.close()


def test_v12_to_v13_evaluation_migration(tmp_path: Path) -> None:
    """M6 迁移创建评价/复核表与质量门禁索引。"""
    backend = SQLiteStorageBackend(tmp_path / "evaluation.db")
    try:
        tables = {
            row[0]
            for row in backend._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"item_evaluations", "review_queue"} <= tables
        indexes = {
            row[0]
            for row in backend._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_eval_quality_gate" in indexes
        assert "idx_review_queue_work" in indexes
        assert backend._conn.execute("PRAGMA user_version").fetchone()[0] == 14
    finally:
        backend.close()


def test_v13_to_v14_ingestion_status_migration(tmp_path: Path) -> None:
    """v13 旧数据保留并默认 accepted，两个表与索引都完成升级。"""
    from sciforge.storage.sqlite_backend import _MIGRATIONS

    db = tmp_path / "v13.db"
    _make_old_db(db, version=8)
    conn = sqlite3.connect(str(db))
    for version in range(8, 13):
        for statement in _MIGRATIONS[version]:
            conn.execute(statement)
    conn.execute("PRAGMA user_version = 13")
    conn.commit()
    conn.close()

    backend = SQLiteStorageBackend(db)
    try:
        assert backend._conn.execute("PRAGMA user_version").fetchone()[0] == 14
        assert backend.load_question("q-old").ingestion_status == "accepted"
        kp_columns = {
            row[1] for row in backend._conn.execute("PRAGMA table_info(knowledge_points)")
        }
        q_columns = {row[1] for row in backend._conn.execute("PRAGMA table_info(questions)")}
        assert "ingestion_status" in kp_columns
        assert "ingestion_status" in q_columns
        indexes = {
            row[0]
            for row in backend._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert {"idx_kp_ingestion_status", "idx_q_ingestion_status"} <= indexes
        assert "idx_q_hash_tenant_uniq" in indexes
        assert "idx_q_hash_uniq" not in indexes
    finally:
        backend.close()


def test_char_span_roundtrip(tmp_path: Path) -> None:
    """v11 字符级溯源字段 save → load 往返（KP 与 Question）"""
    backend = SQLiteStorageBackend(tmp_path / "new.db")
    try:
        kp = KnowledgePoint(
            discipline=Discipline.MATH,
            kp_type="theorem",
            content="定理内容：连续函数在闭区间上有界。",
            start_line=10,
            end_line=12,
            start_char=3,
            end_char=25,
            page=181,
        )
        backend.save_knowledge_point(kp)
        loaded_kp = backend.load_knowledge_point(kp.id)
        assert (loaded_kp.start_char, loaded_kp.end_char, loaded_kp.page) == (3, 25, 181)

        q = Question(
            discipline=Discipline.MATH,
            content="题目内容：证明该定理的推论成立。",
            start_char=7,
            end_char=40,
            page=182,
        )
        backend.save_question(q)
        loaded_q = backend.load_question(q.id)
        assert (loaded_q.start_char, loaded_q.end_char, loaded_q.page) == (7, 40, 182)
    finally:
        backend.close()


def test_ingestion_status_roundtrip(tmp_path: Path) -> None:
    """v14 摄取状态在 KP/Question 中持久化且受数据库 CHECK 约束。"""
    backend = SQLiteStorageBackend(tmp_path / "status.db")
    try:
        kp = KnowledgePoint(content="边界不完整的定理片段。", ingestion_status="needs_patch")
        question = Question(content="待人工复核的题目片段。", ingestion_status="uncertain")
        backend.save_batch([kp], [question])

        assert backend.load_knowledge_point(kp.id).ingestion_status == "needs_patch"
        assert backend.load_question(question.id).ingestion_status == "uncertain"
        with pytest.raises(sqlite3.IntegrityError):
            backend._conn.execute(
                "UPDATE questions SET ingestion_status = 'invented' WHERE id = ?",
                (question.id,),
            )
    finally:
        backend.close()


def test_accepted_correction_promotes_placeholder_without_changing_id(tmp_path: Path) -> None:
    backend = SQLiteStorageBackend(tmp_path / "promotion.db")
    try:
        placeholder = Question(
            id="stable-id",
            content="证明函数在该区间上一致连续。",
            solution_content=None,
            ingestion_status="uncertain",
            tenant_id="tenant-a",
        )
        backend.save_question(placeholder)
        backend.save_evaluation_batch(
            evaluate_item_rows(backend.evaluation_rows(placeholder.source_file or ""))
        )
        assert any(
            item.reason == "non_accepted_ingestion_status"
            for item in backend.load_review_queue(tenant_id="tenant-a")
        )
        correction = Question(
            id="discarded-new-id",
            content="证明函数在该区间上一致连续。",
            solution_content="由紧致性取有限子覆盖，取最小半径即得。",
            taxonomy_path="math.analysis.continuity.uniform_continuity",
            tags=["math:数学", "analysis:分析"],
            ingestion_status="accepted",
            tenant_id="tenant-a",
        )

        canonical_id = backend.save_question(correction)

        assert canonical_id == "stable-id"
        assert backend.question_count() == 1
        promoted = backend.load_question("stable-id")
        assert promoted.ingestion_status == "accepted"
        assert promoted.solution_content == correction.solution_content
        assert promoted.taxonomy_path == correction.taxonomy_path
        assert not any(
            item.reason == "non_accepted_ingestion_status"
            for item in backend.load_review_queue(tenant_id="tenant-a")
        )
        with pytest.raises(FileNotFoundError):
            backend.load_question("discarded-new-id")
    finally:
        backend.close()


def test_future_schema_is_rejected_without_downgrade(tmp_path: Path) -> None:
    db = tmp_path / "future.db"
    SQLiteStorageBackend(db).close()
    conn = sqlite3.connect(str(db))
    conn.execute("PRAGMA user_version = 15")
    conn.commit()
    conn.close()

    with pytest.raises(SchemaVersionMismatch, match="未来 DB schema v15"):
        SQLiteStorageBackend(db)

    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 15
    finally:
        conn.close()


def test_nonempty_v0_schema_is_rejected_clearly(tmp_path: Path) -> None:
    db = tmp_path / "v0.db"
    _make_old_db(db, version=0)
    with pytest.raises(SchemaVersionMismatch, match="非空 DB schema v0"):
        SQLiteStorageBackend(db)


def test_pre_v8_still_requires_restart(tmp_path: Path) -> None:
    """v8 之前的旧库无迁移路径 → 抛 SchemaVersionMismatch（保留既有行为）"""
    db = tmp_path / "v7.db"
    _make_old_db(db, version=7)
    with pytest.raises(SchemaVersionMismatch):
        SQLiteStorageBackend(db)
