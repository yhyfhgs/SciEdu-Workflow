"""VerificationPipeline — QGen 第 4 阶段：多层验证"""

from __future__ import annotations

import structlog

from sciforge.core.models import (
    ContextBundle,
    DifficultyScore,
    Discipline,
    Question,
    Solution,
    VerificationResult,
)
from sciforge.llm.client import LLMClient
from sciforge.pipeline.retry import call_llm_json_with_retry
from sciforge.prompts.templates import get_generation_prompt_template

logger = structlog.get_logger()


class VerificationPipeline:
    """对候选题 + 解答做多层验证

    Check 清单:
    1. solve_confidence: solver 的置信度不为 low
    2. review_passed: 独立 LLM 审核判断题目逻辑是否成立
    3. non_trivial: 题干长度足够
    """

    def __init__(
        self,
        review_llm: LLMClient,
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        self.review_llm = review_llm  # 独立实例避免确认偏差
        self.discipline = discipline
        self._review_template = get_generation_prompt_template(
            "verification/review.txt", discipline
        )

    async def verify(
        self,
        candidate: Question,
        solution: Solution,
        context: ContextBundle | None = None,
    ) -> VerificationResult:
        """执行全部验证检查"""
        checks: dict[str, bool] = {}
        suggestions: list[str] = []

        # Check 1: 解题置信度
        checks["solve_confidence"] = solution.confidence in ("high", "medium")
        if not checks["solve_confidence"]:
            suggestions.append("求解引擎置信度低，题目可能存在问题")

        # Check 2: 数学/物理正确性（独立审核 LLM）
        review = await self._check_with_review_llm(candidate)
        checks["review_passed"] = bool(review.get("passed", False))
        if not checks["review_passed"]:
            raw_issues = review.get("issues", [])
            if isinstance(raw_issues, list):
                suggestions.extend(str(i) for i in raw_issues)

        # Check 3: 非平凡性
        checks["non_trivial"] = len(candidate.content) > 30

        # Phase 6c: 从 review 提取 difficulty_scores（若 prompt 按新模板输出）
        difficulty_score: DifficultyScore | None = None
        raw_scores = review.get("difficulty_scores")
        if isinstance(raw_scores, dict) and checks["review_passed"]:
            try:
                difficulty_score = DifficultyScore(
                    concept_count=int(raw_scores.get("concept_count", 5)),
                    step_count=int(raw_scores.get("step_count", 5)),
                    abstraction_level=int(raw_scores.get("abstraction_level", 5)),
                    technique_novelty=int(raw_scores.get("technique_novelty", 5)),
                    condition_parsing=int(raw_scores.get("condition_parsing", 5)),
                    computation_load=int(raw_scores.get("computation_load", 5)),
                    proof_structure=int(raw_scores.get("proof_structure", 5)),
                    prerequisite_depth=int(raw_scores.get("prerequisite_depth", 5)),
                    multi_representation=int(raw_scores.get("multi_representation", 5)),
                    metacognition=int(raw_scores.get("metacognition", 5)),
                    reasoning=str(review.get("difficulty_reasoning") or ""),
                )
                difficulty_score.compute_overall()
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "verify_difficulty_parse_failed",
                    error=str(exc)[:200],
                )
                difficulty_score = None

        passed = all(checks.values())
        return VerificationResult(
            passed=passed,
            checks=checks,
            suggestions="\n".join(suggestions) if suggestions else "",
            difficulty_score=difficulty_score,
        )

    async def _check_with_review_llm(self, question: Question) -> dict[str, object]:
        """独立审核 LLM 判断题目合理性"""
        prompt = self._review_template.format(question_latex=question.content)

        try:
            result = await call_llm_json_with_retry(self.review_llm, prompt, max_attempts=2)
            verdict = str(result.get("verdict", "FAIL")).upper()
            return {
                "passed": verdict == "PASS",
                "issues": list(result.get("issues") or []),
                "suggestions": str(result.get("suggestions") or ""),
                # Phase 6c: 透传难度评分字段，由调用方决定是否使用
                "difficulty_scores": result.get("difficulty_scores"),
                "difficulty_reasoning": result.get("difficulty_reasoning", ""),
            }
        except Exception as exc:
            logger.warning("review_failed", error=str(exc)[:200])
            return {"passed": False, "issues": [f"review_error: {exc}"]}
