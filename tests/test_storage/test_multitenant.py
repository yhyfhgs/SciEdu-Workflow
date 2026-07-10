"""MultiTenantSQLiteBackend — tenant_id 行级隔离 + 语义检索测试

重点：plan §4.4 / §6 要求的"跨租户越权查询一律拒"安全用例必须全绿。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import Discipline, KnowledgePoint, Question
from sciforge.storage.multitenant import (
    MultiTenantSQLiteBackend,
    open_multitenant_backend,
)
from sciforge.storage.protocol import MultiTenantStorageBackend
from sciforge.storage.query import QualityGateFilter


@pytest.fixture
def backend(tmp_path: Path):
    be = MultiTenantSQLiteBackend(tmp_path / "mt.db")
    yield be
    be.close()


def _q(content: str, tenant_id: str = "") -> Question:
    return Question(content=content, discipline=Discipline.MATH, tenant_id=tenant_id)


# ── tenant 行级隔离 ──────────────────────────────────────


def test_cross_tenant_isolation(backend: MultiTenantSQLiteBackend) -> None:
    """租户 A 的题对租户 B 不可见（越权查询返回空）。"""
    backend.save_batch([], [_q("租户A私有题", tenant_id="tenant-A")])
    backend.save_batch([], [_q("租户B私有题", tenant_id="tenant-B")])

    a_ids = backend.search_questions_for_tenant({}, tenant_id="tenant-A")
    b_ids = backend.search_questions_for_tenant({}, tenant_id="tenant-B")

    a_contents = [backend.load_question(i).content for i in a_ids]
    b_contents = [backend.load_question(i).content for i in b_ids]

    assert "租户A私有题" in a_contents
    assert "租户A私有题" not in b_contents  # ★ 越权一律拒
    assert "租户B私有题" in b_contents
    assert "租户B私有题" not in a_contents


def test_same_content_is_deduplicated_per_tenant(backend: MultiTenantSQLiteBackend) -> None:
    a = _q("两个租户都可拥有的同一道题", tenant_id="tenant-A")
    b = _q("两个租户都可拥有的同一道题", tenant_id="tenant-B")

    backend.save_batch([], [a, b])

    assert backend.load_question(a.id).tenant_id == "tenant-A"
    assert backend.load_question(b.id).tenant_id == "tenant-B"
    assert backend.search_questions_for_tenant({}, tenant_id="tenant-A", include_public=False) == [
        a.id
    ]
    assert backend.search_questions_for_tenant({}, tenant_id="tenant-B", include_public=False) == [
        b.id
    ]
    assert backend.count_existing_content([a.content], "question", "tenant-A") == 1
    assert backend.count_existing_content([a.content], "question", "tenant-B") == 1
    assert backend.count_existing_content([a.content], "question") == 0


def test_tenant_quality_gate_requires_explicit_score_and_matching_kind(
    backend: MultiTenantSQLiteBackend,
) -> None:
    backend.save_question(_q("未评价私有题", tenant_id="tenant-A"))

    assert (
        backend.search_questions_for_tenant(
            {"quality_gate": QualityGateFilter(item_kind="question")},
            tenant_id="tenant-A",
            include_public=False,
        )
        == []
    )
    with pytest.raises(ValueError, match="does not match search table"):
        backend.search_questions_for_tenant(
            {"quality_gate": QualityGateFilter(item_kind="knowledge")},
            tenant_id="tenant-A",
        )


def test_public_questions_visible_to_all(backend: MultiTenantSQLiteBackend) -> None:
    """公共题库（tenant_id=''）默认对所有租户只读可见。"""
    backend.save_batch([], [_q("公共题", tenant_id="")])
    backend.save_batch([], [_q("A私有", tenant_id="tenant-A")])

    a_ids = backend.search_questions_for_tenant({}, tenant_id="tenant-A")
    contents = [backend.load_question(i).content for i in a_ids]
    assert "公共题" in contents
    assert "A私有" in contents


def test_include_public_false_excludes_public(backend: MultiTenantSQLiteBackend) -> None:
    backend.save_batch([], [_q("公共题", tenant_id="")])
    backend.save_batch([], [_q("A私有", tenant_id="tenant-A")])

    ids = backend.search_questions_for_tenant({}, tenant_id="tenant-A", include_public=False)
    contents = [backend.load_question(i).content for i in ids]
    assert contents == ["A私有"]


def test_tenant_filter_combines_with_other_filters(backend: MultiTenantSQLiteBackend) -> None:
    backend.save_batch(
        [],
        [
            Question(
                content="A证明题",
                discipline=Discipline.MATH,
                question_type="proof",
                tenant_id="tenant-A",
            ),
            Question(
                content="A计算题",
                discipline=Discipline.MATH,
                question_type="computational",
                tenant_id="tenant-A",
            ),
            Question(
                content="B证明题",
                discipline=Discipline.MATH,
                question_type="proof",
                tenant_id="tenant-B",
            ),
        ],
    )
    ids = backend.search_questions_for_tenant({"question_type": "proof"}, tenant_id="tenant-A")
    contents = [backend.load_question(i).content for i in ids]
    assert contents == ["A证明题"]  # 只 A 的 proof，不含 B 的 proof


def test_count_questions_for_tenant(backend: MultiTenantSQLiteBackend) -> None:
    backend.save_batch([], [_q("公共", "")])
    backend.save_batch([], [_q("A1", "tenant-A"), _q("A2", "tenant-A")])
    backend.save_batch([], [_q("B1", "tenant-B")])

    assert backend.count_questions_for_tenant("tenant-A") == 3  # 2 私有 + 1 公共
    assert backend.count_questions_for_tenant("tenant-A", include_public=False) == 2
    assert backend.count_questions_for_tenant("tenant-B", include_public=False) == 1


def test_knowledge_points_are_shared_public(backend: MultiTenantSQLiteBackend) -> None:
    """知识点是公共语料：经基类 search_knowledge 全局访问，无租户隔离。"""
    n = backend.save_batch(
        [KnowledgePoint(content="A定理", discipline=Discipline.MATH)],
        [],
    )
    assert n == 1
    assert len(backend.search_knowledge({})) == 1


def test_kp_embedding_is_public(backend: MultiTenantSQLiteBackend) -> None:
    """知识点向量恒为公共：任何租户的 KP 语义检索都能命中。"""
    backend.save_batch([KnowledgePoint(content="共享定理", discipline=Discipline.MATH)], [])
    kp_id = backend.search_knowledge({})[0]
    backend.save_embedding(kp_id, [1.0, 0.0], kind="knowledge_point")
    res = backend.semantic_search([1.0, 0.0], tenant_id="any-tenant", kind="knowledge_point")
    assert [r[0] for r in res] == [kp_id]


# ── 语义向量检索 ────────────────────────────────────────


def test_semantic_search_ranks_by_cosine(backend: MultiTenantSQLiteBackend) -> None:
    qids = backend.save_batch(
        [],
        [_q("题1", "tenant-A"), _q("题2", "tenant-A"), _q("题3", "tenant-A")],
    )
    assert qids == 3
    ids = backend.search_questions_for_tenant({}, tenant_id="tenant-A")
    # 给三题不同向量；query 与第一题最接近
    backend.save_embedding(ids[0], [1.0, 0.0, 0.0])
    backend.save_embedding(ids[1], [0.0, 1.0, 0.0])
    backend.save_embedding(ids[2], [0.9, 0.1, 0.0])

    results = backend.semantic_search([1.0, 0.0, 0.0], tenant_id="tenant-A", limit=2)
    assert len(results) == 2
    assert results[0][0] == ids[0]  # 完全对齐 → cosine=1
    assert results[0][1] > results[1][1]  # 降序
    assert results[1][0] == ids[2]  # 次接近


def test_semantic_search_respects_tenant_isolation(backend: MultiTenantSQLiteBackend) -> None:
    backend.save_batch([], [_q("A题", "tenant-A")])
    backend.save_batch([], [_q("B题", "tenant-B")])
    a_id = backend.search_questions_for_tenant({}, tenant_id="tenant-A", include_public=False)[0]
    b_id = backend.search_questions_for_tenant({}, tenant_id="tenant-B", include_public=False)[0]
    backend.save_embedding(a_id, [1.0, 0.0])
    backend.save_embedding(b_id, [1.0, 0.0])

    # 租户 B 的语义检索绝不返回租户 A 的题
    res = backend.semantic_search([1.0, 0.0], tenant_id="tenant-B", include_public=False)
    assert [r[0] for r in res] == [b_id]


def test_save_embedding_upsert_and_count(backend: MultiTenantSQLiteBackend) -> None:
    backend.save_batch([], [_q("题", "tenant-A")])
    qid = backend.search_questions_for_tenant({}, tenant_id="tenant-A")[0]
    backend.save_embedding(qid, [1.0, 2.0])
    backend.save_embedding(qid, [3.0, 4.0])  # upsert，同 (item_id, kind)
    assert backend.embedding_count(kind="question") == 1


def test_save_embedding_rejects_bad_kind(backend: MultiTenantSQLiteBackend) -> None:
    with pytest.raises(ValueError, match="未知 kind"):
        backend.save_embedding("x", [1.0], kind="bogus")


# ── 协议一致性 + 工厂 ────────────────────────────────────


def test_conforms_to_protocol(backend: MultiTenantSQLiteBackend) -> None:
    assert isinstance(backend, MultiTenantStorageBackend)


def test_factory_opens_sqlite(tmp_path: Path) -> None:
    be = open_multitenant_backend({"db_path": str(tmp_path / "f.db")})
    try:
        assert isinstance(be, MultiTenantSQLiteBackend)
    finally:
        be.close()


def test_factory_rejects_pg_dsn_until_implemented() -> None:
    with pytest.raises(NotImplementedError, match="MultiTenantPostgresBackend"):
        open_multitenant_backend({"dsn": "postgresql://user@host/db"})
