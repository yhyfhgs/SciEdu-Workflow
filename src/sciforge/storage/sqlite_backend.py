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
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog

from sciforge.core.exceptions import SciForgeError
from sciforge.core.models import (
    Discipline,
    Figure,
    KnowledgePoint,
    Question,
)

logger = structlog.get_logger()


# ── SQL 常量（executemany 批量写入复用）──────────────────
# schema v9：questions 新增生成元数据列（is_generated / generator_model /
# generated_at / generation_session_id / tenant_id）；提供 v8→v9 增量迁移
_SCHEMA_VERSION = 10

# schema 增量迁移：{from_version: [该版本→下一版的 ALTER 语句]}。
# 每条 ALTER 容忍 "duplicate column"（幂等，应对中断重跑）。
_MIGRATIONS: dict[int, list[str]] = {
    8: [
        "ALTER TABLE questions ADD COLUMN is_generated INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE questions ADD COLUMN generator_model TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE questions ADD COLUMN generated_at TEXT",
        "ALTER TABLE questions ADD COLUMN generation_session_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE questions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT ''",
    ],
    # v9→v10（REC-19 结构化题型 + REC-16 order_index 单列索引）
    9: [
        "ALTER TABLE questions ADD COLUMN options TEXT DEFAULT '[]'",
        "ALTER TABLE questions ADD COLUMN correct_option TEXT",
        "ALTER TABLE questions ADD COLUMN blanks INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_kp_order_only ON knowledge_points(order_index)",
        "CREATE INDEX IF NOT EXISTS idx_q_order_only ON questions(order_index)",
    ],
}

# 用 INSERT OR IGNORE：content_hash 唯一索引命中（重复内容）或 id 冲突时静默跳过，
# 保证重复窗口 / resume 重放 / 多次运行的幂等性（首次写入者胜出，不产生重复行）。
_KP_INSERT_SQL = """
INSERT OR IGNORE INTO knowledge_points
    (id, discipline, kp_type, number, number_canonical,
     seq_chapter, seq_section, seq_index,
     content, proof_content, difficulty, subject, taxonomy_path, tags, source_file,
     start_line, end_line, order_index, content_hash)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_Q_INSERT_SQL = """
INSERT OR IGNORE INTO questions
    (id, discipline, question_type, question_form, number, number_canonical,
     seq_chapter, seq_section, seq_index,
     content, solution_content, options, correct_option, blanks,
     difficulty, subject, taxonomy_path, tags, source_file,
     start_line, end_line, order_index, content_hash,
     is_generated, generator_model, generated_at, generation_session_id, tenant_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class SchemaVersionMismatch(SciForgeError):  # noqa: N818
    """DB schema 版本与当前代码不兼容"""


@lru_cache(maxsize=4096)
def _content_hash(text: str) -> str:
    """LaTeX 内容归一化后取 SHA-256 前 32 位。

    REC-26：纯函数按入参记忆化——同一条 content 在去重预查(count_existing_content)与
    入库(save_batch)路径各算一次，缓存消除重复归一化+哈希（bounded maxsize）。
    """
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
        kp.number_canonical,
        kp.seq_chapter,
        kp.seq_section,
        kp.seq_index,
        kp.content,
        kp.proof_content,
        kp.difficulty,
        kp.subject,
        kp.taxonomy_path,
        json.dumps(kp.tags, ensure_ascii=False),
        kp.source_file,
        kp.start_line,
        kp.end_line,
        kp.order_index,
        _content_hash(kp.content),
    )


