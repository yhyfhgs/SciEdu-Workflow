"""DifficultyAnnotator — QGen 第 5 阶段：10 维难度评分

TagAgent 也直接复用这个标注器。
"""

from __future__ import annotations

import structlog

from sciforge.core.models import DifficultyScore, Discipline, Question, Solution
from sciforge.llm.client import LLMClient
from sciforge.pipeline.retry import call_llm_json_with_retry
from sciforge.prompts.loader import load_prompt

logger = structlog.get_logger()

_DIFFICULTY_SCORING_RAW = load_prompt("verification/difficulty_scoring.txt")


class DifficultyAnnotator:
    """10 维难度评分 + 加权聚合"""

    def __init__(self, llm: LLMClient, discipline: Discipline = Discipline.MATH) -> None:
        self.llm = llm
        self.discipline = discipline

    async def annotate(self, question: Question, solution: Solution) -> DifficultyScore:
        sketch = solution.solution_latex[:300] if solution.solution_latex else "无解答"

        prompt = _DIFFICULTY_SCORING_RAW.format(
            question_latex=question.content,
            solution_sketch=sketch,
        )

        try:
            result = await call_llm_json_with_retry(self.llm, prompt, max_attempts=2)
            scores = result.get("scores") or {}
        except Exception as exc:
            logger.warning("annotate_failed", error=str(exc)[:200])
            ds = DifficultyScore(reasoning="标注失败，使用默认值")
            ds.overall = float(question.difficulty)
            return ds

        def _clamp(v: object, default: int = 5) -> int:
            if not isinstance(v, (int, float, str)):
                return default
            try:
                return max(1, min(10, int(v)))
            except (ValueError, TypeError):
                return default

        ds = DifficultyScore(
            concept_count=_clamp(scores.get("concept_count")),
            step_count=_clamp(scores.get("step_count")),
            abstraction_level=_clamp(scores.get("abstraction_level")),
            technique_novelty=_clamp(scores.get("technique_novelty")),
            condition_parsing=_clamp(scores.get("condition_parsing")),
            computation_load=_clamp(scores.get("computation_load")),
            proof_structure=_clamp(scores.get("proof_structure")),
            prerequisite_depth=_clamp(scores.get("prerequisite_depth")),
            multi_representation=_clamp(scores.get("multi_representation")),
            metacognition=_clamp(scores.get("metacognition")),
            reasoning=str(result.get("overall_reasoning") or ""),
        )
        ds.compute_overall()
        logger.info(
            "annotate_done",
            question_id=question.id,
            overall=ds.overall,
        )
        return ds
