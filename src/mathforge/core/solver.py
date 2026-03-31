"""Stage 3: 求解引擎"""

from __future__ import annotations

import structlog

from mathforge.core.models import ContextBundle, Question, Solution
from mathforge.llm import LLMClient, LLMConfig
from mathforge.prompts import SOLVE_PROMPT

logger = structlog.get_logger()


class SolveEngine:
    """对候选题独立求解"""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient(LLMConfig.for_task("solve"))

    async def solve(self, question: Question, context: ContextBundle) -> Solution | None:
        """异步求解一道题"""
        prompt = self._build_prompt(question, context)
        try:
            result = await self.llm.generate_json(prompt=prompt, temperature=0.0)
        except Exception as e:
            logger.warning("solve_failed", error=str(e))
            return None

        confidence = result.get("confidence", "low")
        logger.info("solve_complete", confidence=confidence)

        return Solution(
            question_id=question.id,
            solution_latex=result.get("solution_latex", ""),
            methods_used=result.get("methods_used", []),
            step_count=result.get("step_count", 0),
            confidence=confidence,
        )

    def solve_sync(self, question: Question, context: ContextBundle) -> Solution | None:
        """同步求解一道题"""
        prompt = self._build_prompt(question, context)
        try:
            result = self.llm.generate_json_sync(prompt=prompt, temperature=0.0)
        except Exception as e:
            logger.warning("solve_failed", error=str(e))
            return None

        confidence = result.get("confidence", "low")
        logger.info("solve_complete", confidence=confidence)

        return Solution(
            question_id=question.id,
            solution_latex=result.get("solution_latex", ""),
            methods_used=result.get("methods_used", []),
            step_count=result.get("step_count", 0),
            confidence=confidence,
        )

    @staticmethod
    def _build_prompt(question: Question, context: ContextBundle) -> str:
        kp_context = "\n".join(
            f"- {kp.title}: {kp.content_latex[:200]}"
            for kp in context.knowledge_points[:5]
        )
        if not kp_context:
            kp_context = "（无可参考知识点）"

        return SOLVE_PROMPT.format(
            question_latex=question.content_latex,
            knowledge_context=kp_context,
        )
