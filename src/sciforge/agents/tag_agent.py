"""TagAgent — 多维难度标注 Agent (Agent 4)

复用 qgen/annotator.py 的 DifficultyAnnotator，对外以 BaseAgent 接口暴露。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from sciforge.agents.base import AgentError, BaseAgent
from sciforge.agents.qgen.annotator import DifficultyAnnotator
from sciforge.core.models import DifficultyScore, Discipline, Question, Solution

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient


class TagInput(BaseModel):
    question: Question
    solution: Solution | None = None


class TagOutput(BaseModel):
    difficulty_score: DifficultyScore


class TagAgent(BaseAgent[TagInput, TagOutput]):
    """为题目生成 10 维难度评分"""

    name = "tag"
    prompt_file = ""

    def __init__(
        self,
        llm: LLMClient,
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        super().__init__(llm=llm, discipline=discipline)
        self.annotator = DifficultyAnnotator(llm, discipline)

    async def run(self, input: TagInput) -> TagOutput:
        if input.solution is None:
            # 没有解答时用空 Solution，annotator 会用默认值
            solution = Solution(question_id=input.question.id)
        else:
            solution = input.solution

        if input.solution is None and not input.question.solution_content:
            raise AgentError("TagAgent 建议提供 solution，没有解答时难度评分会退化为启发式")

        self._log_start(question_id=input.question.id)
        score = await self.annotator.annotate(input.question, solution)
        self._log_end(question_id=input.question.id, overall=score.overall)
        return TagOutput(difficulty_score=score)
