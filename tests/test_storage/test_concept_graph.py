"""schema v12 Concept Graph 持久化、幂等、查询与 item-edge 聚合证据。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import Discipline, KnowledgePoint, Question
from sciforge.pipeline.concept_linker import (
    aggregate_item_relations_to_concepts,
    link_items_to_concepts,
)
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


@pytest.fixture
def backend(tmp_path: Path) -> SQLiteStorageBackend:
    db = SQLiteStorageBackend(tmp_path / "concepts.db")
    yield db
    db.close()


def _seed_items(db: SQLiteStorageBackend) -> tuple[KnowledgePoint, Question]:
    kp = KnowledgePoint(
        id="kp-continuity",
        discipline=Discipline.MATH,
        kp_type="definition",
        content="一致连续的定义。",
        taxonomy_path="math.analysis.continuity.uniform_continuity",
        tags=["math:数学", "analysis:分析", "uniform_continuity:一致连续"],
        source_file="book-a.md",
        order_index=1,
    )
    q = Question(
        id="q-continuity",
        discipline=Discipline.MATH,
        question_type="proof",
        content="证明该函数一致连续。",
        taxonomy_path="math.analysis.continuity.uniform_continuity",
        tags=["math:数学", "analysis:分析", "uniform_continuity:一致连续"],
        source_file="book-a.md",
        order_index=2,
    )
    db.save_batch([kp], [q])
    return kp, q


def test_concept_graph_roundtrip_and_idempotency(backend: SQLiteStorageBackend) -> None:
    _seed_items(backend)
    batch = link_items_to_concepts(
        backend.concept_link_rows("book-a.md", "math"),
        discipline="math",
        existing_aliases=backend.load_concept_aliases("math"),
    )

    first = backend.save_concept_graph(batch)
    second = backend.save_concept_graph(batch)

    assert first["concepts"] == 3
    assert second == first  # 返回尝试数；底层主键保证实际数据幂等
    assert backend.concept_count() == 3
    assert backend.concept_count(status="pending") == 0
    leaf = "math.analysis.continuity.uniform_continuity"
    linked = backend.items_for_concept(leaf)
    assert {row["item_id"] for row in linked} == {"kp-continuity", "q-continuity"}
    assert {row["role"] for row in linked} == {"defines", "tests"}
    neighbors = backend.concept_neighbors(leaf, direction="outgoing")
    assert neighbors[0]["dst_concept"] == "math.analysis.continuity"
    assert neighbors[0]["relation_type"] == "part_of"


def test_persisted_alias_reuses_identity_for_later_source(
    backend: SQLiteStorageBackend,
) -> None:
    _seed_items(backend)
    original = link_items_to_concepts(
        backend.concept_link_rows("book-a.md", "math"), discipline="math"
    )
    backend.save_concept_graph(original)

    later = Question(
        id="q-book-b",
        discipline=Discipline.MATH,
        content="判断是否一致连续。",
        taxonomy_path="",
        tags=["math:数学", "analysis:分析", "uniform:一致连续"],
        source_file="book-b.md",
    )
    backend.save_question(later)
    linked = link_items_to_concepts(
        backend.concept_link_rows("book-b.md", "math"),
        discipline="math",
        existing_aliases=backend.load_concept_aliases("math"),
    )
    backend.save_concept_graph(linked)

    assert linked.pending_concept_ids == []
    assert linked.item_links[0].concept_id == "math.analysis.continuity.uniform_continuity"
    assert backend.concept_count() == 3


def test_item_relation_evidence_is_promoted_to_concept_graph(
    backend: SQLiteStorageBackend,
) -> None:
    kp, q = _seed_items(backend)
    batch = link_items_to_concepts(
        backend.concept_link_rows("book-a.md", "math"), discipline="math"
    )
    backend.save_concept_graph(batch)
    # 同 concept 的 tests 边会在聚合时被丢为 self-edge；再建一个相邻概念验证升级。
    second = KnowledgePoint(
        id="kp-limits",
        discipline=Discipline.MATH,
        content="函数极限。",
        taxonomy_path="math.analysis.limits.function_limits",
        tags=["math:数学", "analysis:分析", "function_limits:函数极限"],
        source_file="book-a.md",
        order_index=0,
    )
    backend.save_knowledge_point(second)
    second_batch = link_items_to_concepts(
        backend.concept_link_rows("book-a.md", "math"),
        discipline="math",
        existing_aliases=backend.load_concept_aliases("math"),
    )
    backend.save_concept_graph(second_batch)
    backend.save_relations(
        [
            {
                "src_id": kp.id,
                "dst_id": second.id,
                "relation_type": "prerequisite",
                "confidence": 0.8,
                "source": "structural",
                "note": "test",
            },
            {
                "src_id": q.id,
                "dst_id": second.id,
                "relation_type": "prerequisite",
                "confidence": 0.6,
                "source": "structural",
                "note": "test",
            },
        ],
        "math",
    )

    promoted = aggregate_item_relations_to_concepts(backend.concept_relation_evidence_rows("math"))
    backend.save_concept_relations(promoted)
    rows = backend.concept_neighbors(
        "math.analysis.continuity.uniform_continuity",
        direction="outgoing",
        relation_type="prerequisite",
    )
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.7
    assert rows[0]["evidence_count"] == 2
