"""SaasStore — 租户 / API Key / 计费元数据测试"""

from __future__ import annotations

from pathlib import Path

from sciforge.storage.saas import SaasStore


def test_create_and_verify_api_key(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme", plan="pro", monthly_quota=500)
        raw = store.create_api_key(tid, scopes=["generate"])
        assert raw.startswith("sk-")
        info = store.verify_api_key(raw)
        assert info is not None
        assert info.tenant_id == tid
        assert info.scopes == ["generate"]


def test_verify_bad_key_returns_none(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        assert store.verify_api_key("sk-nonexistent") is None
        assert store.verify_api_key("") is None


def test_revoke_api_key(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme")
        raw = store.create_api_key(tid)
        assert store.verify_api_key(raw) is not None
        assert store.revoke_api_key(raw) is True
        assert store.verify_api_key(raw) is None  # 撤销后失效


def test_usage_tracking_and_quota(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme", monthly_quota=10)
        store.record_usage(tid, "/v1/generate", billable_units=1, tokens_est=500)
        store.record_usage(tid, "/v1/generate", billable_units=2)
        u = store.get_usage(tid)
        assert u.used == 3
        assert u.quota == 10
        assert u.remaining == 7
        assert store.has_quota(tid) is True


def test_quota_exhausted(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Acme", monthly_quota=2)
        store.record_usage(tid, "/v1/generate", billable_units=2)
        assert store.has_quota(tid) is False


def test_unlimited_quota(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        tid = store.create_tenant("Enterprise", monthly_quota=0)  # 0 = 不限额
        store.record_usage(tid, "/v1/generate", billable_units=999)
        assert store.has_quota(tid) is True


def test_get_tenant_none(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        assert store.get_tenant("nonexistent") is None
