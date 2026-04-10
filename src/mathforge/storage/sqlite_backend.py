"""SQLite 存储后端 — 替代 JSON 文件存储

提供：事务安全、FTS5 全文检索、索引查询、去重检测。
实现 StorageBackend Protocol。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import structlog

from mathforge.core.models import (
    DocumentTree,
    ItemRelation,
    KnowledgePoint,
    KPType,
    Question,
    QuestionType,
    TaxonomyNode,
)

# 向后兼容 — save_question 签名保留 solution 参数但不再使用
try:
    from mathforge.core.models import Solution
except ImportError:
    Solution = None  # type: ignore[assignment,misc]

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
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA cache_size = -64000")  # 64MB
        self._conn.execute("PRAGMA temp_store = MEMORY")
        self._in_transaction = False
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

    def save_batch(
        self,
        kps: list[KnowledgePoint],
        questions: list[tuple[Question, Solution | None]],
    ) -> int:
        """一次事务保存多个 KP + Question，返回保存总数"""
        saved = 0
        with self.transaction():
            for kp in kps:
                self.save_knowledge_point(kp)
                saved += 1
            for q, sol in questions:
                self.save_question(q, sol)
                saved += 1
        logger.info("storage_batch_saved", kps=len(kps), questions=len(questions))
        return saved

    def update_relations_batch(
        self,
        items: list[tuple[str, str, list[ItemRelation]]],
    ) -> None:
        """批量更新关系：[(item_id, item_type, relations), ...]"""
        with self.transaction():
            for item_id, item_type, relations in items:
                self._save_relations(item_id, item_type, relations)

    # ── KnowledgePoint ────────────────────────────────

    def save_knowledge_point(self, kp: KnowledgePoint) -> str:
        content_hash = _content_hash(kp.content)

        self._conn.execute(
            """INSERT OR REPLACE INTO knowledge_points
            (id, kp_type, number, content, proof_content, proof_methods,
             difficulty, subject, taxonomy_path, tags,
             source_file, source_chapter, source_section,
             quality_grade, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                kp.id,
                kp.kp_type.value if hasattr(kp.kp_type, "value") else str(kp.kp_type),
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
                kp.quality_grade,
                content_hash,
            ),
        )

        self._save_relations(kp.id, "kp", kp.relations)

        if not self._in_transaction:
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
        content_hash = _content_hash(question.content)

        self._conn.execute(
            """INSERT OR REPLACE INTO questions
            (id, question_type, number, content, solution_content, solution_methods,
             answer, difficulty, subject, taxonomy_path, tags,
             source_file, source_chapter, exam_type, exam_year,
             quality_grade, content_hash, is_generated, parent_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                question.id,
                question.question_type.value if hasattr(question.question_type, "value") else str(question.question_type),
                question.number,
                question.content,
                question.solution_content,
                json.dumps(question.solution_methods, ensure_ascii=False),
                question.answer,
                question.difficulty,
                question.subject,
                question.taxonomy_path,
                json.dumps(question.tags, ensure_ascii=False),
                question.source_file,
                question.source_chapter,
                question.exam_type,
                question.exam_year,
                question.quality_grade,
                content_hash,
                int(question.is_generated),
                question.parent_id,
            ),
        )

        self._save_relations(question.id, "question", question.relations)

        if not self._in_transaction:
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
        rel_row = self._conn.execute("SELECT COUNT(*) FROM relations").fetchone()
        tree_row = self._conn.execute("SELECT COUNT(*) FROM taxonomy_nodes").fetchone()
        return {
            "knowledge_points": kp_count,
            "questions": q_count,
            "relations": rel_row[0] if rel_row else 0,
            "tree_nodes": tree_row[0] if tree_row else 0,
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
        tags = json.loads(row["tags"]) if row["tags"] else []
        relations = self.get_relations(row["id"], "outgoing")
        try:
            kp_type = KPType(row["kp_type"])
        except ValueError:
            kp_type = KPType.THEOREM

        return KnowledgePoint(
            id=row["id"],
            kp_type=kp_type,
            number=row["number"] or "",
            content=row["content"],
            proof_content=row["proof_content"],
            proof_methods=json.loads(row["proof_methods"]) if row["proof_methods"] else [],
            difficulty=row["difficulty"],
            subject=row["subject"] or "",
            taxonomy_path=row["taxonomy_path"] or "",
            tags=tags,
            relations=relations,
            quality_grade=row["quality_grade"] or "B",
            source_file=row["source_file"] or "",
            source_chapter=row["source_chapter"] or "",
            source_section=row["source_section"] or "",
        )

    def _row_to_question(self, row: sqlite3.Row) -> Question:
        tags = json.loads(row["tags"]) if row["tags"] else []
        relations = self.get_relations(row["id"], "outgoing")

        try:
            q_type = QuestionType(row["question_type"])
        except ValueError:
            q_type = QuestionType.PROOF

        return Question(
            id=row["id"],
            question_type=q_type,
            number=row["number"] or "",
            content=row["content"],
            solution_content=row["solution_content"],
            solution_methods=json.loads(row["solution_methods"]) if row["solution_methods"] else [],
            answer=row["answer"] or "",
            difficulty=row["difficulty"],
            subject=row["subject"] or "",
            taxonomy_path=row["taxonomy_path"] or "",
            tags=tags,
            relations=relations,
            quality_grade=row["quality_grade"] or "B",
            source_file=row["source_file"] or "",
            source_chapter=row["source_chapter"] or "",
            exam_type=row["exam_type"],
            exam_year=row["exam_year"],
            is_generated=bool(row["is_generated"]),
            parent_id=row["parent_id"],
        )

    # ── 文档结构树持久化 ──

    def save_tree_node(self, node: TaxonomyNode) -> None:
        """增量保存单个树节点"""
        self._conn.execute(
            """INSERT OR REPLACE INTO taxonomy_nodes
               (node_id, level, title, number, parent_id, source_file, position)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                node.node_id,
                node.level,
                node.title,
                node.number,
                node.parent_id,
                node.source_file,
                node.position,
            ),
        )
        self._conn.commit()

    def save_item_placement(
        self, item_id: str, item_type: str, node_id: str, position: int
    ) -> None:
        """保存 item 在树中的挂载关系"""
        self._conn.execute(
            """INSERT OR REPLACE INTO item_tree_placement
               (item_id, item_type, node_id, position_in_node)
               VALUES (?, ?, ?, ?)""",
            (item_id, item_type, node_id, position),
        )
        self._conn.commit()

    def load_tree(self, source_file: str) -> DocumentTree:
        """加载某个文件的完整文档结构树"""
        from mathforge.core.models import DocumentTree, TaxonomyNode

        rows = self._conn.execute(
            "SELECT * FROM taxonomy_nodes WHERE source_file = ? ORDER BY position",
            (source_file,),
        ).fetchall()

        tree = DocumentTree()
        for row in rows:
            node = TaxonomyNode(
                node_id=row["node_id"],
                level=row["level"],
                title=row["title"],
                number=row["number"] or "",
                parent_id=row["parent_id"],
                source_file=row["source_file"],
                position=row["position"],
            )
            tree.nodes[node.node_id] = node
            if node.level == "book":
                tree.root_id = node.node_id

        # 重建 children_ids
        for node in tree.nodes.values():
            if node.parent_id and node.parent_id in tree.nodes:
                tree.nodes[node.parent_id].children_ids.append(node.node_id)

        # 加载 item placements
        placements = self._conn.execute(
            """SELECT p.item_id, p.node_id
               FROM item_tree_placement p
               JOIN taxonomy_nodes n ON p.node_id = n.node_id
               WHERE n.source_file = ?
               ORDER BY p.position_in_node""",
            (source_file,),
        ).fetchall()

        for p in placements:
            item_id = p["item_id"]
            node_id = p["node_id"]
            tree.item_to_node[item_id] = node_id
            if node_id in tree.nodes:
                tree.nodes[node_id].item_ids.append(item_id)

        return tree

    def get_subtree_items(self, node_id: str) -> list[str]:
        """获取某个节点及其所有子节点下的 item_id 列表"""
        # 递归收集子节点
        node_ids = [node_id]
        queue = [node_id]
        while queue:
            current = queue.pop(0)
            children = self._conn.execute(
                "SELECT node_id FROM taxonomy_nodes WHERE parent_id = ?",
                (current,),
            ).fetchall()
            for child in children:
                cid = child["node_id"]
                node_ids.append(cid)
                queue.append(cid)

        # 查询所有 item
        placeholders = ",".join("?" * len(node_ids))
        rows = self._conn.execute(
            f"SELECT item_id FROM item_tree_placement WHERE node_id IN ({placeholders})"
            " ORDER BY position_in_node",
            node_ids,
        ).fetchall()

        return [r["item_id"] for r in rows]
