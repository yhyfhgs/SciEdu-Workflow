"""Stage 5: 难度标注器"""

from __future__ import annotations

import structlog

from mathforge.core.models import DifficultyScore, Question, Solution
from mathforge.llm import LLMClient, LLMConfig
from mathforge.prompts import DIFFICULTY_SCORING_PROMPT

logger = structlog.get_logger()


class DifficultyAnnotator:
    """多维难度评分"""

    def __init__(self, llm: LLMClient | None = None):
        self.llm = llm or LLMClient(LLMConfig.for_task("tag"))

    async def annotate(self, question: Question, solution: Solution) -> DifficultyScore:
        """异步计算多维难度分"""
        prompt = self._build_prompt(question, solution)
        try:
            result = await self.llm.generate_json(prompt=prompt, temperature=0.0)
            return self._parse_scores(result, question)
        except Exception as e:
            logger.warning("annotate_failed", error=str(e))
            return self._default_score(question)

    def annotate_sync(self, question: Question, solution: Solution) -> DifficultyScore:
        """同步计算多维难度分"""
        prompt = self._build_prompt(question, solution)
        try:
            result = self.llm.generate_json_sync(prompt=prompt, temperature=0.0)
            return self._parse_scores(result, question)
        except Exception as e:
            logger.warning("annotate_failed", error=str(e))
            return self._default_score(question)

    @staticmethod
    def _build_prompt(question: Question, solution: Solution) -> str:
        sketch = solution.solution_latex if solution.solution_latex else "无解答"
        return DIFFICULTY_SCORING_PROMPT.format(
            question_latex=question.content_latex,
            solution_sketch=sketch,
        )

    @staticmethod
    def _parse_scores(result: dict, question: Question) -> DifficultyScore:
        """从 LLM 响应解析难度评分"""
        scores = result.get("scores", {})
        ds = DifficultyScore(
            concept_count=scores.get("concept_count", 5),
            step_count=scores.get("step_count", 5),
            abstraction_level=scores.get("abstraction_level", 5),
            technique_novelty=scores.get("technique_novelty", 5),
            condition_parsing=scores.get("condition_parsing", 5),
            computation_load=scores.get("computation_load", 5),
            proof_structure=scores.get("proof_structure", 5),
            prerequisite_depth=scores.get("prerequisite_depth", 5),
            multi_representation=scores.get("multi_representation", 5),
            metacognition=scores.get("metacognition", 5),
            reasoning=result.get("overall_reasoning", ""),
        )
        ds.compute_overall()
        logger.info("difficulty_annotated", overall=ds.overall)
        return ds

    @staticmethod
    def _default_score(question: Question) -> DifficultyScore:
        ds = DifficultyScore(reasoning="标注失败，使用默认值")
        ds.overall = float(question.difficulty)
        return ds
