"""M5 Concept Linker：taxonomy 权威身份、pending 治理、跨书别名与关系聚合。"""

from __future__ import annotations

from sciforge.core.models import ConceptRole, ConceptStatus, RelationType
from sciforge.pipeline.concept_linker import (
    aggregate_item_relations_to_concepts,
    link_items_to_concepts,
    normalize_concept_alias,
)


def test_controlled_taxonomy_builds_hierarchy_and_question_role() -> None:
    batch = link_items_to_concepts(
        [
            {
                "id": "q-1",
                "kind": "question",
                "type": "proof",
                "subject": "real_analysis:实分析",
                "taxonomy_path": "math.analysis.continuity.uniform_continuity",
                "tags": [
                    "math:数学",
                    "analysis:分析",
                    "continuity:连续性",
                    "uniform_continuity:一致连续",
                ],
            }
        ],
        discipline="math",
    )

    assert [node.id for node in batch.concepts] == [
        "math.analysis",
        "math.analysis.continuity",
        "math.analysis.continuity.uniform_continuity",
    ]
    assert all(node.status == ConceptStatus.ACTIVE for node in batch.concepts)
    assert batch.item_links[0].role == ConceptRole.TESTS
    assert batch.item_links[0].concept_id.endswith("uniform_continuity")
    assert {
        (edge.relation_type, edge.src_concept, edge.dst_concept) for edge in batch.relations
    } == {
        (RelationType.PART_OF, "math.analysis.continuity", "math.analysis"),
        (
            RelationType.PART_OF,
            "math.analysis.continuity.uniform_continuity",
            "math.analysis.continuity",
        ),
    }
    aliases = {(alias.alias, alias.concept_id) for alias in batch.aliases}
    assert (
        "一致连续",
        "math.analysis.continuity.uniform_continuity",
    ) in aliases


def test_unknown_tag_is_pending_instead_of_polluting_active_graph() -> None:
    batch = link_items_to_concepts(
        [
            {
                "id": "kp-unknown",
                "kind": "knowledge",
                "type": "definition",
                "subject": "analysis:分析",
                "taxonomy_path": "math.analysis.not_in_taxonomy.xyz",
                "tags": ["math:数学", "analysis:分析", "brand_new_term:全新术语"],
            }
        ],
        discipline="math",
    )

    assert len(batch.concepts) == 1
    assert batch.concepts[0].status == ConceptStatus.PENDING
    assert batch.concepts[0].id == "math.analysis.brand_new_term"
    assert batch.pending_concept_ids == ["math.analysis.brand_new_term"]
    assert batch.item_links[0].role == ConceptRole.DEFINES


def test_existing_branch_scoped_alias_is_reused_across_books() -> None:
    alias_key = ("analysis", normalize_concept_alias("一致连续"))
    batch = link_items_to_concepts(
        [
            {
                "id": "q-book-b",
                "kind": "question",
                "type": "proof",
                "taxonomy_path": "",
                "tags": ["math:数学", "analysis:分析", "uniform:一致连续"],
            }
        ],
        discipline="math",
        existing_aliases={alias_key: "math.analysis.continuity.uniform_continuity"},
    )

    assert batch.concepts == []
    assert batch.pending_concept_ids == []
    assert batch.item_links[0].concept_id == "math.analysis.continuity.uniform_continuity"


def test_item_edges_are_aggregated_with_evidence_count() -> None:
    edges = aggregate_item_relations_to_concepts(
        [
            {
                "src_concept": "math.analysis.c1",
                "dst_concept": "math.analysis.c2",
                "relation_type": "prerequisite",
                "confidence": 0.8,
            },
            {
                "src_concept": "math.analysis.c1",
                "dst_concept": "math.analysis.c2",
                "relation_type": "prerequisite",
                "confidence": 0.6,
            },
            {
                "src_concept": "math.analysis.c1",
                "dst_concept": "math.analysis.c1",
                "relation_type": "tests",
                "confidence": 1.0,
            },
        ]
    )

    assert len(edges) == 1
    assert edges[0].confidence == 0.7
    assert edges[0].evidence_count == 2
    assert edges[0].relation_type == RelationType.PREREQUISITE