def _question_to_tuple(q: Question) -> tuple[Any, ...]:
    """Question → executemany 参数元组（顺序与 _Q_INSERT_SQL 一致）"""
    return (
        q.id,
        str(q.discipline),
        str(q.question_type),
        q.question_form,
        q.number,
        q.number_canonical,
        q.seq_chapter,
        q.seq_section,
        q.seq_index,
        q.content,
        q.solution_content,
        json.dumps(q.options, ensure_ascii=False),
        q.correct_option,
        q.blanks,
        q.difficulty,
        q.subject,
        q.taxonomy_path,
        json.dumps(q.tags, ensure_ascii=False),
        q.source_file,
        q.start_line,
        q.end_line,
        q.order_index,
        _content_hash(q.content),
        int(q.is_generated),
        q.generator_model,
        q.generated_at.isoformat() if q.generated_at else None,
        q.generation_session_id,
        q.tenant_id,
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
        cur_version = self._conn.execute("PRAGMA user_version").fetchone()[0]
        has_kp_table = (
            self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_points'"
            ).fetchone()
            is not None
        )
        # 旧库：优先走增量迁移（ALTER）；仅在缺迁移路径时才要求删库重建
        if has_kp_table and 0 < cur_version < _SCHEMA_VERSION:
            self._migrate(cur_version)

        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        self._conn.executescript(schema_sql)
        self._conn.commit()

    def _migrate(self, from_version: int) -> None:
        """按 _MIGRATIONS 逐版本增量迁移（ALTER），替代删库重建。

        每条 ALTER 容忍 "duplicate column"，保证中断后重跑幂等；缺迁移路径时
        抛 SchemaVersionMismatch，提示 --restart（如 v8 之前的旧库）。
        """
        v = from_version
        while v < _SCHEMA_VERSION:
            stmts = _MIGRATIONS.get(v)
            if stmts is None:
                raise SchemaVersionMismatch(
                    f"检测到 DB schema v{from_version}，无 v{v}→v{v + 1} 迁移路径。"
                    f"请删除旧 DB 并加 --restart：\n"
                    f"  rm -rf {self.db_path} data/checkpoint/\n"
                    f"  uv run sciforge parse run ... --restart"
                )
            for sql in stmts:
                try:
                    self._conn.execute(sql)
                except sqlite3.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            v += 1
            self._conn.execute(f"PRAGMA user_version = {v}")
            self._conn.commit()
            logger.info("schema_migrated_step", from_version=v - 1, to_version=v)

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
        """一次事务 + executemany 保存多个 KP + Question（INSERT OR IGNORE 幂等去重）。

        返回尝试条数（非真实新增——FTS AFTER INSERT 触发器会污染 total_changes）。
        REC-10：真实 persisted/deduped 由 Stage3 用 ``count_existing_content`` 批量预查得出。
        """
        with self._write_lock, self.transaction():
            if kps:
                self._conn.executemany(_KP_INSERT_SQL, [_kp_to_tuple(kp) for kp in kps])
            if questions:
                self._conn.executemany(_Q_INSERT_SQL, [_question_to_tuple(q) for q in questions])
        logger.info("storage_batch_saved", kps=len(kps), questions=len(questions))
        return len(kps) + len(questions)

    def count_existing_content(self, contents: list[str], scope: str) -> int:
        """REC-10：批量统计 ``contents`` 已存在于库的条数（一次 SELECT，替代逐条 find_duplicates）。

        scope: "knowledge" → knowledge_points；其余 → questions。用 content_hash IN 批量查；
        入参内重复内容只算一次（按 hash 去重），与 INSERT OR IGNORE 的语义一致。
        """
        if not contents:
            return 0
        table = "knowledge_points" if scope == "knowledge" else "questions"
        hashes = list({_content_hash(c) for c in contents})
        placeholders = ",".join("?" for _ in hashes)
        cur = self._conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE content_hash IN ({placeholders})",  # noqa: S608
            hashes,
        )
        row = cur.fetchone()
        return int(row[0]) if row else 0

    def save_figures(self, figures: list[Figure]) -> int:
        """REC-18：批量写入图表元数据（INSERT OR IGNORE，UNIQUE(item_id,image_path) 幂等）。"""
        if not figures:
            return 0
        with self._write_lock, self.transaction():
            self._conn.executemany(
                "INSERT OR IGNORE INTO figures "
                "(item_id, item_kind, source_file, image_path, caption, page) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (f.item_id, f.item_kind, f.source_file, f.image_path, f.caption, f.page)
                    for f in figures
                ],
            )
        return len(figures)

    def figures_for_item(self, item_id: str) -> list[Figure]:
        """取某条目的全部图表。"""
        rows = self._conn.execute(
            "SELECT * FROM figures WHERE item_id = ? ORDER BY id", (item_id,)
        ).fetchall()
        return [
            Figure(
                id=r["id"],
                item_id=r["item_id"],
                item_kind=r["item_kind"],
                source_file=r["source_file"],
                image_path=r["image_path"],
                caption=r["caption"],
                page=r["page"],
            )
            for r in rows
        ]

    # ── 单条写入（内部用 save_batch 统一路径）─────────

    def save_knowledge_point(self, kp: KnowledgePoint) -> str:
        self.save_batch([kp], [])
        return kp.id

    def save_question(self, question: Question) -> str:
        self.save_batch([], [question])
        return question.id

    # ── 读取 ──────────────────────────────────────────

    def load_knowledge_point(self, id: str) -> KnowledgePoint:
        row = self._conn.execute("SELECT * FROM knowledge_points WHERE id = ?", (id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"KnowledgePoint {id} not found")
        return self._row_to_kp(row)

    def load_question(self, id: str) -> Question:
        row = self._conn.execute("SELECT * FROM questions WHERE id = ?", (id,)).fetchone()
        if not row:
            raise FileNotFoundError(f"Question {id} not found")
        return self._row_to_question(row)

    def load_questions_by_source(
        self, source_file: str, discipline: str | None = None
    ) -> list[Question]:
        """加载某 source_file 的全部 Question（无 500 上限，供 answer_linker）。"""
        cond = "source_file = ?"
        params: list[Any] = [source_file]
        if discipline:
            cond += " AND discipline = ?"
            params.append(discipline)
        rows = self._conn.execute(
            f"SELECT * FROM questions WHERE {cond} ORDER BY order_index",  # noqa: S608
            params,
        ).fetchall()
        return [self._row_to_question(r) for r in rows]

    def load_questions_by_discipline(self, discipline: str | None = None) -> list[Question]:
        """加载全库（或某学科）的全部 Question，供 REC-29 跨文档题答联动。"""
        if discipline:
            rows = self._conn.execute(
                "SELECT * FROM questions WHERE discipline = ? ORDER BY order_index",
                (discipline,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM questions ORDER BY order_index").fetchall()
        return [self._row_to_question(r) for r in rows]

    def update_question_solution(self, qid: str, solution: str) -> None:
        """回填题目的 solution_content（answer_linker 跨章节并答用）。"""
        with self._write_lock, self.transaction():
            self._conn.execute(
                "UPDATE questions SET solution_content = ? WHERE id = ?", (solution, qid)
            )

    def update_kp_type(self, kp_id: str, kp_type: str) -> None:
        """REC-15：reconciliation 高置信类型纠正——回改 knowledge_points.kp_type。"""
        with self._write_lock, self.transaction():
            self._conn.execute(
                "UPDATE knowledge_points SET kp_type = ? WHERE id = ?", (kp_type, kp_id)
            )

    def delete_question(self, qid: str) -> None:
        """删除一条 Question（回填后移除冗余答案条目；FTS 由触发器同步）。

        REC-28：item_relations 的 src_id/dst_id 跨表引用无法设 FK，故在此**级联清理**
        指向被删条目的边，避免知识图谱残留悬挂边指向不存在节点。
        """
        with self._write_lock, self.transaction():
            self._conn.execute("DELETE FROM questions WHERE id = ?", (qid,))
            self._conn.execute(
                "DELETE FROM item_relations WHERE src_id = ? OR dst_id = ?", (qid, qid)
            )

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

    def max_order_index(self, discipline: str | None = None) -> int:
        """返回当前 DB 里最大的 order_index（跨 KP/Question 合并）

        用于 resume 时同步 state.next_item_id 起点，避免新旧并存时 id 冲突。
        """
        cond = ""
        params: list[Any] = []
        if discipline:
            cond = " WHERE discipline = ?"
            params.append(discipline)

        kp_row = self._conn.execute(
            f"SELECT COALESCE(MAX(order_index), -1) FROM knowledge_points{cond}",  # noqa: S608
            params,
        ).fetchone()
        q_row = self._conn.execute(
            f"SELECT COALESCE(MAX(order_index), -1) FROM questions{cond}",  # noqa: S608
            params,
        ).fetchone()
        return max(kp_row[0], q_row[0]) if kp_row and q_row else -1

    def stats(self) -> dict[str, int]:
        return {
            "knowledge_points": self.knowledge_count(),
            "questions": self.question_count(),
        }

    def validation_report(self) -> dict[str, Any]:
        """只读体检报告：汇总各 phase 的正确性信号（供 `parse validate`）。

        覆盖 #2/#3/#4/#5/#6/#8：提取总量与类型分布、题答绑定率、编号规范化覆盖与
        "脏"规范化检测、content_hash 重复组（UNIQUE 应保证 0）、知识图谱边分布、
        来源文件分布。纯聚合查询，不修改数据。
        """
        c = self._conn

        def scalar(sql: str) -> int:
            row = c.execute(sql).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        def dist(sql: str) -> dict[str, int]:
            return {(r[0] if r[0] not in (None, "") else "(空)"): r[1] for r in c.execute(sql)}

        q_total = scalar("SELECT COUNT(*) FROM questions")
        q_with_sol = scalar(
            "SELECT COUNT(*) FROM questions "
            "WHERE solution_content IS NOT NULL AND TRIM(solution_content) != ''"
        )
        # "脏"规范化题号：非空但含非 [0-9.] 字符（如残留 "第"/"题"/字母/空格）→ 违反 #4
        dirty_canonical = scalar(
            "SELECT COUNT(*) FROM ("
            "  SELECT number_canonical AS nc FROM knowledge_points"
            "  UNION ALL SELECT number_canonical FROM questions"
            ") WHERE nc != '' AND nc GLOB '*[^0-9.]*'"
        )
        # 表内 content_hash 重复组（UNIQUE 部分索引应保证为 0）→ #8
        dup_groups = scalar(
            "SELECT COUNT(*) FROM (SELECT content_hash FROM knowledge_points"
            " WHERE content_hash != '' GROUP BY content_hash HAVING COUNT(*) > 1)"
        ) + scalar(
            "SELECT COUNT(*) FROM (SELECT content_hash FROM questions"
            " WHERE content_hash != '' GROUP BY content_hash HAVING COUNT(*) > 1)"
        )

        return {
            "kp_total": scalar("SELECT COUNT(*) FROM knowledge_points"),
            "q_total": q_total,
            "kp_by_type": dist(
                "SELECT kp_type, COUNT(*) FROM knowledge_points GROUP BY kp_type ORDER BY 2 DESC"
            ),
            "q_by_type": dist(
                "SELECT question_type, COUNT(*) FROM questions "
                "GROUP BY question_type ORDER BY 2 DESC"
            ),
            "q_by_form": dist(
                "SELECT question_form, COUNT(*) FROM questions "
                "GROUP BY question_form ORDER BY 2 DESC"
            ),
            "q_with_solution": q_with_sol,
            "q_missing_solution": q_total - q_with_sol,
            "kp_canonical_cov": scalar(
                "SELECT COUNT(*) FROM knowledge_points WHERE number_canonical != ''"
            ),
            "q_canonical_cov": scalar(
                "SELECT COUNT(*) FROM questions WHERE number_canonical != ''"
            ),
            "dirty_canonical": dirty_canonical,
            "dup_hash_groups": dup_groups,
            "relations_total": scalar("SELECT COUNT(*) FROM item_relations"),
            "relations_by_type": dist(
                "SELECT relation_type, COUNT(*) FROM item_relations "
                "GROUP BY relation_type ORDER BY 2 DESC"
            ),
            "source_files": dist(
                "SELECT source_file, COUNT(*) FROM ("
                "  SELECT source_file FROM knowledge_points"
                "  UNION ALL SELECT source_file FROM questions"
                ") GROUP BY source_file ORDER BY 2 DESC"
            ),
        }

    def numbering_rows(
        self, source_file: str, discipline: str | None = None
    ) -> list[tuple[Any, ...]]:
        """返回某 source_file 下所有条目的编号信息，供编号连续性 audit。

        每行：(subject, seq_chapter, seq_section, seq_index, number_canonical, kind, id)
        kind ∈ {"kp", "question"}。
        """
        out: list[tuple[Any, ...]] = []
        for table, kind in (("knowledge_points", "kp"), ("questions", "question")):
            cond = "source_file = ?"
            params: list[Any] = [source_file]
            if discipline:
                cond += " AND discipline = ?"
                params.append(discipline)
            sql = (  # noqa: S608 — table 来自硬编码字面量元组
                f"SELECT subject, seq_chapter, seq_section, seq_index, "
                f"number_canonical, id FROM {table} WHERE {cond}"
            )
            for r in self._conn.execute(sql, params).fetchall():
                out.append(
                    (
                        r["subject"],
                        r["seq_chapter"],
                        r["seq_section"],
                        r["seq_index"],
                        r["number_canonical"],
                        kind,
                        r["id"],
                    )
                )
        return out

    def relation_rows(
        self, source_file: str, discipline: str | None = None
    ) -> list[dict[str, Any]]:
        """返回某 source_file 下所有条目的结构信息，供结构化关系推断（#6）。

        每行 dict：``id`` / ``kind`` ("kp"|"question") / ``type`` (kp_type|question_type) /
        ``subject`` / ``seq_chapter`` / ``seq_section`` / ``order_index``。按 order_index 升序。
        """
        out: list[dict[str, Any]] = []
        for table, kind, type_col in (
            ("knowledge_points", "kp", "kp_type"),
            ("questions", "question", "question_type"),
        ):
            cond = "source_file = ?"
            params: list[Any] = [source_file]
            if discipline:
                cond += " AND discipline = ?"
                params.append(discipline)
            sql = (  # noqa: S608 — table/type_col 来自硬编码字面量元组
                f"SELECT id, {type_col} AS type, subject, seq_chapter, seq_section, "
                f"order_index FROM {table} WHERE {cond}"
            )
            for r in self._conn.execute(sql, params).fetchall():
                out.append(
                    {
                        "id": r["id"],
                        "kind": kind,
                        "type": r["type"],
                        "subject": r["subject"],
                        "seq_chapter": r["seq_chapter"],
                        "seq_section": r["seq_section"],
                        "order_index": r["order_index"] or 0,
                    }
                )
        out.sort(key=lambda x: x["order_index"])
        return out

    def reconciliation_rows(
        self, source_file: str, discipline: str | None = None
    ) -> list[dict[str, Any]]:
        """返回某 source_file 下条目的复核信息（#7）。

        每行 dict：``id`` / ``kind`` / ``type`` / ``number_canonical`` / ``subject`` /
        ``seq_chapter`` / ``seq_section`` / ``content_head``（正文前 40 字）/ ``content_hash``。
        """
        out: list[dict[str, Any]] = []
        for table, kind, type_col in (
            ("knowledge_points", "kp", "kp_type"),
            ("questions", "question", "question_type"),
        ):
            cond = "source_file = ?"
            params: list[Any] = [source_file]
            if discipline:
                cond += " AND discipline = ?"
                params.append(discipline)
            sql = (  # noqa: S608 — table/type_col 来自硬编码字面量元组
                f"SELECT id, {type_col} AS type, number_canonical, subject, "
                f"seq_chapter, seq_section, SUBSTR(content, 1, 40) AS content_head, "
                f"content_hash, order_index FROM {table} WHERE {cond}"
            )
            for r in self._conn.execute(sql, params).fetchall():
                out.append(
                    {
                        "id": r["id"],
                        "kind": kind,
                        "type": r["type"],
                        "number_canonical": r["number_canonical"],
                        "subject": r["subject"],
                        "seq_chapter": r["seq_chapter"],
                        "seq_section": r["seq_section"],
                        "content_head": r["content_head"] or "",
                        "content_hash": r["content_hash"] or "",
                        "order_index": r["order_index"] or 0,
                    }
                )
        out.sort(key=lambda x: x["order_index"])
        return out

    def save_relations(self, edges: list[dict[str, Any]], discipline: str = "math") -> int:
        """批量写入关系边（INSERT OR IGNORE，UNIQUE(src,dst,type) 去重幂等）。

        Args:
            edges: 边 dict（src_id/dst_id/relation_type/confidence/source/note）
            discipline: 学科标识，写入 discipline 列

        Returns:
            实际新增行数（rowcount）。
        """
        if not edges:
            return 0
        rows = [
            (
                e["src_id"],
                e["dst_id"],
                e.get("relation_type", "related"),
                float(e.get("confidence", 1.0)),
                e.get("source", "structural"),
                discipline,
                e.get("note", ""),
            )
            for e in edges
        ]
        with self._write_lock:
            cur = self._conn.executemany(
                "INSERT OR IGNORE INTO item_relations "
                "(src_id, dst_id, relation_type, confidence, source, discipline, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            if not self._in_transaction:
                self._conn.commit()
            return cur.rowcount

    def load_relations(self, src_id: str) -> list[dict[str, Any]]:
        """加载某条目出发的所有关系边（供知识图谱查询/导出）。"""
        rows = self._conn.execute(
            "SELECT src_id, dst_id, relation_type, confidence, source, note "
            "FROM item_relations WHERE src_id = ?",
            (src_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def relation_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM item_relations").fetchone()[0])

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

    # ── 学科全局 Tag Tree ──────────────────────────────

    def load_discipline_tag_tree(self, discipline: str) -> str:
        """加载学科的全局 tag tree JSON，不存在返回空串"""
        row = self._conn.execute(
            "SELECT tree_json FROM discipline_tag_trees WHERE discipline = ?",
            (discipline,),
        ).fetchone()
        return str(row["tree_json"]) if row else ""

    def save_discipline_tag_tree(self, discipline: str, tree: object) -> None:
        """合并保存学科 tag tree（load→merge→save 事务）"""
        from sciforge.pipeline.tag_tree import TagTree

        with self._write_lock:
            existing_json = self.load_discipline_tag_tree(discipline)
            if existing_json:
                global_tree = TagTree.from_json(existing_json)
                global_tree.merge(tree)  # type: ignore[arg-type]
            else:
                global_tree = tree  # type: ignore[assignment]

            self._conn.execute(
                """INSERT INTO discipline_tag_trees (discipline, tree_json, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(discipline) DO UPDATE
                   SET tree_json = excluded.tree_json,
                       updated_at = excluded.updated_at""",
                (discipline, global_tree.to_json()),
            )
            self._conn.commit()
            logger.info(
                "discipline_tag_tree_saved",
                discipline=discipline,
                nodes=global_tree.total_nodes(),
            )

    # ── 内部方法 ──────────────────────────────────────

    _ALLOWED_SEARCH_TABLES = {"knowledge_points", "questions"}

    @staticmethod
    def _build_filter_clause(filters: dict[str, object]) -> tuple[list[str], list[object]]:
        """把 filters 字典编译成 (conditions, params)，供 _search 与多租户子类复用。

        仅放行白名单字段，避免 SQL 注入（key 全部来自字面量）。
        """
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
                "source_file",
            ):
                conditions.append(f"{key} = ?")
                params.append(value)
            elif key == "content_hash":
                conditions.append("content_hash = ?")
                params.append(value)

        return conditions, params

    def _search(self, table: str, filters: dict[str, object]) -> list[str]:
        """动态构建 WHERE 子句（table 必须是白名单内的表名）"""
        assert table in self._ALLOWED_SEARCH_TABLES

        conditions, params = self._build_filter_clause(filters)
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
            number_canonical=row["number_canonical"] or "",
            seq_chapter=row["seq_chapter"],
            seq_section=row["seq_section"],
            seq_index=row["seq_index"],
            content=row["content"],
            proof_content=row["proof_content"],
            difficulty=row["difficulty"],
            subject=row["subject"] or "",
            taxonomy_path=row["taxonomy_path"] or "",
            tags=_safe_json_list(row, "tags"),
            source_file=row["source_file"] or "",
            start_line=row["start_line"] or 0,
            end_line=row["end_line"] or 0,
            order_index=row["order_index"] or 0,
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
            question_form=row["question_form"] or "",
            number=row["number"] or "",
            number_canonical=row["number_canonical"] or "",
            seq_chapter=row["seq_chapter"],
            seq_section=row["seq_section"],
            seq_index=row["seq_index"],
            content=row["content"],
            solution_content=row["solution_content"],
            options=_safe_json_list(row, "options"),
            correct_option=row["correct_option"],
            blanks=row["blanks"],
            difficulty=row["difficulty"],
            subject=row["subject"] or "",
            taxonomy_path=row["taxonomy_path"] or "",
            tags=_safe_json_list(row, "tags"),
            source_file=row["source_file"] or "",
            start_line=row["start_line"] or 0,
            end_line=row["end_line"] or 0,
            order_index=row["order_index"] or 0,
            is_generated=bool(row["is_generated"]),
            generator_model=row["generator_model"] or "",
            generated_at=(
                datetime.fromisoformat(row["generated_at"]) if row["generated_at"] else None
            ),
            generation_session_id=row["generation_session_id"] or "",
            tenant_id=row["tenant_id"] or "",
        )
