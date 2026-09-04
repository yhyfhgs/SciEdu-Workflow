"""M6 确定性知识评价：10 维难度、质量门禁与上下文通用性。"""

from __future__ import annotations

from sciforge.core.models import CognitiveLevel
from sciforge.pipeline.evaluation import evaluate_item_rows


def test_complete_controlled_question_passes_quality_gate() -> None:
    batch = evaluate_item_rows(
        [
            {
                "id": "q-good",
                "kind": "question",
                "type": "proof",
                "content": "设 $f$ 在紧区间上连续，证明 $f$ 一致连续。",
                "reasoning": "证明：反设不一致连续，则存在序列导出矛盾。证毕。",
                "difficulty": 7,
                "taxonomy_path": "math.analysis.continuity.uniform_continuity",
                "tags": ["math:数学", "analysis:分析", "uniform_continuity:一致连续"],
                "start_line": 10,
                "end_line": 18,
                "concept_status": "active",
                "concept_count": 1,
                "concept_depth": 3,
                "concept_ids": ["math.analysis.continuity.uniform_continuity"],
                "figure_count": 0,
                "options": [],
            }
        ]
    )

    assert len(batch.evaluations) == 1
    evaluation = batch.evaluations[0]
    assert evaluation.extraction_quality >= 0.9
    assert evaluation.solution_completeness == 1.0
    assert 1 <= evaluation.difficulty_dims.concept_count <= 10
    assert 1 <= evaluation.difficulty_overall <= 10
    assert evaluation.cognitive_level == CognitiveLevel.ANALYZE
    assert batch.review_items == []


def test_missing_solution_pending_concept_enters_review_queue() -> None:
    batch = evaluate_item_rows(
        [
            {
                "id": "q-risky",
                "kind": "question",
                "type": "calculation",
                "content": "求 $\\int_0^1 x^2 dx",
                "reasoning": "",
                "difficulty": 4,
                "taxonomy_path": "",
                "tags": [],
                "start_line": 0,
                "end_line": 0,
                "concept_status": "pending",
                "concept_ids": ["math.unclassified.integral"],
            }
        ]
    )

    evaluation = batch.evaluations[0]
    assert evaluation.extraction_quality < 0.7
    assert evaluation.solution_completeness < 0.6
    reasons = {item.reason for item in batch.review_items}
    assert reasons == {"low_extraction_quality", "incomplete_solution", "pending_concept"}
    assert max(item.priority for item in batch.review_items) == 5


def test_source_specific_references_reduce_cross_book_generality() -> None:
    rows = [
        {
            "id": "kp-generic",
            "kind": "knowledge",
            "type": "definition",
            "content": "连续函数是保持极限的函数。",
            "reasoning": "",
            "difficulty": 2,
            "taxonomy_path": "math.analysis.continuity",
            "tags": ["math:数学", "analysis:分析", "continuity:连续性"],
            "start_line": 1,
            "end_line": 2,
            "concept_status": "active",
        },
        {
            "id": "kp-local",
            "kind": "knowledge",
            "type": "definition",
            "content": "由本章上文定义并结合下图可知该对象的性质。",
            "reasoning": "",
            "difficulty": 2,
            "taxonomy_path": "math.analysis.continuity",
            "tags": ["math:数学", "analysis:分析", "continuity:连续性"],
            "start_line": 3,
            "end_line": 4,
            "concept_status": "active",
        },
    ]
    evaluations = {item.item_id: item for item in evaluate_item_rows(rows).evaluations}

    assert (
        evaluations["kp-generic"].cross_book_generality
        > evaluations["kp-local"].cross_book_generality
    )
    assert (
        evaluations["kp-generic"].lecture_suitability > evaluations["kp-local"].lecture_suitability
    )
