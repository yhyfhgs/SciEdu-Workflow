"""schema v13 Knowledge Evaluation 持久化、门禁查询与 review queue。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import Discipline, Question
from sciforge.pipeline.concept_linker import link_items_to_concepts
from sciforge.pipeline.evaluation import evaluate_item_rows
from sciforge.storage.query import QualityGateFilter
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


@pytest.fixture
def backend(tmp_path: Path) -> SQLiteStorageBackend:
    db = SQLiteStorageBackend(tmp_path / "evaluation.db")
    yield db
    db.close()


def _seed(db: SQLiteStorageBackend) -> None:
    good = Question(
        id="q-good",
        discipline=Discipline.MATH,
        question_type="proof",
        content="设 $f$ 在闭区间连续，证明它一致连续。",
        solution_content="证明：由紧致性取有限子覆盖，从而得到统一的 $\\delta$。",
        difficulty=7,
        taxonomy_path="math.analysis.continuity.uniform_continuity",
        tags=["math:数学", "analysis:分析", "uniform_continuity:一致连续"],
        source_file="book.md",
        start_line=1,
        end_line=8,
    )
    risky = Question(
        id="q-risky",
        discipline=Discipline.MATH,
        question_type="calculation",
        content="求 $1+1$。",
        solution_content=None,
        difficulty=1,
        taxonomy_path="",
        tags=[],
        source_file="book.md",
    )
    db.save_batch([], [good, risky])
    concepts = link_items_to_concepts(db.concept_link_rows("book.md", "math"), discipline="math")
    db.save_concept_graph(concepts)


def test_evaluation_roundtrip_quality_gate_and_review_queue(
    backend: SQLiteStorageBackend,
) -> None:
    _seed(backend)
    batch = evaluate_item_rows(backend.evaluation_rows("book.md", "math"))
    first = backend.save_evaluation_batch(batch)
    second = backend.save_evaluation_batch(batch)

    assert first == {"evaluations": 2, "review_items": len(batch.review_items)}
    assert second == first
    good = backend.load_item_evaluation("q-good")
    assert good.difficulty_dims.overall == good.difficulty_overall
    assert good.extraction_quality >= 0.7
    eligible = backend.quality_eligible_item_ids(item_kind="question", difficulty_range=(6, 9))
    assert eligible == ["q-good"]
    queue = backend.load_review_queue()
    assert {item.item_id for item in queue} == {"q-risky"}
    assert {item.reason for item in queue} >= {"incomplete_solution"}


def test_rubric_versions_coexist(backend: SQLiteStorageBackend) -> None:
    _seed(backend)
    rows = backend.evaluation_rows("book.md", "math")
    backend.save_evaluation_batch(evaluate_item_rows(rows, rubric_version="v1"))
    backend.save_evaluation_batch(evaluate_item_rows(rows, rubric_version="v2"))

    count = backend._conn.execute(
        "SELECT COUNT(*) FROM item_evaluations WHERE item_id = 'q-good'"
    ).fetchone()[0]
    assert count == 2
    assert backend.load_item_evaluation("q-good", rubric_version="v1").rubric_version == "v1"
    assert backend.load_item_evaluation("q-good", rubric_version="v2").rubric_version == "v2"


def test_quality_gate_is_applied_before_search_limit(backend: SQLiteStorageBackend) -> None:
    noisy = [
        Question(
            id=f"bad-{index:03d}",
            content=f"低质量未完成题目 {index}。",
            source_file="bulk.md",
            order_index=index,
        )
        for index in range(500)
    ]
    good = Question(
        id="good-after-limit",
        discipline=Discipline.MATH,
        question_type="proof",
        content="证明闭区间连续函数一致连续，并给出完整的有限覆盖论证。",
        solution_content="由 Heine-Borel 定理取有限子覆盖，取最小半径即可。",
        difficulty=7,
        taxonomy_path="math.analysis.continuity.uniform_continuity",
        tags=["math:数学", "analysis:分析", "uniform_continuity:一致连续"],
        source_file="bulk.md",
        start_line=2001,
        end_line=2010,
        order_index=501,
    )
    backend.save_batch([], [*noisy, good])
    batch = evaluate_item_rows(backend.evaluation_rows("bulk.md", "math"))
    backend.save_evaluation_batch(batch)

    ids = backend.search_questions(
        {"quality_gate": QualityGateFilter(item_kind="question")}
    )

    assert ids == ["good-after-limit"]


def test_quality_gate_legacy_fallback_only_when_corpus_has_no_evaluations(
    backend: SQLiteStorageBackend,
) -> None:
    backend.save_batch(
        [],
        [
            Question(
                id="legacy",
                content="尚未迁移量规但仍应在纯旧库中可见。",
                source_file="legacy.md",
                order_index=1,
            ),
            Question(
                id="legacy-uncertain",
                content="无评价时也不得绕过摄取状态门禁。",
                source_file="legacy.md",
                order_index=2,
                ingestion_status="uncertain",
            ),
        ],
    )
    policy = QualityGateFilter(item_kind="question")

    assert backend.search_questions({"quality_gate": policy}) == ["legacy"]

    backend.save_batch(
        [],
        [
            Question(
                id="evaluated-risky",
                content="求一加一。",
                source_file="evaluated.md",
                order_index=2,
            )
        ],
    )
    backend.save_evaluation_batch(
        evaluate_item_rows(
            backend.evaluation_rows("evaluated.md", "math"), rubric_version="v1"
        )
    )
    # 一旦该 kind/rubric/source 存在评价，兼容回退整体结束；逐条无证据不再
    # 等同于质量通过，且已评价但不达标的题也被排除。
    assert backend.search_questions({"quality_gate": policy}) == []


def test_nonaccepted_ingestion_is_reviewed_and_never_quality_eligible(
    backend: SQLiteStorageBackend,
) -> None:
    uncertain = Question(
        id="q-uncertain",
        discipline=Discipline.MATH,
        question_type="proof",
        content="证明闭区间连续函数一致连续，并给出有限覆盖论证。",
        solution_content="由紧致性取有限子覆盖，再取有限个半径的最小值。",
        difficulty=7,
        taxonomy_path="math.analysis.continuity.uniform_continuity",
        tags=["math:数学", "analysis:分析", "uniform_continuity:一致连续"],
        source_file="uncertain.md",
        start_line=1,
        end_line=8,
        ingestion_status="uncertain",
    )
    backend.save_question(uncertain)
    batch = evaluate_item_rows(backend.evaluation_rows("uncertain.md", "math"))
    backend.save_evaluation_batch(batch)

    evaluation = backend.load_item_evaluation(uncertain.id)
    assert evaluation.extraction_quality == 0.0
    queued = backend.load_review_queue()
    status_item = next(item for item in queued if item.item_id == uncertain.id)
    assert status_item.reason == "non_accepted_ingestion_status"
    assert status_item.details["status"] == "uncertain"

    # 即使外部系统错误地把量规分数改成满分，结构化摄取门禁仍不可绕过。
    backend._conn.execute(
        "UPDATE item_evaluations SET extraction_quality = 1, solution_completeness = 1 "
        "WHERE item_id = ?",
        (uncertain.id,),
    )
    backend._conn.commit()
    assert backend.quality_eligible_item_ids(item_kind="question") == []
    assert backend.search_questions(
        {"quality_gate": QualityGateFilter(item_kind="question")}
    ) == []


def test_quality_gate_rejects_item_kind_mismatch(backend: SQLiteStorageBackend) -> None:
    with pytest.raises(ValueError, match="does not match search table"):
        backend.search_questions(
            {"quality_gate": QualityGateFilter(item_kind="knowledge")}
        )
    with pytest.raises(ValueError, match="does not match search table"):
        backend.search_knowledge(
            {"quality_gate": QualityGateFilter(item_kind="question")}
        )


def test_quality_gate_blocks_pending_concepts_and_unresolved_review_work(
    backend: SQLiteStorageBackend,
) -> None:
    _seed(backend)
    backend.save_evaluation_batch(
        evaluate_item_rows(backend.evaluation_rows("book.md", "math"))
    )
    policy = QualityGateFilter(item_kind="question")
    assert backend.search_questions({"quality_gate": policy}) == ["q-good"]

    backend._conn.execute(
        "INSERT INTO concepts "
        "(id, discipline, branch, canonical_en, status, depth) "
        "VALUES ('math.pending', 'math', 'analysis', 'pending', 'pending', 1)"
    )
    backend._conn.execute(
        "INSERT INTO item_concepts (item_id, item_kind, concept_id, role, weight) "
        "VALUES ('q-good', 'question', 'math.pending', 'uses', 1.0)"
    )
    backend._conn.commit()
    assert backend.search_questions({"quality_gate": policy}) == []

    backend._conn.execute(
        "UPDATE concepts SET status = 'active' WHERE id = 'math.pending'"
    )
    backend._conn.execute(
        "INSERT INTO review_queue "
        "(item_id, item_kind, rubric_version, reason, status) "
        "VALUES ('q-good', 'question', 'v1', 'manual_review', 'open')"
    )
    backend._conn.commit()
    assert backend.search_questions({"quality_gate": policy}) == []

    backend._conn.execute(
        "UPDATE review_queue SET status = 'resolved' WHERE item_id = 'q-good'"
    )
    backend._conn.commit()
    assert backend.search_questions({"quality_gate": policy}) == ["q-good"]


def test_literal_text_query_filters_before_limit_and_escapes_wildcards(
    backend: SQLiteStorageBackend,
) -> None:
    noise = [
        Question(id=f"noise-{index:03d}", content=f"普通题目 {index}", order_index=index)
        for index in range(500)
    ]
    target = Question(
        id="target-after-limit",
        content="研究 100%_uniform 这个含通配符外观的字面术语。",
        order_index=501,
    )
    backend.save_batch([], [*noise, target])

    ids = backend.search_questions({"text_query": "100%_uniform"})

    assert ids == ["target-after-limit"]
