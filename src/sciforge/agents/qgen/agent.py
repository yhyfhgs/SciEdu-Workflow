"""QGenAgent — 5 阶段生成闭环的 Agent 封装

组合 retriever → generator → solver → verifier → annotator。
相当于原 GenerationEngine 但作为 BaseAgent 暴露 async run 接口。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from sciforge.agents.base import BaseAgent
from sciforge.agents.qgen.annotator import DifficultyAnnotator
from sciforge.agents.qgen.generator import QuestionGenerator
from sciforge.agents.qgen.retriever import ContextRetriever
from sciforge.agents.qgen.solver import SolveEngine
from sciforge.agents.qgen.verifier import VerificationPipeline
from sciforge.core.models import (
    Discipline,
    GeneratedQuestion,
    GenerationRequest,
)

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend


class QGenInput(BaseModel):
    request: GenerationRequest


class QGenOutput(BaseModel):
    """QGenAgent.run 返回的结构化结果"""

    generated: GeneratedQuestion | None = None
    rounds_used: int = 0
    candidates_tried: int = 0
    reason: str = ""  # 未成功时说明原因


class QGenAgent(BaseAgent[QGenInput, QGenOutput]):
    """题目生成的多阶段 Agent，内部组合 5 个子组件"""

    name = "qgen"
    prompt_file = ""  # 每个子组件自己加载 prompt

    # 默认值；实际使用实例属性 self.max_rounds / self.candidates_per_round
    DEFAULT_MAX_ROUNDS: int = 3
    DEFAULT_CANDIDATES_PER_ROUND: int = 3

    def __init__(
        self,
        llm: LLMClient,
        storage: SQLiteStorageBackend,
        discipline: Discipline = Discipline.MATH,
        *,
        review_llm: LLMClient | None = None,
        solve_llm: LLMClient | None = None,
        tag_llm: LLMClient | None = None,
        max_rounds: int | None = None,
        candidates_per_round: int | None = None,
        retriever_max_kps: int | None = None,
        retriever_max_refs: int | None = None,
        retriever_max_negatives: int | None = None,
        merge_difficulty_into_verify: bool = False,
        lightweight_prefilter: bool = True,
    ) -> None:
        super().__init__(llm=llm, discipline=discipline)
        self.storage = storage
        self.max_rounds = int(max_rounds) if max_rounds is not None else self.DEFAULT_MAX_ROUNDS
        self.candidates_per_round = (
            int(candidates_per_round)
            if candidates_per_round is not None
            else self.DEFAULT_CANDIDATES_PER_ROUND
        )
        self.merge_difficulty_into_verify = merge_difficulty_into_verify
        self.lightweight_prefilter = lightweight_prefilter
        self.retriever = ContextRetriever(
            storage,
            max_kps=retriever_max_kps,
            max_refs=retriever_max_refs,
            max_negatives=retriever_max_negatives,
        )
        self.generator = QuestionGenerator(llm, discipline)
        self.solver = SolveEngine(solve_llm or llm, discipline)
        self.verifier = VerificationPipeline(review_llm or llm, discipline)
        self.annotator = DifficultyAnnotator(tag_llm or llm, discipline)

    async def run(self, input: QGenInput) -> QGenOutput:
        from sciforge.agents.qgen.prefilter import prefilter_candidate

        request = input.request
        self._log_start(
            subject=request.subject,
            topics=request.topics,
            target=request.difficulty_target,
            max_rounds=self.max_rounds,
        )

        # Stage 1: 检索上下文
        context = self.retriever.build_context(request, self.discipline)

        candidates_tried = 0
        prefilter_rejected = 0
        for round_num in range(self.max_rounds):
            # Stage 2: 生成候选
            candidates = await self.generator.generate_candidates(
                request, context, n=self.candidates_per_round
            )
            candidates_tried += len(candidates)

            for candidate in candidates:
                # Stage 2.5: 轻量预筛（zero-cost）
                if self.lightweight_prefilter:
                    passed, reason = prefilter_candidate(
                        candidate, context.negative_examples
                    )
                    if not passed:
                        prefilter_rejected += 1
                        continue

                # Stage 3: 求解
                solution = await self.solver.solve(candidate, context)
                if solution is None or solution.confidence == "low":
                    continue

                # Stage 4: 验证
                verification = await self.verifier.verify(candidate, solution, context)
                if not verification.passed:
                    if verification.has_suggestions:
                        context.feedback_from_previous_round = verification.suggestions
                    continue

                # Stage 5: 标注难度
                # merge_difficulty_into_verify=True 时跳过独立 annotate 调用
                # （需要 verifier 在 verdict=PASS 时已返回 difficulty_scores）
                difficulty_score = (
                    verification.difficulty_score
                    if self.merge_difficulty_into_verify
                    else None
                )
                if difficulty_score is not None:
                    skipped_annotate_call = True
                else:
                    difficulty_score = await self.annotator.annotate(candidate, solution)
                    skipped_annotate_call = False
                candidate.difficulty = int(round(difficulty_score.overall))

                self._log_end(
                    success=True,
                    round=round_num + 1,
                    candidates_tried=candidates_tried,
                    prefilter_rejected=prefilter_rejected,
                    skipped_annotate_call=skipped_annotate_call,
                    overall_difficulty=difficulty_score.overall,
                )
                return QGenOutput(
                    generated=GeneratedQuestion(
                        question=candidate,
                        solution=solution,
                        difficulty_score=difficulty_score,
                        verification=verification,
                        generation_meta={
                            "round": round_num + 1,
                            "strategy": request.strategy.value,
                            "discipline": self.discipline.value,
                            "prefilter_rejected": prefilter_rejected,
                            "skipped_annotate_call": skipped_annotate_call,
                        },
                    ),
                    rounds_used=round_num + 1,
                    candidates_tried=candidates_tried,
                )

        self._log_end(
            success=False,
            rounds=self.max_rounds,
            candidates_tried=candidates_tried,
            prefilter_rejected=prefilter_rejected,
        )
        return QGenOutput(
            generated=None,
            rounds_used=self.max_rounds,
            candidates_tried=candidates_tried,
            reason=(
                f"{self.max_rounds} 轮 × {self.candidates_per_round} 候选后仍未通过验证"
                f"（预筛拒绝 {prefilter_rejected} 次）"
            ),
        )
