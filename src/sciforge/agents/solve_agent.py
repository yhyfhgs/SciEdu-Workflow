"""SolveAgent — 独立求解 Agent (Agent 3)

复用 qgen/solver.py 的 SolveEngine，对外以 BaseAgent 接口暴露：
- 输入：question_id 或 Question + 可选 ContextBundle
- 输出：Solution
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from sciforge.agents.base import AgentError, BaseAgent
from sciforge.agents.qgen.solver import SolveEngine
from sciforge.core.models import (
    ContextBundle,
    Discipline,
    Question,
    Solution,
)

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend


class SolveInput(BaseModel):
    question: Question | None = None
    question_id: str | None = None
    context: ContextBundle | None = None


class SolveOutput(BaseModel):
    solution: Solution | None = None
    confidence: str = "low"


class SolveAgent(BaseAgent[SolveInput, SolveOutput]):
    """对指定题目独立生成解答"""

    name = "solve"
    prompt_file = ""  # SolveEngine 自己加载

    def __init__(
        self,
        llm: LLMClient,
        storage: SQLiteStorageBackend | None = None,
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        super().__init__(llm=llm, discipline=discipline)
        self.storage = storage
        self.engine = SolveEngine(llm, discipline)

    async def run(self, input: SolveInput) -> SolveOutput:
        question = input.question
        if question is None:
            if not input.question_id:
                raise AgentError("SolveAgent 需要提供 question 或 question_id")
            if self.storage is None:
                raise AgentError("通过 question_id 求解时必须提供 storage")
            question = self.storage.load_question(input.question_id)

        self._log_start(question_id=question.id)
        solution = await self.engine.solve(question, input.context)

        confidence = solution.confidence if solution else "low"
        self._log_end(question_id=question.id, confidence=confidence)
        return SolveOutput(solution=solution, confidence=confidence)
