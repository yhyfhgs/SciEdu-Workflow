"""RecommendEngine —— 基于难度/标签的个性化推荐（纯算法，无 LLM）。

两步：
1. ``build_profile``：把 ``BehaviorRecord`` 流水聚合成 ``UserProfile``
   —— 掌握难度（答对题的加权均值）+ 各标签正确率 + 薄弱标签。
2. ``recommend``：对候选题打分排序
   —— "最近发展区"（目标难度 = 掌握难度 + 步长）接近度 + 薄弱标签命中加成，
   排除已做过的题。

权重可调；冷启动（无行为）退化为按默认难度推荐、无标签偏好。
"""

from __future__ import annotations

import structlog

from sciforge.core.models import (
    PracticeOutcome,
    Question,
    QuestionRecommendation,
    UserProfile,
)
from sciforge.storage.saas import BehaviorRecord

logger = structlog.get_logger()

# 评分权重：难度贴合度 vs 薄弱标签命中
_W_DIFFICULTY = 0.6
_W_WEAK_TAG = 0.4
# 最近发展区步长：目标难度 = 掌握难度 + 此值
_ZPD_STEP = 1.0
# 薄弱标签判定阈值：该标签正确率低于此值且至少答过 1 次
_WEAK_TAG_THRESHOLD = 0.6
_DEFAULT_MASTERED = 3.0


class RecommendEngine:
    """难度 + 标签个性化推荐引擎（无状态，可复用）。"""

    def __init__(
        self,
        *,
        w_difficulty: float = _W_DIFFICULTY,
        w_weak_tag: float = _W_WEAK_TAG,
        zpd_step: float = _ZPD_STEP,
        weak_tag_threshold: float = _WEAK_TAG_THRESHOLD,
    ) -> None:
        self.w_difficulty = w_difficulty
        self.w_weak_tag = w_weak_tag
        self.zpd_step = zpd_step
        self.weak_tag_threshold = weak_tag_threshold

    # ── 画像构建 ──────────────────────────────────────────

    def build_profile(self, user_id: str, behaviors: list[BehaviorRecord]) -> UserProfile:
        """把行为流水聚合成用户能力画像。"""
        answered = [b for b in behaviors if b.outcome != PracticeOutcome.SKIPPED]
        correct = [b for b in answered if b.outcome == PracticeOutcome.CORRECT]
        accuracy = len(correct) / len(answered) if answered else 0.0

        # 掌握难度 = 答对题难度的均值（无答对则默认）
        mastered = (
            sum(b.difficulty for b in correct) / len(correct) if correct else _DEFAULT_MASTERED
        )

        # 各标签正确率：tag 段 → (correct, answered)
        tag_stats: dict[str, list[int]] = {}
        for b in answered:
            is_correct = 1 if b.outcome == PracticeOutcome.CORRECT else 0
            for tag in b.tags:
                stat = tag_stats.setdefault(tag, [0, 0])
                stat[0] += is_correct
                stat[1] += 1
        tag_accuracy = {t: (c / n if n else 0.0) for t, (c, n) in tag_stats.items()}

        # 薄弱标签：正确率低于阈值，按正确率升序（最弱在前）
        weak = sorted(
            (t for t, acc in tag_accuracy.items() if acc < self.weak_tag_threshold),
            key=lambda t: tag_accuracy[t],
        )

        return UserProfile(
            user_id=user_id,
            total_attempts=len(behaviors),
            correct=len(correct),
            accuracy=round(accuracy, 4),
            mastered_difficulty=round(mastered, 4),
            tag_accuracy={t: round(a, 4) for t, a in tag_accuracy.items()},
            weak_tags=weak,
            attempted_question_ids=[b.question_id for b in behaviors if b.question_id],
        )

    # ── 推荐排序 ──────────────────────────────────────────

    def recommend(
        self,
        profile: UserProfile,
        candidates: list[Question],
        *,
        limit: int = 10,
        exclude_attempted: bool = True,
    ) -> list[QuestionRecommendation]:
        """对候选题按 (难度贴合度, 薄弱标签命中) 打分，返回 top-limit。"""
        target = max(1.0, min(10.0, profile.mastered_difficulty + self.zpd_step))
        weak_set = set(profile.weak_tags)
        attempted = set(profile.attempted_question_ids) if exclude_attempted else set()

        recs: list[QuestionRecommendation] = []
        for q in candidates:
            if q.id in attempted:
                continue
            # 难度贴合度：与目标难度距离归一化到 0..1（距离越小越高）
            diff_score = 1.0 - abs(q.difficulty - target) / 9.0
            # 薄弱标签命中
            matched = [t for t in q.tags if t in weak_set]
            tag_score = min(1.0, len(matched) / 2.0) if weak_set else 0.0
            score = self.w_difficulty * diff_score + self.w_weak_tag * tag_score
            recs.append(
                QuestionRecommendation(
                    question_id=q.id,
                    score=round(score, 4),
                    difficulty=q.difficulty,
                    matched_weak_tags=matched,
                    reason=self._reason(target, q.difficulty, matched),
                )
            )

        recs.sort(key=lambda r: r.score, reverse=True)
        logger.info(
            "recommend_ranked",
            user_id=profile.user_id,
            target_difficulty=round(target, 2),
            candidates=len(candidates),
            returned=min(limit, len(recs)),
        )
        return recs[:limit]

    @staticmethod
    def _reason(target: float, difficulty: int, matched: list[str]) -> str:
        parts = [f"难度{difficulty}≈目标{target:.1f}"]
        if matched:
            parts.append(f"覆盖薄弱标签 {', '.join(matched)}")
        return "；".join(parts)
