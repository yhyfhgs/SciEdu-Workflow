"""VerificationPipeline — QGen 第 4 阶段：多层验证"""

from __future__ import annotations

import asyncio

import structlog

from sciforge.core.models import (
    ContextBundle,
    DifficultyScore,
    Discipline,
    NumericalVerification,
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
    4. solution_relevant: 解答确实在解这道题（G5，套餐可配，默认开）
    5. numerical_valid: sympy/numpy 沙箱数值验证未发现矛盾（G4，opt-in）
    """

    def __init__(
        self,
        review_llm: LLMClient,
        discipline: Discipline = Discipline.MATH,
        *,
        enable_solution_relevance: bool = True,
        enable_numerical_verification: bool = False,
        sandbox_timeout: int = 10,
    ) -> None:
        self.review_llm = review_llm  # 独立实例避免确认偏差
        self.discipline = discipline
        self.enable_solution_relevance = enable_solution_relevance
        self.enable_numerical_verification = enable_numerical_verification
        self.sandbox_timeout = sandbox_timeout
        self._review_template = get_generation_prompt_template(
            "verification/review.txt", discipline
        )
        self._relevance_template = (
            get_generation_prompt_template("verification/solution_relevance.txt", discipline)
            if enable_solution_relevance
            else ""
        )
        self._numerical_template = (
            get_generation_prompt_template("verification/generate_numerical_test.txt", discipline)
            if enable_numerical_verification
            else ""
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
        numerical_verification: NumericalVerification | None = None

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

        # Check 4 (G5): 解答相关性 —— 解答是否在解这道题（套餐可配，默认开）。
        # 仅在有解答文本时执行；字段缺失或基础设施故障时降级为通过，不误杀候选。
        if self.enable_solution_relevance and solution.solution_latex.strip():
            checks["solution_relevant"] = await self._check_solution_relevance(candidate, solution)
            if not checks["solution_relevant"]:
                suggestions.append("解答与题目主题不一致（疑似答非所问），请检查题目表述是否清晰")

        # Check 5 (G4): 数值验证 —— 生成 sympy/numpy 代码在沙箱执行，检测数学错误（opt-in）。
        # 仅在明确检测到数值矛盾时拒题；执行失败/超时/无明确结论时降级为通过，不误杀候选。
        if self.enable_numerical_verification and solution.solution_latex.strip():
            numerical_verification = await self._check_numerical(candidate, solution)
            exec_ok = (
                numerical_verification.execution is not None
                and numerical_verification.execution.success
                and not numerical_verification.execution.timeout
            )
            explicit_fail = (
                exec_ok
                and not numerical_verification.all_passed
                and bool(numerical_verification.discrepancies)
            )
            checks["numerical_valid"] = not explicit_fail
            if explicit_fail:
                suggestions.append(
                    "数值验证发现矛盾：" + "; ".join(numerical_verification.discrepancies[:3])
                )

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
            numerical_verification=numerical_verification,
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

    async def _check_solution_relevance(self, question: Question, solution: Solution) -> bool:
        """G5：判断解答是否在尝试解这道题（方向/主题一致即可，不要求正确或完整）"""
        prompt = self._relevance_template.format(
            question_latex=question.content,
            solution_latex=solution.solution_latex,
        )
        try:
            result = await call_llm_json_with_retry(self.review_llm, prompt, max_attempts=2)
            return bool(result.get("relevant", True))
        except Exception as exc:
            logger.warning("solution_relevance_failed", error=str(exc)[:200])
            return True  # 基础设施故障降级为通过，不因验证器自身故障误杀候选

    async def _check_numerical(
        self, question: Question, solution: Solution
    ) -> NumericalVerification:
        """G4：LLM 生成 sympy/numpy 验证代码 → 沙箱执行 → 解析 PASS/FAIL/ALL_PASSED。"""
        from sciforge.core.sandbox import run_sandboxed

        prompt = self._numerical_template.format(
            question_latex=question.content,
            solution_sketch=solution.solution_latex,
        )
        try:
            data = await call_llm_json_with_retry(self.review_llm, prompt, max_attempts=2)
        except Exception as exc:
            logger.warning("numerical_codegen_failed", error=str(exc)[:200])
            return NumericalVerification(all_passed=False)

        code = str(data.get("verification_code") or "").strip()
        if not code:
            return NumericalVerification(all_passed=False)

        # run_sandboxed 是同步 subprocess 调用，用 to_thread 避免阻塞事件循环
        exec_result = await asyncio.to_thread(run_sandboxed, code, self.sandbox_timeout)
        stdout = exec_result.stdout or ""
        all_passed = "ALL_PASSED" in stdout
        discrepancies = [
            line.strip() for line in stdout.splitlines() if line.strip().startswith("FAIL")
        ]
        return NumericalVerification(
            all_passed=all_passed,
            discrepancies=discrepancies,
            code_used=code,
            execution=exec_result,
        )
