"""SaasStore — P2 多租户 / API Key 鉴权 / 用量计费的元数据存储

独立 SQLite DB（默认 data/saas.db），与题库 storage（sqlite_backend）解耦：
题库存内容，SaaS 存账户 / 鉴权 / 计费。这种分离也契合私有化部署（题库可独立交付，
SaaS 元数据仅在共享后端存在）。

表：tenants / api_keys / usage_records。API Key 仅存 sha256 hash（原始 key 仅创建时返回一次）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import uuid
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()

_SAAS_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT 'free',
    monthly_quota INTEGER NOT NULL DEFAULT 100,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_keys (
    key_hash TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT 'generate,solve',
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_apikey_tenant ON api_keys(tenant_id);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS usage_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    endpoint TEXT NOT NULL DEFAULT '',
    billable_units INTEGER NOT NULL DEFAULT 1,
    tokens_est INTEGER NOT NULL DEFAULT 0,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_usage_tenant ON usage_records(tenant_id);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'generate_batch',
    status TEXT NOT NULL DEFAULT 'pending',
    total INTEGER NOT NULL DEFAULT 0,
    done INTEGER NOT NULL DEFAULT 0,
    result_json TEXT NOT NULL DEFAULT '[]',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_task_tenant ON tasks(tenant_id);
"""


class TenantInfo(BaseModel):
    id: str
    name: str = ""
    plan: str = "free"
    monthly_quota: int = 100


class ApiKeyInfo(BaseModel):
    tenant_id: str
    scopes: list[str] = Field(default_factory=list)


class UsageSummary(BaseModel):
    tenant_id: str
    used: int = 0
    quota: int = 0
    remaining: int = 0


