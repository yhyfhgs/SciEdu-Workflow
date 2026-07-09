"""schema v13 Knowledge Evaluation 持久化、门禁查询与 review queue。"""

from __future__ import annotations

from pathlib import Path

import pytest

from sciforge.core.models import Discipline, Question
from sciforge.pipeline.concept_linker import link_items_to_concepts
from sciforge.pipeline.evaluation import evaluate_item_rows
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
