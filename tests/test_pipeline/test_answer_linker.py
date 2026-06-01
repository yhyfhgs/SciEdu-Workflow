"""跨章节题答回填（Phase 2 #3）回归测试"""

from __future__ import annotations

from sciforge.core.models import Discipline, Question
from sciforge.pipeline.answer_linker import link_answers, looks_like_answer


def test_looks_like_answer() -> None:
    assert looks_like_answer("解：由定理 3.1 得 x=5")
    assert looks_like_answer("3.2 解答：先求导")
    assert looks_like_answer("证明. 设 x ...")
    assert looks_like_answer("**Solution.** Let x = 1")
    assert not looks_like_answer("求下列函数的极限")
    assert not looks_like_answer("设 f 在 [a,b] 上连续，证明 f 一致连续")  # "证明" 在句中非起头


def _q(content: str, num: str, subject: str = "analysis:分析", sol: str | None = None) -> Question:
    return Question(
        content=content,
        number=num,
        subject=subject,
        solution_content=sol,
        discipline=Discipline.MATH,
    )


def test_link_problem_and_answer() -> None:
    prob = _q("习题 3.2 求 ∫x²dx", "习题 3.2")
    ans = _q("3.2 解：= 1/3", "3.2")
    res = link_answers([prob, ans])
    assert len(res.backfills) == 1
    qid, sol = res.backfills[0]
    assert qid == prob.id
    assert "1/3" in sol
    assert ans.id in res.delete_ids


def test_no_merge_when_two_problems() -> None:
    a = _q("习题 3.2 求极限", "3.2")
    b = _q("习题 3.2 另一问", "3.2")
    res = link_answers([a, b])
    assert res.backfills == []


def test_no_merge_without_answer() -> None:
    a = _q("习题 3.2 求极限", "3.2")
    b = _q("习题 3.3 求导数", "3.3")
    res = link_answers([a, b])
    assert res.backfills == []


def test_answer_solution_content_preferred() -> None:
    """答案条目若把解答放在 solution_content，回填取该字段。"""
    prob = _q("习题 5 求值", "5")
    ans = _q("5 解答", "5", sol="x = 42")
    res = link_answers([prob, ans])
    assert res.backfills[0][1] == "x = 42"


def test_subject_isolation() -> None:
    """不同 subject 即使同号也不并。"""
    prob = _q("习题 1 求极限", "1", subject="analysis:分析")
    ans = _q("1 解：见上", "1", subject="algebra:代数")
    res = link_answers([prob, ans])
    assert res.backfills == []
