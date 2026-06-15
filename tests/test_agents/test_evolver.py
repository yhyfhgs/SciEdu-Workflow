"""Evolver 进化闭环单测（缺陷②修复）

覆盖：
- solver / verification 两种 failure_kind 各产出带正确 parent_id 与
  generation = parent_generation + 1 的 CandidateRecord
- improvement_directive 来自 LLM 的 changes_made
- improvement_strategy == failure_kind，改进后 Question 沿用原候选的学科/类型/学科领域/标签
- content_latex → content；estimated_difficulty 非法时回退父候选难度
- LLM 调用失败 / 输出无 content → 返回 None（不抛）

mock 风格仿照 tests/test_agents/test_qgen_agent.py：MagicMock + AsyncMock(generate_json)。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from sciforge.agents.qgen.evolver import Evolver
from sciforge.core.models import CandidateRecord, Discipline, Question


def _candidate() -> Question:
    return Question(
        discipline=Discipline.MATH,
        question_type="proof",
        content="设 $f$ 在 $[0,1]$ 上连续，证明 $f$ 有界。",
        difficulty=5,
        subject="real_analysis:实分析",
        tags=["math:数学", "analysis:数学分析"],
    )


def _improved_payload(difficulty: int = 6) -> dict[str, object]:
    return {
        "content_latex": "设 $f$ 在 $[0,1]$ 上连续，证明 $f$ 在 $[0,1]$ 上一致连续。",
        "changes_made": "补充紧致区间条件，明确求证目标为一致连续",
        "solution_sketch": "由 Heine-Cantor 定理，闭区间上连续即一致连续。",
        "knowledge_points_used": ["一致连续", "紧致性"],
        "methods_required": ["Heine-Cantor 定理"],
        "estimated_difficulty": difficulty,
    }


def _evolver(payload: dict[str, object]) -> Evolver:
    llm = MagicMock()
    llm.generate_json = AsyncMock(return_value=payload)
    return Evolver(llm=llm, discipline=Discipline.MATH)


async def test_evolve_solver_failure_produces_child_record() -> None:
    """solver 失败 → 正确 parent_id / generation / strategy / directive"""
    parent = _candidate()
    evolver = _evolver(_improved_payload())

    record = await evolver.evolve(
        parent,
        failure_kind="solver",
        failure_detail="求解引擎置信度 low，缺关键边界条件",
        parent_generation=0,
    )

    assert isinstance(record, CandidateRecord)
    assert record.parent_id == parent.id
    assert record.generation == 1
    assert record.improvement_strategy == "solver"
    assert record.improvement_directive == "补充紧致区间条件，明确求证目标为一致连续"
    # content_latex → content
    assert "一致连续" in record.question.content
    assert record.question.content == _improved_payload()["content_latex"]


async def test_evolve_verification_failure_produces_child_record() -> None:
    """verification 失败 → strategy=verification 且 generation 递增"""
    parent = _candidate()
    evolver = _evolver(_improved_payload(difficulty=7))

    record = await evolver.evolve(
        parent,
        failure_kind="verification",
        failure_detail="math_correctness 未通过：条件不足以推出结论",
        parent_generation=2,
    )

    assert record is not None
    assert record.parent_id == parent.id
    assert record.generation == 3  # parent_generation + 1
    assert record.improvement_strategy == "verification"
    assert record.improvement_directive == "补充紧致区间条件，明确求证目标为一致连续"
    assert record.question.difficulty == 7


async def test_evolve_inherits_parent_fields() -> None:
    """改进后 Question 沿用原候选的 discipline/question_type/subject/tags"""
    parent = _candidate()
    evolver = _evolver(_improved_payload())

    record = await evolver.evolve(
        parent,
        failure_kind="solver",
        failure_detail="置信度不足",
    )

    assert record is not None
    q = record.question
    assert q.discipline == parent.discipline
    assert q.question_type == parent.question_type
    assert q.subject == parent.subject
    assert q.tags == parent.tags
    # 新题是独立对象，id 不应等于父题
    assert q.id != parent.id


async def test_evolve_default_parent_generation_is_zero() -> None:
    """不传 parent_generation 时默认 0 → 子代 generation=1"""
    evolver = _evolver(_improved_payload())
    record = await evolver.evolve(
        _candidate(),
        failure_kind="solver",
        failure_detail="x",
    )
    assert record is not None
    assert record.generation == 1


async def test_evolve_difficulty_fallback_on_invalid() -> None:
    """estimated_difficulty 非法 → 回退父候选难度（不抛）"""
    parent = _candidate()  # difficulty=5
    payload = _improved_payload()
    payload["estimated_difficulty"] = "not-a-number"
    evolver = _evolver(payload)

    record = await evolver.evolve(
        parent,
        failure_kind="verification",
        failure_detail="latex_valid 未通过",
    )
    assert record is not None
    assert record.question.difficulty == 5


async def test_evolve_returns_none_on_llm_failure() -> None:
    """LLM 调用抛异常 → 返回 None，不向上抛"""
    llm = MagicMock()
    llm.generate_json = AsyncMock(side_effect=RuntimeError("boom"))
    evolver = Evolver(llm=llm, discipline=Discipline.MATH)

    record = await evolver.evolve(
        _candidate(),
        failure_kind="solver",
        failure_detail="置信度不足",
    )
    assert record is None


async def test_evolve_returns_none_on_empty_content() -> None:
    """LLM 输出无 content/content_latex → 返回 None"""
    payload = _improved_payload()
    payload["content_latex"] = ""
    payload["content"] = ""
    evolver = _evolver(payload)

    record = await evolver.evolve(
        _candidate(),
        failure_kind="verification",
        failure_detail="non_trivial 未通过",
    )
    assert record is None


async def test_evolve_accepts_content_key_fallback() -> None:
    """LLM 用新 key 'content' 而非 content_latex 时也能解析"""
    payload: dict[str, object] = {
        "content": "改进后的题面（用 content key）",
        "changes_made": "精简表述",
        "estimated_difficulty": 4,
    }
    evolver = _evolver(payload)

    record = await evolver.evolve(
        _candidate(),
        failure_kind="solver",
        failure_detail="表述歧义",
    )
    assert record is not None
    assert record.question.content == "改进后的题面（用 content key）"
    assert record.question.difficulty == 4
