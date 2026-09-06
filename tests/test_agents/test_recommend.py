"""P4 个性化推荐：RecommendEngine 算法 + SaasStore 行为流水测试"""

from __future__ import annotations

from pathlib import Path

from sciforge.agents.recommend import RecommendEngine
from sciforge.core.models import Discipline, PracticeOutcome, Question
from sciforge.storage.saas import BehaviorRecord, SaasStore


def _b(outcome: str, difficulty: int, tags: list[str], qid: str = "") -> BehaviorRecord:
    return BehaviorRecord(
        user_id="u1", question_id=qid, outcome=outcome, difficulty=difficulty, tags=tags
    )


def _q(qid: str, difficulty: int, tags: list[str]) -> Question:
    return Question(id=qid, discipline=Discipline.MATH, difficulty=difficulty, tags=tags)


# ── 画像构建 ──────────────────────────────────────────


def test_build_profile_aggregates_accuracy_and_mastery() -> None:
    eng = RecommendEngine()
    behaviors = [
        _b(PracticeOutcome.CORRECT, 4, ["math:数学", "calc:微积分"]),
        _b(PracticeOutcome.CORRECT, 6, ["math:数学", "calc:微积分"]),
        _b(PracticeOutcome.INCORRECT, 7, ["math:数学", "algebra:代数"]),
        _b(PracticeOutcome.SKIPPED, 5, ["math:数学"]),  # skipped 不计入正确率
    ]
    p = eng.build_profile("u1", behaviors)
    assert p.total_attempts == 4
    assert p.correct == 2
    # 已答 3 题（不含 skipped），对 2 → 2/3（引擎保留 4 位小数）
    assert p.accuracy == round(2 / 3, 4)
    # 掌握难度 = 答对题难度均值 (4+6)/2 = 5
    assert p.mastered_difficulty == 5.0


def test_build_profile_identifies_weak_tags() -> None:
    eng = RecommendEngine()
    behaviors = [
        # algebra 全错 → 薄弱
        _b(PracticeOutcome.INCORRECT, 5, ["algebra:代数"]),
        _b(PracticeOutcome.INCORRECT, 5, ["algebra:代数"]),
        # geometry 全对 → 不薄弱
        _b(PracticeOutcome.CORRECT, 5, ["geometry:几何"]),
    ]
    p = eng.build_profile("u1", behaviors)
    assert "algebra:代数" in p.weak_tags
    assert "geometry:几何" not in p.weak_tags
    assert p.tag_accuracy["algebra:代数"] == 0.0
    assert p.tag_accuracy["geometry:几何"] == 1.0


def test_build_profile_cold_start_defaults() -> None:
    eng = RecommendEngine()
    p = eng.build_profile("u1", [])
    assert p.total_attempts == 0
    assert p.accuracy == 0.0
    assert p.mastered_difficulty == 3.0  # 默认
    assert p.weak_tags == []


# ── 推荐排序 ──────────────────────────────────────────


def test_recommend_targets_zpd_difficulty() -> None:
    """掌握难度 5 → 目标 6（ZPD 步长 1）；难度 6 的题应排最前。"""
    eng = RecommendEngine()
    behaviors = [_b(PracticeOutcome.CORRECT, 5, ["math:数学"])]
    p = eng.build_profile("u1", behaviors)
    candidates = [_q("easy", 2, []), _q("target", 6, []), _q("hard", 10, [])]
    recs = eng.recommend(p, candidates, limit=3)
    assert recs[0].question_id == "target"


def test_recommend_boosts_weak_tags() -> None:
    """同难度下，命中薄弱标签的题得分更高。"""
    eng = RecommendEngine()
    behaviors = [
        _b(PracticeOutcome.INCORRECT, 5, ["algebra:代数"]),
        _b(PracticeOutcome.CORRECT, 5, ["math:数学"]),  # 设掌握难度 5 → 目标 6
    ]
    p = eng.build_profile("u1", behaviors)
    assert "algebra:代数" in p.weak_tags
    # 两题同为目标难度 6，仅 tag 不同
    candidates = [_q("plain", 6, ["geometry:几何"]), _q("weak", 6, ["algebra:代数"])]
    recs = eng.recommend(p, candidates, limit=2)
    assert recs[0].question_id == "weak"
    assert "algebra:代数" in recs[0].matched_weak_tags


def test_recommend_excludes_attempted() -> None:
    eng = RecommendEngine()
    behaviors = [_b(PracticeOutcome.CORRECT, 5, ["math:数学"], qid="done")]
    p = eng.build_profile("u1", behaviors)
    candidates = [_q("done", 6, []), _q("fresh", 6, [])]
    recs = eng.recommend(p, candidates, limit=10)
    ids = [r.question_id for r in recs]
    assert "done" not in ids
    assert "fresh" in ids


def test_recommend_empty_candidates() -> None:
    eng = RecommendEngine()
    p = eng.build_profile("u1", [])
    assert eng.recommend(p, [], limit=5) == []


# ── SaasStore 行为流水 ──────────────────────────────────


def test_saas_record_and_get_behavior(tmp_path: Path) -> None:
    with SaasStore(tmp_path / "saas.db") as store:
        store.record_behavior(
            "u1", "q1", "correct", difficulty=6, tags=["math:数学"], time_spent_ms=12000
        )
        store.record_behavior("u1", "q2", "incorrect", difficulty=7, tags=["algebra:代数"])
        store.record_behavior("u2", "q3", "correct", difficulty=4)  # 他人行为隔离

        recs = store.get_behavior("u1")
        assert len(recs) == 2
        # 倒序：最近的 q2 在前
        assert recs[0].question_id == "q2"
        assert recs[0].outcome == "incorrect"
        assert recs[1].tags == ["math:数学"]
        assert recs[1].time_spent_ms == 12000
        # u2 行为不混入 u1
        assert all(r.user_id == "u1" for r in recs)


def test_end_to_end_record_to_recommend(tmp_path: Path) -> None:
    """行为入库 → 取出建画像 → 推荐，全链路。"""
    eng = RecommendEngine()
    with SaasStore(tmp_path / "saas.db") as store:
        store.record_behavior("u1", "old", "correct", difficulty=5, tags=["math:数学"])
        store.record_behavior("u1", "bad", "incorrect", difficulty=5, tags=["algebra:代数"])
        profile = eng.build_profile("u1", store.get_behavior("u1"))

    candidates = [_q("old", 6, []), _q("rec1", 6, ["algebra:代数"]), _q("rec2", 2, [])]
    recs = eng.recommend(profile, candidates, limit=5)
    ids = [r.question_id for r in recs]
    assert "old" not in ids  # 已做过
    assert ids[0] == "rec1"  # 目标难度 + 薄弱标签命中
