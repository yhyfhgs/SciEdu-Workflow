"""SQLite 存储后端 — SciForge 主存储

提供事务安全、FTS5 全文检索、索引查询、内容哈希去重。
实现 `BatchStorageBackend` Protocol。

并发约定：
- 每个 logical_document 处理时**构造自己的 SQLiteStorageBackend 实例**
  （见 orchestrator.process_file），因此不同协程不会共享 connection
- PRAGMA busy_timeout 确保 WAL 模式下并发写自动等待而非立即失败
- `_write_lock` 是防御锁，应对同一实例被异外并发调用的情况
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

from sciforge.core.models import (
    Discipline,
    KnowledgePoint,
    Question,
)

logger = structlog.get_logger()


# ── SQL 常量（executemany 批量写入复用）──────────────────

_KP_INSERT_SQL = """
INSERT OR REPLACE INTO knowledge_points
    (id, discipline, kp_type, number, content, proof_content, proof_methods,
     difficulty, subject, taxonomy_path, tags,
     source_file, source_chapter, source_section,
     start_line, end_line, order_index, page_start, page_end,
     quality_grade, content_hash)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_Q_INSERT_SQL = """
INSERT OR REPLACE INTO questions
    (id, discipline, question_type, number, content, solution_content, solution_methods,
     answer, difficulty, subject, taxonomy_path, tags,
     source_file, source_chapter, exam_type, exam_year,
     start_line, end_line, order_index, page_start, page_end,
     quality_grade, content_hash, is_generated, parent_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _content_hash(text: str) -> str:
    """LaTeX 内容归一化后取 SHA-256 前 32 位"""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


def _safe_json_list(row: sqlite3.Row, col: str) -> list[str]:
    """从 row 读 JSON 列；空或解析失败返回空 list"""
    raw = row[col]
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(x) for x in data] if isinstance(data, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def _kp_to_tuple(kp: KnowledgePoint) -> tuple[Any, ...]:
    """KP → executemany 参数元组（顺序与 _KP_INSERT_SQL 一致）"""
    return (
        kp.id,
        str(kp.discipline),
        str(kp.kp_type),
        kp.number,
        kp.content,
        kp.proof_content,
        json.dumps(kp.proof_methods, ensure_ascii=False),
        kp.difficulty,
        kp.subject,
        kp.taxonomy_path,
        json.dumps(kp.tags, ensure_ascii=False),
        kp.source_file,
        kp.source_chapter,
        kp.source_section,
        kp.start_line,
        kp.end_line,
        kp.order_index,
        kp.page_start,
        kp.page_end,
        kp.quality_grade,
        _content_hash(kp.content),
    )


def _question_to_tuple(q: Question) -> tuple[Any, ...]:
    """Question → executemany 参数元组（顺序与 _Q_INSERT_SQL 一致）"""
    return (
        q.id,
        str(q.discipline),
        str(q.question_type),
        q.number,
        q.content,
        q.solution_content,
        json.dumps(q.solution_methods, ensure_ascii=False),
        q.answer,
        q.difficulty,
        q.subject,
        q.taxonomy_path,
        json.dumps(q.tags, ensure_ascii=False),
        q.source_file,
        q.source_chapter,
        q.exam_type,
        q.exam_year,
        q.start_line,
        q.end_line,
        q.order_index,
        q.page_start,
        q.page_end,
        q.quality_grade,
        _content_hash(q.content),
        int(q.is_generated),
        q.parent_id,
    )


class SQLiteStorageBackend:
    """基于 SQLite 的存储后端（单 connection，per-logical-document 实例）"""

    def __init__(self, db_path: Path | str, busy_timeout_ms: int = 5000) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL + busy_timeout 支持多进程/多实例并发写
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA cache_size = -64000")  # 64MB
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        # 防御锁：同一实例被 to_thread 并发调用时串行化
        self._write_lock = threading.Lock()
        self._in_transaction = False
        self._init_schema()

    def _init_schema(self) -> None:
        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        self._conn.executescript(schema_sql)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteStorageBackend:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    # ── 事务管理 ──────────────────────────────────────

    @contextmanager
    def transaction(self) -> Generator[None, None, None]:
        """将多次写操作包装在单一事务中，一次 commit"""
        self._in_transaction = True
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._in_transaction = False

    # ── 批量写入（主入口）─────────────────────────────

    def save_batch(
        self,
        kps: list[KnowledgePoint],
        questions: list[Question],
    ) -> int:
        """一次事务 + executemany 保存多个 KP + Question"""
        with self._write_lock, self.transaction():
            if kps:
                self._conn.executemany(_KP_INSERT_SQL, [_kp_to_tuple(kp) for kp in kps])
            if questions:
                self._conn.executemany(
                    _Q_INSERT_SQL, [_question_to_tuple(q) for q in questions]
                )
        logger.info("storage_batch_saved", kps=len(kps), questions=len(questions))
        return len(kps) + len(questions)

    # ── 单条写入（内部用 save_batch 统一路径）─────────

    def save_knowledge_point(self, kp: KnowledgePoint) -> str:
        self.save_batch([kp], [])
        return kp.id

    def save_question(self, question: Question) -> str:
        self.save_batch([], [question])
        return question.id

    # ── 读取 ──────────────────────────────────────────

    def load_knowledge_point(self, id: str) -> KnowledgePoint:
        row = self._conn.execute(
            "SELECT * FROM knowledge_points WHERE id = ?", (id,)
        ).fetchone()
        if not row:
            raise FileNotFoundError(f"KnowledgePoint {id} not found")
        return self._row_to_kp(row)

    def load_question(self, id: str) -> Question:
        row = self._conn.execute("SELECT * FROM questions WHERE id = ?", (id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"Question {id} not found")
        return self._row_to_question(row)

    def load_questions_by_ids(self, ids: list[str]) -> list[Question]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT * FROM questions WHERE id IN ({placeholders})",  # noqa: S608
            ids,
        ).fetchall()
        return [self._row_to_question(r) for r in rows]

    def load_knowledge_by_ids(self, ids: list[str]) -> list[KnowledgePoint]:
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(
            f"SELECT * FROM knowledge_points WHERE id IN ({placeholders})",  # noqa: S608
            ids,
        ).fetchall()
        return [self._row_to_kp(r) for r in rows]

    # ── 搜索 / 统计 ───────────────────────────────────

    def search_knowledge(self, filters: dict[str, object]) -> list[str]:
        return self._search("knowledge_points", filters)

    def search_questions(self, filters: dict[str, object]) -> list[str]:
        return self._search("questions", filters)

    def knowledge_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()
        return row[0] if row else 0

    def question_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM questions").fetchone()
        return row[0] if row else 0

    def stats(self) -> dict[str, int]:
        return {
            "knowledge_points": self.knowledge_count(),
            "questions": self.question_count(),
        }

    def search_fulltext(self, query: str, item_type: str = "all") -> list[str]:
        """FTS5 全文检索"""
        results: list[str] = []

        if item_type in ("all", "kp"):
            rows = self._conn.execute(
                """SELECT kp.id FROM knowledge_points kp
                   JOIN kp_fts ON kp.rowid = kp_fts.rowid
                   WHERE kp_fts MATCH ?
                   ORDER BY rank LIMIT 100""",
                (query,),
            ).fetchall()
            results.extend(r["id"] for r in rows)

        if item_type in ("all", "question"):
            rows = self._conn.execute(
                """SELECT q.id FROM questions q
                   JOIN question_fts ON q.rowid = question_fts.rowid
                   WHERE question_fts MATCH ?
                   ORDER BY rank LIMIT 100""",
                (query,),
            ).fetchall()
            results.extend(r["id"] for r in rows)

        return results

    def find_duplicates(self, content: str, item_type: str = "all") -> list[str]:
        """按 content_hash 查找精确重复"""
        ch = _content_hash(content)
        results: list[str] = []

        if item_type in ("all", "kp"):
            rows = self._conn.execute(
                "SELECT id FROM knowledge_points WHERE content_hash = ?", (ch,)
            ).fetchall()
            results.extend(r["id"] for r in rows)

        if item_type in ("all", "question"):
            rows = self._conn.execute(
                "SELECT id FROM questions WHERE content_hash = ?", (ch,)
            ).fetchall()
            results.extend(r["id"] for r in rows)

        return results

    # ── 内部方法 ──────────────────────────────────────

    _ALLOWED_SEARCH_TABLES = {"knowledge_points", "questions"}

    def _search(self, table: str, filters: dict[str, object]) -> list[str]:
        """动态构建 WHERE 子句（table 必须是白名单内的表名）"""
        assert table in self._ALLOWED_SEARCH_TABLES

        conditions: list[str] = []
        params: list[object] = []

        for key, value in filters.items():
            if key == "difficulty_range" and isinstance(value, (list, tuple)):
                conditions.append("difficulty BETWEEN ? AND ?")
                params.extend(value[:2])
            elif key == "exclude_ids" and isinstance(value, (list, set)):
                ids_list = list(value)
                if ids_list:
                    placeholders = ",".join("?" * len(ids_list))
                    conditions.append(f"id NOT IN ({placeholders})")
                    params.extend(ids_list)
            elif key in (
                "discipline",
                "kp_type",
                "question_type",
                "subject",
                "exam_type",
                "is_generated",
                "quality_grade",
                "source_file",
            ):
                conditions.append(f"{key} = ?")
                params.append(value)
            elif key == "content_hash":
                conditions.append("content_hash = ?")
                params.append(value)

        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self._conn.execute(
            f"SELECT id FROM {table} WHERE {where} ORDER BY order_index LIMIT 500",  # noqa: S608
            params,
        ).fetchall()
        return [r["id"] for r in rows]

    @staticmethod
    def _row_to_kp(row: sqlite3.Row) -> KnowledgePoint:
        try:
            discipline = Discipline(row["discipline"] or "math")
        except ValueError:
            discipline = Discipline.OTHER
        return KnowledgePoint(
            id=row["id"],
            discipline=discipline,
            kp_type=row["kp_type"] or "theorem",
            number=row["number"] or "",
            content=row["content"],
            proof_content=row["proof_content"],
            proof_methods=_safe_json_list(row, "proof_methods"),
            difficulty=row["difficulty"],
            subject=row["subject"] or "",
            taxonomy_path=row["taxonomy_path"] or "",
            tags=_safe_json_list(row, "tags"),
            quality_grade=row["quality_grade"] or "B",
            source_file=row["source_file"] or "",
            source_chapter=row["source_chapter"] or "",
            source_section=row["source_section"] or "",
            start_line=row["start_line"] or 0,
            end_line=row["end_line"] or 0,
            order_index=row["order_index"] or 0,
            page_start=row["page_start"],
            page_end=row["page_end"],
        )

    @staticmethod
    def _row_to_question(row: sqlite3.Row) -> Question:
        try:
            discipline = Discipline(row["discipline"] or "math")
        except ValueError:
            discipline = Discipline.OTHER
        return Question(
            id=row["id"],
            discipline=discipline,
            question_type=row["question_type"] or "proof",
            number=row["number"] or "",
            content=row["content"],
            solution_content=row["solution_content"],
            solution_methods=_safe_json_list(row, "solution_methods"),
            answer=row["answer"] or "",
            difficulty=row["difficulty"],
            subject=row["subject"] or "",
            taxonomy_path=row["taxonomy_path"] or "",
            tags=_safe_json_list(row, "tags"),
            quality_grade=row["quality_grade"] or "B",
            source_file=row["source_file"] or "",
            source_chapter=row["source_chapter"] or "",
            exam_type=row["exam_type"],
            exam_year=row["exam_year"],
            start_line=row["start_line"] or 0,
            end_line=row["end_line"] or 0,
            order_index=row["order_index"] or 0,
            page_start=row["page_start"],
            page_end=row["page_end"],
            is_generated=bool(row["is_generated"]),
            parent_id=row["parent_id"],
        )
