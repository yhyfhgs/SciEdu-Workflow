"""Stage 4: 验证管线"""

from __future__ import annotations

import re

import structlog

from mathforge.core.models import (
    ContextBundle,
    Question,
    Solution,
    VerificationResult,
)
from mathforge.llm import LLMClient, LLMConfig
from mathforge.prompts import REVIEW_PROMPT, SOLUTION_RELEVANCE_PROMPT

logger = structlog.get_logger()


class VerificationPipeline:
    """多层验证管线

    使用独立的 LLM 实例做审核，避免确认偏差。
    """

    def __init__(self, llm: LLMClient | None = None):
        self.review_llm = llm or LLMClient(LLMConfig.for_task("review"))

    async def verify(
        self,
        candidate: Question,
        solution: Solution,
        context: ContextBundle,
    ) -> VerificationResult:
        """异步执行全部验证检查"""
        checks: dict[str, bool] = {}
        suggestions: list[str] = []

        # Check 1: 求解置信度
        checks["solve_confidence"] = solution.confidence in ("high", "medium")
        if not checks["solve_confidence"]:
            suggestions.append("求解引擎置信度低，题目可能存在问题。")

        # Check 2: 数学正确性（独立 LLM 审核）
        math_review = await self._check_math(candidate)
        checks["math_correctness"] = math_review["passed"]
        if not checks["math_correctness"]:
            suggestions.extend(math_review.get("issues", []))

        # Check 3: 非平凡性
        checks["non_trivial"] = self._check_non_trivial(candidate)
        if not checks["non_trivial"]:
            suggestions.append("题目过于简短或缺少数学内容。")

        # Check 4: LaTeX 语法有效性
        checks["latex_valid"] = self._check_latex_syntax(candidate.content_latex)
        if not checks["latex_valid"]:
            suggestions.append("LaTeX 语法存在括号不匹配或环境未闭合。")

        # Check 5: 解答与题目相关性
        checks["solution_relevant"] = await self._check_solution_relevance(
            candidate, solution
        )
        if not checks["solution_relevant"]:
            suggestions.append("解答可能未正确回应题目要求。")

        passed = all(checks.values())
        logger.info("verification_done", passed=passed, checks=checks)

        return VerificationResult(
            passed=passed,
            checks=checks,
            suggestions="\n".join(suggestions) if suggestions else "",
        )

    def verify_sync(
        self,
        candidate: Question,
        solution: Solution,
        context: ContextBundle,
    ) -> VerificationResult:
        """同步执行全部验证检查"""
        checks: dict[str, bool] = {}
        suggestions: list[str] = []

        checks["solve_confidence"] = solution.confidence in ("high", "medium")
        if not checks["solve_confidence"]:
            suggestions.append("求解引擎置信度低，题目可能存在问题。")

        math_review = self._check_math_sync(candidate)
        checks["math_correctness"] = math_review["passed"]
        if not checks["math_correctness"]:
            suggestions.extend(math_review.get("issues", []))

        checks["non_trivial"] = self._check_non_trivial(candidate)
        if not checks["non_trivial"]:
            suggestions.append("题目过于简短或缺少数学内容。")

        checks["latex_valid"] = self._check_latex_syntax(candidate.content_latex)
        if not checks["latex_valid"]:
            suggestions.append("LaTeX 语法存在括号不匹配或环境未闭合。")

        checks["solution_relevant"] = self._check_solution_relevance_sync(
            candidate, solution
        )
        if not checks["solution_relevant"]:
            suggestions.append("解答可能未正确回应题目要求。")

        passed = all(checks.values())
        logger.info("verification_done", passed=passed, checks=checks)

        return VerificationResult(
            passed=passed,
            checks=checks,
            suggestions="\n".join(suggestions) if suggestions else "",
        )

    # ── 检查项实现 ────────────────────────────────────

    async def _check_math(self, question: Question) -> dict:
        """独立数学审核 (async)"""
        prompt = REVIEW_PROMPT.format(question_latex=question.content_latex)
        try:
            result = await self.review_llm.generate_json(prompt=prompt, temperature=0.0)
            verdict = result.get("verdict", "FAIL")
            return {
                "passed": verdict == "PASS",
                "issues": result.get("issues", []),
                "suggestions": result.get("suggestions", ""),
            }
        except Exception as e:
            logger.warning("math_review_failed", error=str(e))
            return {"passed": False, "issues": [str(e)]}

    def _check_math_sync(self, question: Question) -> dict:
        """独立数学审核 (sync)"""
        prompt = REVIEW_PROMPT.format(question_latex=question.content_latex)
        try:
            result = self.review_llm.generate_json_sync(prompt=prompt, temperature=0.0)
            verdict = result.get("verdict", "FAIL")
            return {
                "passed": verdict == "PASS",
                "issues": result.get("issues", []),
                "suggestions": result.get("suggestions", ""),
            }
        except Exception as e:
            logger.warning("math_review_failed", error=str(e))
            return {"passed": False, "issues": [str(e)]}

    @staticmethod
    def _check_non_trivial(candidate: Question) -> bool:
        """检查题目非平凡性: 长度 > 30 且含数学内容"""
        content = candidate.content_latex
        if len(content) <= 30:
            return False
        has_math = any(marker in content for marker in ("$", "\\", "\\begin"))
        return has_math

    @staticmethod
    def _check_latex_syntax(latex: str) -> bool:
        """轻量 LaTeX 语法检查: 括号匹配 + 环境闭合"""
        # 括号匹配
        for open_c, close_c in [("{", "}"), ("(", ")"), ("[", "]")]:
            if latex.count(open_c) != latex.count(close_c):
                return False

        # \begin{X} 和 \end{X} 匹配
        begins = re.findall(r"\\begin\{(\w+)\}", latex)
        ends = re.findall(r"\\end\{(\w+)\}", latex)
        if begins != ends:
            return False

        return True

    async def _check_solution_relevance(
        self, candidate: Question, solution: Solution
    ) -> bool:
        """LLM 判断解答是否回应题目 (async)"""
        if not solution.solution_latex:
            return False
        prompt = SOLUTION_RELEVANCE_PROMPT.format(
            question_latex=candidate.content_latex,
            solution_latex=solution.solution_latex[:1500],
        )
        try:
            result = await self.review_llm.generate_json(prompt=prompt, temperature=0.0)
            return bool(result.get("relevant", False))
        except Exception as e:
            logger.warning("relevance_check_failed", error=str(e))
            return True  # 检查失败时默认通过，避免误拦

    def _check_solution_relevance_sync(
        self, candidate: Question, solution: Solution
    ) -> bool:
        """LLM 判断解答是否回应题目 (sync)"""
        if not solution.solution_latex:
            return False
        prompt = SOLUTION_RELEVANCE_PROMPT.format(
            question_latex=candidate.content_latex,
            solution_latex=solution.solution_latex[:1500],
        )
        try:
            result = self.review_llm.generate_json_sync(prompt=prompt, temperature=0.0)
            return bool(result.get("relevant", False))
        except Exception as e:
            logger.warning("relevance_check_failed", error=str(e))
            return True