class TaskInfo(BaseModel):
    id: str
    tenant_id: str = ""
    status: str = "pending"  # pending / running / completed / failed
    total: int = 0
    done: int = 0
    result: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _hash_password(password: str, salt: str | None = None) -> str:
    """pbkdf2-sha256 密码哈希，格式 ``salt$digest``。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100_000)
    return f"{salt}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, _digest = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(_hash_password(password, salt), stored)


class SaasStore:
    """租户 / API Key / 计费元数据存储（独立 SQLite DB）。"""

    def __init__(self, db_path: Path | str = "data/saas.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SAAS_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SaasStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── 租户 ──

    def create_tenant(self, name: str, *, plan: str = "free", monthly_quota: int = 100) -> str:
        tid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO tenants (id, name, plan, monthly_quota) VALUES (?, ?, ?, ?)",
            (tid, name, plan, monthly_quota),
        )
        self._conn.commit()
        logger.info("saas_tenant_created", tenant_id=tid, plan=plan)
        return tid

    def get_tenant(self, tenant_id: str) -> TenantInfo | None:
        row = self._conn.execute(
            "SELECT id, name, plan, monthly_quota FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()
        if row is None:
            return None
        return TenantInfo(
            id=row["id"], name=row["name"], plan=row["plan"], monthly_quota=row["monthly_quota"]
        )

    # ── 用户 ──

    def create_user(
        self,
        username: str,
        password: str,
        *,
        tenant_id: str | None = None,
        plan: str = "free",
    ) -> tuple[str, str]:
        """注册用户；未指定 tenant_id 则自动建租户。

        返回 (user_id, tenant_id)；用户名重复抛 ValueError。
        """
        tid = tenant_id or self.create_tenant(username, plan=plan)
        uid = str(uuid.uuid4())
        try:
            self._conn.execute(
                "INSERT INTO users (id, username, password_hash, tenant_id) VALUES (?, ?, ?, ?)",
                (uid, username, _hash_password(password), tid),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"用户名已存在: {username}") from exc
        return uid, tid

    def authenticate(self, username: str, password: str) -> tuple[str, str] | None:
        """校验用户名+密码；成功返回 (user_id, tenant_id)，否则 None。"""
        row = self._conn.execute(
            "SELECT id, password_hash, tenant_id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row is None or not _verify_password(password, str(row["password_hash"])):
            return None
        return str(row["id"]), str(row["tenant_id"])

    # ── API Key ──

    def create_api_key(self, tenant_id: str, *, scopes: list[str] | None = None) -> str:
        """生成并返回原始 key（仅此一次可见）；DB 只存其 sha256 hash。"""
        raw = "sk-" + secrets.token_urlsafe(32)
        scope_str = ",".join(scopes or ["generate", "solve"])
        self._conn.execute(
            "INSERT INTO api_keys (key_hash, tenant_id, scopes) VALUES (?, ?, ?)",
            (_hash_key(raw), tenant_id, scope_str),
        )
        self._conn.commit()
        return raw

    def verify_api_key(self, raw_key: str) -> ApiKeyInfo | None:
        """校验原始 key；有效返回 ApiKeyInfo，否则 None。"""
        if not raw_key:
            return None
        row = self._conn.execute(
            "SELECT tenant_id, scopes FROM api_keys WHERE key_hash = ? AND revoked = 0",
            (_hash_key(raw_key),),
        ).fetchone()
        if row is None:
            return None
        scopes = [s for s in str(row["scopes"]).split(",") if s]
        return ApiKeyInfo(tenant_id=row["tenant_id"], scopes=scopes)

    def revoke_api_key(self, raw_key: str) -> bool:
        cur = self._conn.execute(
            "UPDATE api_keys SET revoked = 1 WHERE key_hash = ?", (_hash_key(raw_key),)
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── 计费 / 用量 ──

    def record_usage(
        self, tenant_id: str, endpoint: str, *, billable_units: int = 1, tokens_est: int = 0
    ) -> None:
        self._conn.execute(
            "INSERT INTO usage_records (tenant_id, endpoint, billable_units, tokens_est) "
            "VALUES (?, ?, ?, ?)",
            (tenant_id, endpoint, billable_units, tokens_est),
        )
        self._conn.commit()

    def get_usage(self, tenant_id: str) -> UsageSummary:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(billable_units), 0) AS used "
            "FROM usage_records WHERE tenant_id = ?",
            (tenant_id,),
        ).fetchone()
        used = int(row["used"])
        tenant = self.get_tenant(tenant_id)
        quota = tenant.monthly_quota if tenant else 0
        return UsageSummary(
            tenant_id=tenant_id, used=used, quota=quota, remaining=max(0, quota - used)
        )

    def has_quota(self, tenant_id: str) -> bool:
        """是否还有配额（quota<=0 视为不限额）。"""
        summary = self.get_usage(tenant_id)
        if summary.quota <= 0:
            return True
        return summary.remaining > 0

    # ── 异步任务 ──

    def create_task(self, tenant_id: str, total: int, *, kind: str = "generate_batch") -> str:
        task_id = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO tasks (id, tenant_id, kind, total) VALUES (?, ?, ?, ?)",
            (task_id, tenant_id, kind, total),
        )
        self._conn.commit()
        return task_id

    def update_task(
        self,
        task_id: str,
        *,
        done: int | None = None,
        status: str | None = None,
        result_json: str | None = None,
        error: str | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if done is not None:
            sets.append("done = ?")
            params.append(done)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if result_json is not None:
            sets.append("result_json = ?")
            params.append(result_json)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if not sets:
            return
        sets.append("updated_at = datetime('now')")
        params.append(task_id)
        self._conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
        self._conn.commit()

    def get_task(self, task_id: str) -> TaskInfo | None:
        row = self._conn.execute(
            "SELECT id, tenant_id, status, total, done, result_json, error "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            parsed = json.loads(row["result_json"])
        except (json.JSONDecodeError, TypeError):
            parsed = []
        return TaskInfo(
            id=row["id"],
            tenant_id=row["tenant_id"],
            status=row["status"],
            total=row["total"],
            done=row["done"],
            result=parsed if isinstance(parsed, list) else [],
            error=row["error"],
        )
