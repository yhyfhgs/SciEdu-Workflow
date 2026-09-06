"""Stage3Persist._ensure_discipline_root —— 兜底补齐学科根

覆盖：
- tags 缺根 → 前置 tag_root
- tags[0] 已是 tag_root → 不变
- tags 中已含 tag_root 但不在首位 → 去重后前置
- tags[0] 是其他注册学科根 → 跨学科 warning，不修
- taxonomy_path 缺学科前缀 → 前置 discipline.value + "."
- taxonomy_path 已等于或以 discipline.value. 开头 → 不变
- taxonomy_path 首段是其他注册学科 → 不修
"""

from __future__ import annotations

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline, ReviewedAnnotatedItem
from sciforge.pipeline.llm_schemas import SingleAnnotationLLM, SingleItemExtractLLM
from sciforge.pipeline.stages.stage3_persist import Stage3Persist


def _make_stage(discipline: Discipline = Discipline.MATH) -> Stage3Persist:
    cfg = AppConfig()
    return Stage3Persist(
        storage=None,  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=None,
        discipline=discipline,
        tag_tree=None,
    )


def _ann(**overrides) -> SingleAnnotationLLM:
    data = {
        "subject": "real_analysis:实分析",
        "taxonomy_path": "math.analysis.continuity",
        "tags": ["math:数学", "analysis:数学分析"],
        "difficulty": 5,
        "question_type": None,
        "methods": [],
    }
    data.update(overrides)
    return SingleAnnotationLLM.model_validate(data)


class TestTagRoot:
    def test_missing_root_prepended(self):
        stage = _make_stage()
        ann = _ann(tags=["combinatorics:组合数学", "counting:计数"])
        out = stage._ensure_discipline_root(ann, item_id=1)
        assert out.tags[0] == "math:数学"
        assert out.tags == ["math:数学", "combinatorics:组合数学", "counting:计数"]

    def test_root_already_first_unchanged(self):
        stage = _make_stage()
        ann = _ann(tags=["math:数学", "algebra:代数"])
        out = stage._ensure_discipline_root(ann, item_id=2)
        assert out.tags == ["math:数学", "algebra:代数"]

    def test_root_in_middle_deduped_and_prepended(self):
        stage = _make_stage()
        ann = _ann(tags=["analysis:数学分析", "math:数学", "limits:极限"])
        out = stage._ensure_discipline_root(ann, item_id=3)
        assert out.tags == ["math:数学", "analysis:数学分析", "limits:极限"]

    def test_cross_discipline_root_kept(self):
        stage = _make_stage(Discipline.MATH)
        ann = _ann(tags=["physics:物理", "mechanics:力学"])
        out = stage._ensure_discipline_root(ann, item_id=4)
        assert out.tags == ["physics:物理", "mechanics:力学"]

    def test_physics_root_prepended(self):
        stage = _make_stage(Discipline.PHYSICS)
        ann = _ann(
            tags=["mechanics:力学", "newton_laws:牛顿定律"],
            taxonomy_path="physics.mechanics.newton_laws",
        )
        out = stage._ensure_discipline_root(ann, item_id=5)
        assert out.tags[0] == "physics:物理"


class TestTaxonomyRoot:
    def test_missing_root_prepended(self):
        stage = _make_stage()
        ann = _ann(taxonomy_path="combinatorics.counting")
        out = stage._ensure_discipline_root(ann, item_id=10)
        assert out.taxonomy_path == "math.combinatorics.counting"

    def test_root_only_unchanged(self):
        stage = _make_stage()
        ann = _ann(taxonomy_path="math")
        out = stage._ensure_discipline_root(ann, item_id=11)
        assert out.taxonomy_path == "math"

    def test_correct_prefix_unchanged(self):
        stage = _make_stage()
        ann = _ann(taxonomy_path="math.algebra.eigenvalues")
        out = stage._ensure_discipline_root(ann, item_id=12)
        assert out.taxonomy_path == "math.algebra.eigenvalues"

    def test_cross_discipline_taxonomy_kept(self):
        stage = _make_stage(Discipline.MATH)
        ann = _ann(taxonomy_path="physics.mechanics")
        out = stage._ensure_discipline_root(ann, item_id=13)
        assert out.taxonomy_path == "physics.mechanics"


def test_build_persistable_preserves_ingestion_status() -> None:
    stage = _make_stage()
    msg = ReviewedAnnotatedItem(
        item_id=7,
        item=SingleItemExtractLLM(
            category="question",
            item_type="proof",
            corrected_content="证明该结论成立。",
            corrected_solution=None,
            solution_status="no_solution_in_source",
            start_line=10,
            end_line=12,
            status="uncertain",
        ),
        annotation=_ann(question_type="proof"),
        source_file="book.md",
    )

    _, question = stage._build_persistable(msg)

    assert question is not None
    assert question.ingestion_status == "uncertain"


def test_build_persistable_preserves_knowledge_ingestion_status() -> None:
    stage = _make_stage()
    msg = ReviewedAnnotatedItem(
        item_id=8,
        item=SingleItemExtractLLM(
            category="knowledge",
            item_type="theorem",
            corrected_content="连续函数在紧集上一致连续。",
            corrected_proof="由有限子覆盖可得统一半径。",
            solution_status="complete",
            start_line=20,
            end_line=24,
            status="needs_patch",
        ),
        annotation=_ann(question_type=None),
        source_file="book.md",
    )

    knowledge, _ = stage._build_persistable(msg)

    assert knowledge is not None
    assert knowledge.ingestion_status == "needs_patch"
