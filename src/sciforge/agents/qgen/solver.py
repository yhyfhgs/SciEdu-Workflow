"""SolveEngine — QGen 第 3 阶段：独立求解候选题

SolveAgent 也直接复用这个引擎。
"""

from __future__ import annotations

import structlog

from sciforge.core.models import ContextBundle, Discipline, Question, Solution
from sciforge.llm.client import LLMClient
from sciforge.pipeline.retry import call_llm_json_with_retry
from sciforge.prompts.templates import get_generation_prompt_template

logger = structlog.get_logger()


class SolveEngine:
    """对给定题目独立求解，返回 Solution（含 confidence）"""

    def __init__(self, llm: LLMClient, discipline: Discipline = Discipline.MATH) -> None:
        self.llm = llm
        self.discipline = discipline
        self._solve_template = get_generation_prompt_template(
            "solving/solve.txt", discipline
        )

    async def solve(
        self,
        question: Question,
        context: ContextBundle | None = None,
    ) -> Solution | None:
        """求解；解不出返回 None"""
        kp_context = ""
        if context is not None and context.knowledge_points:
            kp_context = "\n".join(
                f"- {kp.number} [{kp.kp_type}]: {kp.content[:200]}"
                for kp in context.knowledge_points[:5]
            )

        prompt = self._solve_template.format(
            question_latex=question.content,
            knowledge_context=kp_context,
        )

        try:
            result = await call_llm_json_with_retry(
                self.llm, prompt, max_attempts=2
            )
        except Exception as exc:
            logger.warning("solve_failed", error=str(exc)[:200])
            return None

        confidence_raw = str(result.get("confidence", "low")).lower()
        if confidence_raw not in ("high", "medium", "low"):
            confidence_raw = "low"

        solution_text = str(
            result.get("solution_latex") or result.get("solution") or ""
        )

        logger.info(
            "solve_done",
            question_id=question.id,
            confidence=confidence_raw,
            length=len(solution_text),
        )

        return Solution(
            question_id=question.id,
            solution_latex=solution_text,
            methods_used=list(result.get("methods_used") or []),
            confidence=confidence_raw,  # type: ignore[arg-type]
        )
