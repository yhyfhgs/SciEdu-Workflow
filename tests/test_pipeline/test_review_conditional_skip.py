"""Stage1Review / Stage2Review 条件化跳过逻辑"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sciforge.config.settings import AppConfig
from sciforge.core.models import (
    AnnotatedItem,
    Discipline,
    ExtractedBatch,
)
from sciforge.pipeline.llm_schemas import (
    SingleAnnotationLLM,
    SingleItemExtractLLM,
    StructuralContextLLM,
)
from sciforge.pipeline.stages.stage1_review import Stage1Review
from sciforge.pipeline.stages.stage2_review import Stage2Review

# ── Fixture helpers ──────────────────────────────────────────────


def _mk_item(
    *,
    item_type: str = "definition",
    category: str = "knowledge",
    solution_status: str = "no_solution_in_source",
    number: str = "1.1",
    proof: str | None = None,
    solution: str | None = None,
) -> SingleItemExtractLLM:
    return SingleItemExtractLLM(
        category=category,  # type: ignore[arg-type]
        item_type=item_type,
        number=number,
        corrected_content="content of the item for testing purposes",
        corrected_proof=proof,
        corrected_solution=solution,
        solution_status=solution_status,  # type: ignore[arg-type]
        structural_context=StructuralContextLLM(),
        start_line=1,
        end_line=5,
    )


def _mk_annotation(
    *,
    tags: list[str] | None = None,
    taxonomy_path: str = "math.analysis.limits",
    difficulty: int = 5,
    question_type: str | None = None,
) -> SingleAnnotationLLM:
    return SingleAnnotationLLM(
        subject="real_analysis:实分析",
        taxonomy_path=taxonomy_path,
        tags=tags or ["math:数学", "analysis:分析", "limits:极限"],
        difficulty=difficulty,
        question_type=question_type,
        methods=[],
    )


def _mk_batch(items: list[SingleItemExtractLLM]) -> ExtractedBatch:
    return ExtractedBatch(
        item_ids=list(range(len(items))),
        items=items,
        window_lines=["line 1\n", "line 2\n"],
        window_line_range=(1, 2),
        window_last_line=2,
        block_index=0,
    )


class _DummyLLM:
    """占位 LLM；测试中仅测试跳过逻辑不会走到 LLM 调用。"""

    async def generate_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("Should not be called when skip applies")


# ── Stage1Review tests ───────────────────────────────────────────


def test_stage1_skip_all_low_risk_definitions(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage1Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    batch = _mk_batch([
        _mk_item(item_type="definition", number="1.1"),
        _mk_item(item_type="remark", number="1.2"),
    ])
    assert r._should_skip_review(batch) is True


def test_stage1_no_skip_if_contains_theorem(tmp_path: Path) -> None:
    """theorem 是高危类型，不允许跳过"""
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage1Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    batch = _mk_batch([
        _mk_item(item_type="definition", number="1.1"),
        _mk_item(item_type="theorem", number="1.2", proof="proof text", solution_status="complete"),
    ])
    assert r._should_skip_review(batch) is False


def test_stage1_no_skip_if_exercise_present(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage1Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    batch = _mk_batch([
        _mk_item(
            item_type="exercise", category="question", number="1",
            solution="sol", solution_status="complete",
        ),
    ])
    assert r._should_skip_review(batch) is False


def test_stage1_no_skip_large_batch(tmp_path: Path) -> None:
    """batch > 3 条 → 不跳"""
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage1Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    batch = _mk_batch([_mk_item(item_type="definition", number=f"1.{i}") for i in range(1, 5)])
    assert r._should_skip_review(batch) is False


def test_stage1_no_skip_on_jump_in_numbers(tmp_path: Path) -> None:
    """相邻条目 number 跳号 → 不跳"""
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage1Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    batch = _mk_batch([
        _mk_item(item_type="definition", number="1.1"),
        _mk_item(item_type="definition", number="1.5"),  # 跳 2/3/4
    ])
    assert r._should_skip_review(batch) is False


def test_stage1_no_skip_mixed_solution_status(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage1Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    batch = _mk_batch([
        _mk_item(item_type="definition", number="1.1"),
        _mk_item(item_type="remark", number="1.2", proof="hmm", solution_status="complete"),
    ])
    # 两种 solution_status 混杂 → 不跳
    assert r._should_skip_review(batch) is False


def test_stage1_skip_disabled_by_config(tmp_path: Path) -> None:
    """review_conditional_enabled=False → 跳过逻辑不生效（_should_skip_review 仍计算，
    但 conditional_skip 为 False 时 run() 不会使用它）"""
    cfg = AppConfig(review_conditional_enabled=False)  # type: ignore[call-arg]
    r = Stage1Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    assert r.conditional_skip is False


# ── Stage2Review tests ───────────────────────────────────────────


def test_stage2_skip_clean_low_risk_knowledge(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="definition", category="knowledge"),
        annotation=_mk_annotation(difficulty=5),
    )
    assert r._should_skip_review(annotated) is True


def test_stage2_no_skip_bad_root_tag(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="definition"),
        annotation=_mk_annotation(tags=["physics:物理", "mechanics:力学"]),  # 错误学科根
    )
    assert r._should_skip_review(annotated) is False


def test_stage2_no_skip_extreme_difficulty_low(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="definition"),
        annotation=_mk_annotation(difficulty=1),
    )
    assert r._should_skip_review(annotated) is False


def test_stage2_no_skip_extreme_difficulty_high(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="definition"),
        annotation=_mk_annotation(difficulty=10),
    )
    assert r._should_skip_review(annotated) is False


def test_stage2_no_skip_wrong_taxonomy_prefix(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="definition"),
        annotation=_mk_annotation(taxonomy_path="physics.mechanics"),
    )
    assert r._should_skip_review(annotated) is False


def test_stage2_no_skip_taxonomy_prefix_without_dot_boundary(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="definition"),
        annotation=_mk_annotation(taxonomy_path="mathematics.analysis"),
    )
    assert r._should_skip_review(annotated) is False


def test_stage2_no_skip_high_risk_question_with_question_type(tmp_path: Path) -> None:
    """高危类型且有 question_type → 不跳"""
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="exercise", category="question",
                      solution="sol", solution_status="complete"),
        annotation=_mk_annotation(question_type="calculation"),
    )
    assert r._should_skip_review(annotated) is False


def test_stage2_no_skip_tag_too_few(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="definition"),
        annotation=_mk_annotation(tags=["math:数学"]),  # 只有 1 段
    )
    assert r._should_skip_review(annotated) is False


def test_stage2_no_skip_tag_too_many(tmp_path: Path) -> None:
    cfg = AppConfig(review_conditional_enabled=True)  # type: ignore[call-arg]
    r = Stage2Review(
        llm_client=_DummyLLM(),  # type: ignore[arg-type]
        config=cfg,
        checkpoint_root=tmp_path,
        discipline=Discipline.MATH,
    )
    annotated = AnnotatedItem(
        item_id=0,
        item=_mk_item(item_type="definition"),
        annotation=_mk_annotation(tags=[
            "math:数学", "analysis:分析", "limits:极限",
            "sequence:序列", "convergence:收敛", "cauchy:柯西", "real:实数",
        ]),  # 7 段 > 6
    )
    assert r._should_skip_review(annotated) is False
