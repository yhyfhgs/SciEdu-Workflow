"""MultiTenantSQLiteBackend — 多租户题库后端（共享云端后端的 SQLite 参考实现）

在 ``SQLiteStorageBackend`` 之上实现 ``MultiTenantStorageBackend`` 协议：
- **tenant_id 行级隔离**：检索强制注入 ``(tenant_id = ? [OR tenant_id = ''])``，
  跨租户数据物理不可见（plan §4.4 "查询层强制 tenant 过滤"）。
- **语义向量检索**：附加 ``item_embeddings`` 表（幂等建表，不入核心 schema），
  线性 cosine 扫描；生产 PostgreSQL 后端可改 pgvector 下推到 DB。

这是"用 SQLite 验证接口、PG 连接串留作配置"的落地：同一 ``MultiTenantStorageBackend``
协议，云端换 PG/pgvector 实现即可，业务层无感。account 元数据仍由 ``SaasStore`` 管。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import structlog

from sciforge.storage.query import require_scored_quality_gate
from sciforge.storage.sqlite_backend import SQLiteStorageBackend

logger = structlog.get_logger()

_EMBEDDINGS_DDL = """
CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'question',
    dim INTEGER NOT NULL,
    vector_json TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (item_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_emb_kind_tenant ON item_embeddings(kind, tenant_id);
"""

_KIND_TABLE = {"question": "questions", "knowledge_point": "knowledge_points"}


def _cosine(a: list[float], b: list[float]) -> float:
    """两向量 cosine 相似度；维度不一致或零向量返回 0。"""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class MultiTenantSQLiteBackend(SQLiteStorageBackend):
    """带 tenant_id 行级隔离 + 语义检索的 SQLite 题库后端。"""

    def __init__(self, db_path: Path | str, busy_timeout_ms: int = 5000) -> None:
        super().__init__(db_path, busy_timeout_ms)
        # 幂等建 embeddings 表（backend 私有扩展，不参与核心 _SCHEMA_VERSION 迁移）
        self._conn.executescript(_EMBEDDINGS_DDL)
        self._conn.commit()

    # ── tenant_id 行级隔离检索 ──────────────────────────────

    def _tenant_clause(self, tenant_id: str, include_public: bool) -> tuple[str, list[object]]:
        """构造租户隔离条件。include_public 时附带公共题库（tenant_id='')。"""
        if include_public:
            return "(tenant_id = ? OR tenant_id = '')", [tenant_id]
        return "tenant_id = ?", [tenant_id]

    def _search_for_tenant(
        self, table: str, filters: dict[str, object], tenant_id: str, include_public: bool
    ) -> list[str]:
        assert table in self._ALLOWED_SEARCH_TABLES
        filters = require_scored_quality_gate(filters)
        expected_kind = "knowledge" if table == "knowledge_points" else "question"
        conditions, params = self._build_filter_clause(
            filters,
            expected_item_kind=expected_kind,
        )
        tclause, tparams = self._tenant_clause(tenant_id, include_public)
        conditions.append(tclause)
        params.extend(tparams)
        where = " AND ".join(conditions)
        rows = self._conn.execute(
            f"SELECT id FROM {table} WHERE {where} ORDER BY order_index LIMIT 500",  # noqa: S608
            params,
        ).fetchall()
        return [r["id"] for r in rows]

    def search_questions_for_tenant(
        self,
        filters: dict[str, object],
        *,
        tenant_id: str,
        include_public: bool = True,
    ) -> list[str]:
        return self._search_for_tenant("questions", filters, tenant_id, include_public)

    def count_questions_for_tenant(self, tenant_id: str, *, include_public: bool = True) -> int:
        tclause, tparams = self._tenant_clause(tenant_id, include_public)
        row = self._conn.execute(
            f"SELECT COUNT(*) FROM questions WHERE {tclause}",  # noqa: S608
            tparams,
        ).fetchone()
        return int(row[0]) if row else 0

    # ── 语义向量检索 ────────────────────────────────────────

    def save_embedding(
        self, item_id: str, vector: list[float], *, kind: str = "question"
    ) -> None:
        if kind not in _KIND_TABLE:
            raise ValueError(f"未知 kind: {kind}（应为 question / knowledge_point）")
        # 记录该 item 的 tenant_id，使语义检索也做行级隔离。
        # 知识点是公共语料（表无 tenant_id 列）→ 恒为 ''（公共）。
        if kind == "question":
            row = self._conn.execute(
                "SELECT tenant_id FROM questions WHERE id = ?", (item_id,)
            ).fetchone()
            tenant_id = (row["tenant_id"] if row else "") or ""
        else:
            tenant_id = ""
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO item_embeddings (item_id, kind, dim, vector_json, tenant_id) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(item_id, kind) DO UPDATE SET "
                "dim = excluded.dim, vector_json = excluded.vector_json, "
                "tenant_id = excluded.tenant_id",
                (item_id, kind, len(vector), json.dumps(vector), tenant_id),
            )
            self._conn.commit()

    def semantic_search(
        self,
        query_embedding: list[float],
        *,
        tenant_id: str = "",
        kind: str = "question",
        limit: int = 10,
        include_public: bool = True,
    ) -> list[tuple[str, float]]:
        if kind not in _KIND_TABLE:
            raise ValueError(f"未知 kind: {kind}")
        tclause, tparams = self._tenant_clause(tenant_id, include_public)
        rows = self._conn.execute(
            "SELECT item_id, vector_json FROM item_embeddings "
            f"WHERE kind = ? AND {tclause}",  # noqa: S608
            [kind, *tparams],
        ).fetchall()
        scored: list[tuple[str, float]] = []
        for r in rows:
            try:
                vec = json.loads(r["vector_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(vec, list):
                continue
            scored.append((r["item_id"], _cosine(query_embedding, vec)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:limit]

    def embedding_count(self, *, kind: str | None = None) -> int:
        """已存向量数（测试 / 监控用）。"""
        if kind is None:
            row = self._conn.execute("SELECT COUNT(*) FROM item_embeddings").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM item_embeddings WHERE kind = ?", (kind,)
            ).fetchone()
        return int(row[0]) if row else 0


def open_multitenant_backend(config: dict[str, Any] | None = None) -> MultiTenantSQLiteBackend:
    """工厂：按配置打开多租户后端。

    当前仅 SQLite 参考实现；``config["dsn"]`` 形如 ``postgresql://...`` 时，
    未来切换到 ``MultiTenantPostgresBackend``（连接串作为配置项，不在此硬编码凭据）。
    """
    config = config or {}
    dsn = str(config.get("dsn", ""))
    if dsn.startswith(("postgres://", "postgresql://")):
        raise NotImplementedError(
            "MultiTenantPostgresBackend 待实现（P2 云端）；当前 dsn 应为 SQLite 路径。"
            " PG 连接串已作为配置项预留，需 PostgreSQL + pgvector 环境后接入。"
        )
    db_path = config.get("db_path") or dsn or "data/sciforge.db"
    return MultiTenantSQLiteBackend(db_path)
