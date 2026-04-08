"""SQLite 存储后端 — 替代 JSON 文件存储

提供：事务安全、FTS5 全文检索、索引查询、去重检测。
实现 StorageBackend Protocol。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import structlog

from mathforge.core.models import (
    ItemRelation,
    KnowledgePoint,
    KPSource,
    KPType,
    Question,
    QuestionSource,
    QuestionType,
    Solution,
    Subject,
)

logger = structlog.get_logger()


def _content_hash(text: str) -> str:
    """LaTeX 内容归一化后取 SHA-256"""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode()).hexdigest()[:32]


class SQLiteStorageBackend:
    """基于 SQLite 的存储后端"""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        """初始化数据库 schema"""
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

    # ── KnowledgePoint ────────────────────────────────

    def save_knowledge_point(self, kp: KnowledgePoint) -> str:
        content_hash = _content_hash(kp.content_latex)

        taxonomy_path = ""

        solution_json = None
        if kp.solution:
            solution_json = kp.solution.model_dump_json()

        source_file = ""
        source_chapter = None
        source_section = None
        source_line_start = None
        source_line_end = None
        if kp.source:
            source_file = kp.source.source_file or ""
            source_chapter = kp.source.chapter
            source_section = kp.source.section
            if kp.source.line_range:
                source_line_start, source_line_end = kp.source.line_range

        self._conn.execute(
            """INSERT OR REPLACE INTO knowledge_points
            (id, title, content_latex, content_plain, kp_type, difficulty,
             solution_json, source_file, source_chapter, source_section,
             source_line_start, source_line_end,
             taxonomy_path, tags_json, prerequisites_json,
             content_hash, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                kp.id,
                kp.title,
                kp.content_latex,
                kp.content_plain,
                kp.kp_type.value,
                kp.difficulty,
                solution_json,
                source_file,
                source_chapter,
                source_section,
                source_line_start,
                source_line_end,
                taxonomy_path,
                json.dumps(kp.tags, ensure_ascii=False),
                json.dumps(kp.prerequisites, ensure_ascii=False),
                content_hash,
            ),
        )

        # 保存关系
        self._save_relations(kp.id, "kp", kp.relations)

        # 保存标签
        self._save_tags(kp.id, "kp", kp.tags)

        self._conn.commit()
        return kp.id

    def load_knowledge_point(self, id: str) -> KnowledgePoint:
        row = self._conn.execute("SELECT * FROM knowledge_points WHERE id = ?", (id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"KnowledgePoint {id} not found")
        return self._row_to_kp(row)

    def search_knowledge(self, filters: dict) -> list[str]:
        return self._search("knowledge_points", filters)

    def knowledge_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM knowledge_points").fetchone()
        return row[0] if row else 0

    # ── Question ──────────────────────────────────────

    def save_question(self, question: Question, solution: Solution | None = None) -> str:
        content_hash = _content_hash(question.content_latex)

        source_file = ""
        source_chapter = None
        exam_type = None
        exam_year = None
        book_title = None
        if question.source:
            book_title = question.source.book_title
            source_chapter = question.source.chapter
            exam_type = question.source.exam_type.value if question.source.exam_type else None
            exam_year = question.source.year

        self._conn.execute(
            """INSERT OR REPLACE INTO questions
            (id, content_latex, content_plain, question_type, difficulty,
             subject, answer, source_file, source_chapter,
             exam_type, exam_year, book_title,
             is_generated, parent_id,
             taxonomy_path, tags_json,
             content_hash, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                question.id,
                question.content_latex,
                question.content_plain,
                question.question_type.value,
                question.difficulty,
                question.subject.value,
                "",
                source_file,
                source_chapter,
                exam_type,
                exam_year,
                book_title,
                int(question.is_generated),
                question.parent_id,
                "",
                json.dumps(question.tags, ensure_ascii=False),
                content_hash,
            ),
        )

        if solution:
            self._conn.execute(
                """INSERT OR REPLACE INTO solutions
                (id, question_id, solution_latex, solution_plain,
                 methods_used_json, step_count, confidence, is_verified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    solution.id,
                    solution.question_id,
                    solution.solution_latex,
                    "",
                    json.dumps(solution.methods_used, ensure_ascii=False),
                    solution.step_count,
                    solution.confidence,
                    int(solution.is_verified),
                ),
            )

        # 保存关系和标签
        self._save_relations(question.id, "question", question.relations)
        self._save_tags(question.id, "question", question.tags)

        self._conn.commit()
        return question.id

    def load_question(self, id: str) -> Question:
        row = self._conn.execute("SELECT * FROM questions WHERE id = ?", (id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"Question {id} not found")
        return self._row_to_question(row)

    def search_questions(self, filters: dict) -> list[str]:
        return self._search("questions", filters)

    def question_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM questions").fetchone()
        return row[0] if row else 0

    # ── Solution ──────────────────────────────────────

    def load_solution(self, question_id: str) -> Solution | None:
        row = self._conn.execute(
            "SELECT * FROM solutions WHERE question_id = ?", (question_id,)
        ).fetchone()
        if not row:
            return None
        return Solution(
            id=row["id"],
            question_id=row["question_id"],
            solution_latex=row["solution_latex"],
            methods_used=json.loads(row["methods_used_json"]) if row["methods_used_json"] else [],
            step_count=row["step_count"] or 0,
            confidence=row["confidence"] or "medium",
            is_verified=bool(row["is_verified"]),
        )

    # ── 全文检索 ──────────────────────────────────────

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

    # ── 去重 ──────────────────────────────────────────

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

    # ── 关系 ──────────────────────────────────────────

    def get_relations(self, item_id: str, direction: str = "both") -> list[ItemRelation]:
        """获取条目的关系"""
        results: list[ItemRelation] = []

        if direction in ("both", "outgoing"):
            rows = self._conn.execute(
                "SELECT * FROM relations WHERE source_id = ?", (item_id,)
            ).fetchall()
            for r in rows:
                results.append(
                    ItemRelation(
                        relation_type=r["relation_type"],
                        target_id=r["target_id"],
                        confidence=r["confidence"],
                        source=r["source_method"],
                        note=r["note"] or "",
                    )
                )

        if direction in ("both", "incoming"):
            rows = self._conn.execute(
                "SELECT * FROM relations WHERE target_id = ?", (item_id,)
            ).fetchall()
            for r in rows:
                results.append(
                    ItemRelation(
                        relation_type=r["relation_type"],
                        target_id=r["source_id"],
                        confidence=r["confidence"],
                        source=r["source_method"],
                        note=r["note"] or "",
                    )
                )

        return results

    # ── 统计 ──────────────────────────────────────────

    def stats(self) -> dict:
        """数据库统计信息"""
        kp_count = self.knowledge_count()
        q_count = self.question_count()
        sol_row = self._conn.execute("SELECT COUNT(*) FROM solutions").fetchone()
        rel_row = self._conn.execute("SELECT COUNT(*) FROM relations").fetchone()
        return {
            "knowledge_points": kp_count,
            "questions": q_count,
            "solutions": sol_row[0] if sol_row else 0,
            "relations": rel_row[0] if rel_row else 0,
        }

    # ── 批量操作 ─────────────────────────────────────

    def load_questions_by_ids(self, ids: list[str]) -> list[Question]:
        results = []
        for qid in ids:
            try:
                results.append(self.load_question(qid))
            except FileNotFoundError:
                pass
        return results

    def load_knowledge_by_ids(self, ids: list[str]) -> list[KnowledgePoint]:
        results = []
        for kp_id in ids:
            try:
                results.append(self.load_knowledge_point(kp_id))
            except FileNotFoundError:
                pass
        return results

    # ── 内部方法 ─────────────────────────────────────

    def _save_relations(self, item_id: str, item_type: str, relations: list[ItemRelation]) -> None:
        # 先清除旧关系
        self._conn.execute(
            "DELETE FROM relations WHERE source_id = ? AND source_type = ?",
            (item_id, item_type),
        )
        for rel in relations:
            # 从数据库推断 target_type
            target_type = self._infer_item_type(rel.target_id)
            self._conn.execute(
                """INSERT INTO relations
                (source_id, source_type, target_id, target_type,
                 relation_type, confidence, source_method, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    item_id,
                    item_type,
                    rel.target_id,
                    target_type,
                    rel.relation_type.value
                    if hasattr(rel.relation_type, "value")
                    else str(rel.relation_type),
                    rel.confidence,
                    rel.source,
                    rel.note,
                ),
            )

    def _infer_item_type(self, item_id: str) -> str:
        """从数据库推断条目类型"""
        row = self._conn.execute("SELECT 1 FROM questions WHERE id = ?", (item_id,)).fetchone()
        if row:
            return "question"
        return "kp"

    def _save_tags(self, item_id: str, item_type: str, tags: list[str]) -> None:
        self._conn.execute(
            "DELETE FROM tags WHERE item_id = ? AND item_type = ?",
            (item_id, item_type),
        )
        for tag in tags:
            self._conn.execute(
                "INSERT INTO tags (item_id, item_type, tag) VALUES (?, ?, ?)",
                (item_id, item_type, tag),
            )

    def _search(self, table: str, filters: dict) -> list[str]:
        """动态构建 WHERE 子句"""
        conditions: list[str] = []
        params: list = []

        for key, value in filters.items():
            if key == "tags" and isinstance(value, list):
                placeholders = ",".join("?" * len(value))
                conditions.append(f"id IN (SELECT item_id FROM tags WHERE tag IN ({placeholders}))")
                params.extend(value)
            elif key == "difficulty_range" and isinstance(value, (list, tuple)):
                conditions.append("difficulty BETWEEN ? AND ?")
                params.extend(value[:2])
            elif key == "exclude_ids" and isinstance(value, (list, set)):
                ids_list = list(value)
                if ids_list:
                    placeholders = ",".join("?" * len(ids_list))
                    conditions.append(f"id NOT IN ({placeholders})")
                    params.extend(ids_list)
            elif key in (
                "kp_type",
                "question_type",
                "subject",
                "exam_type",
                "is_generated",
                "quality_grade",
            ):
                conditions.append(f"{key} = ?")
                params.append(value)
            elif key == "content_hash":
                conditions.append("content_hash = ?")
                params.append(value)

        where = " AND ".join(conditions) if conditions else "1=1"
        rows = self._conn.execute(
            f"SELECT id FROM {table} WHERE {where} LIMIT 500",  # noqa: S608
            params,
        ).fetchall()
        return [r["id"] for r in rows]

    def _row_to_kp(self, row: sqlite3.Row) -> KnowledgePoint:
        source = KPSource(
            source_file=row["source_file"] or None,
            chapter=row["source_chapter"],
            section=row["source_section"],
            line_range=(row["source_line_start"], row["source_line_end"])
            if row["source_line_start"] is not None
            else None,
        )
        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        prerequisites = json.loads(row["prerequisites_json"]) if row["prerequisites_json"] else []
        relations = self.get_relations(row["id"], "outgoing")

        solution = None
        if row["solution_json"]:
            from mathforge.core.models import SolutionContent

            solution = SolutionContent.model_validate_json(row["solution_json"])

        try:
            kp_type = KPType(row["kp_type"])
        except ValueError:
            kp_type = KPType.THEOREM

        return KnowledgePoint(
            id=row["id"],
            title=row["title"],
            content_latex=row["content_latex"],
            content_plain=row["content_plain"],
            kp_type=kp_type,
            difficulty=row["difficulty"],
            tags=tags,
            prerequisites=prerequisites,
            relations=relations,
            solution=solution,
            source=source,
        )

    def _row_to_question(self, row: sqlite3.Row) -> Question:
        source = QuestionSource(
            book_title=row["book_title"],
            chapter=row["source_chapter"],
            year=row["exam_year"],
        )
        if row["exam_type"]:
            from mathforge.core.models import ExamType

            try:
                source.exam_type = ExamType(row["exam_type"])
            except ValueError:
                pass

        tags = json.loads(row["tags_json"]) if row["tags_json"] else []
        relations = self.get_relations(row["id"], "outgoing")

        try:
            q_type = QuestionType(row["question_type"])
        except ValueError:
            q_type = QuestionType.PROOF

        try:
            subject = Subject(row["subject"])
        except ValueError:
            subject = Subject.ANALYSIS

        return Question(
            id=row["id"],
            content_latex=row["content_latex"],
            content_plain=row["content_plain"],
            difficulty=row["difficulty"],
            question_type=q_type,
            subject=subject,
            tags=tags,
            relations=relations,
            source=source,
            is_generated=bool(row["is_generated"]),
            parent_id=row["parent_id"],
        )
