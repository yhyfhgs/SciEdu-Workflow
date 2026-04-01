"""生成引擎主循环: 生成 → 求解 → 验证 → 标注 闭环"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from mathforge.core.annotator import DifficultyAnnotator
from mathforge.core.generator import QuestionGenerator
from mathforge.core.models import GeneratedQuestion, GenerationRequest
from mathforge.core.retriever import ContextRetriever
from mathforge.core.solver import SolveEngine
from mathforge.core.verifier import VerificationPipeline

if TYPE_CHECKING:
    from mathforge.storage.file_backend import FileStorageBackend

logger = structlog.get_logger()


class GenerationEngine:
    """完整的 生成 → 求解 → 验证 → 标注 闭环

    最多 max_rounds 轮，每轮 candidates_per_round 个候选。
    """

    def __init__(
        self,
        retriever: ContextRetriever,
        generator: QuestionGenerator | None = None,
        solver: SolveEngine | None = None,
        verifier: VerificationPipeline | None = None,
        annotator: DifficultyAnnotator | None = None,
        storage: FileStorageBackend | None = None,
        max_rounds: int = 3,
        candidates_per_round: int = 3,
    ):
        self.retriever = retriever
        self.generator = generator or QuestionGenerator()
        self.solver = solver or SolveEngine()
        self.verifier = verifier or VerificationPipeline()
        self.annotator = annotator or DifficultyAnnotator()
        self.storage = storage
        self.max_rounds = max_rounds
        self.candidates_per_round = candidates_per_round

    async def generate(self, request: GenerationRequest) -> GeneratedQuestion | None:
        """异步主入口: 尝试生成一道通过验证的高质量题目"""
        logger.info(
            "generation_start",
            subject=request.subject.value,
            topics=request.topics,
            difficulty=request.difficulty_target,
            strategy=request.strategy.value,
            question_type=request.question_type.value,
        )

        # Stage 1: 检索上下文
        context = self.retriever.build_context(request)

        for round_num in range(self.max_rounds):
            logger.info("round_start", round=round_num + 1, max_rounds=self.max_rounds)

            # Stage 2: 生成候选
            candidates = await self.generator.generate_candidates(
                request=request,
                context=context,
                n=self.candidates_per_round,
                extra_temperature=round_num * 0.1,
            )
            logger.info("candidates_generated", count=len(candidates))

            for idx, candidate in enumerate(candidates):
                logger.info(
                    "candidate_eval",
                    index=idx + 1,
                    preview=candidate.content_latex[:80],
                )

                # Stage 3: 求解
                solution = await self.solver.solve(candidate, context)
                if not solution or solution.confidence == "low":
                    logger.info("candidate_skip", reason="solve_low_confidence")
                    continue

                # Stage 4: 验证
                verification = await self.verifier.verify(candidate, solution, context)

                if verification.passed:
                    # Stage 5: 难度标注
                    difficulty_score = await self.annotator.annotate(candidate, solution)
                    candidate.difficulty_ai = difficulty_score.overall

                    logger.info(
                        "generation_success",
                        round=round_num + 1,
                        difficulty_ai=difficulty_score.overall,
                    )

                    return GeneratedQuestion(
                        question=candidate,
                        solution=solution,
                        difficulty_score=difficulty_score,
                        verification=verification,
                        generation_meta={
                            "round": round_num + 1,
                            "strategy": request.strategy.value,
                            "context_kps": len(context.knowledge_points),
                            "context_refs": len(context.reference_questions),
                        },
                    )
                else:
                    failed = [k for k, v in verification.checks.items() if not v]
                    logger.info("candidate_rejected", failed_checks=failed)
                    if verification.has_suggestions:
                        context.feedback_from_previous_round = verification.suggestions

        logger.warning("generation_exhausted", max_rounds=self.max_rounds)
        return None

    def generate_sync(self, request: GenerationRequest) -> GeneratedQuestion | None:
        """同步主入口"""
        logger.info(
            "generation_start",
            subject=request.subject.value,
            topics=request.topics,
            difficulty=request.difficulty_target,
            strategy=request.strategy.value,
        )

        context = self.retriever.build_context(request)

        for round_num in range(self.max_rounds):
            logger.info("round_start", round=round_num + 1, max_rounds=self.max_rounds)

            candidates = self.generator.generate_candidates_sync(
                request=request,
                context=context,
                n=self.candidates_per_round,
                extra_temperature=round_num * 0.1,
            )
            logger.info("candidates_generated", count=len(candidates))

            for idx, candidate in enumerate(candidates):
                logger.info(
                    "candidate_eval",
                    index=idx + 1,
                    preview=candidate.content_latex[:80],
                )

                solution = self.solver.solve_sync(candidate, context)
                if not solution or solution.confidence == "low":
                    logger.info("candidate_skip", reason="solve_low_confidence")
                    continue

                verification = self.verifier.verify_sync(candidate, solution, context)

                if verification.passed:
                    difficulty_score = self.annotator.annotate_sync(candidate, solution)
                    candidate.difficulty_ai = difficulty_score.overall

                    logger.info(
                        "generation_success",
                        round=round_num + 1,
                        difficulty_ai=difficulty_score.overall,
                    )

                    return GeneratedQuestion(
                        question=candidate,
                        solution=solution,
                        difficulty_score=difficulty_score,
                        verification=verification,
                        generation_meta={
                            "round": round_num + 1,
                            "strategy": request.strategy.value,
                            "context_kps": len(context.knowledge_points),
                            "context_refs": len(context.reference_questions),
                        },
                    )
                else:
                    failed = [k for k, v in verification.checks.items() if not v]
                    logger.info("candidate_rejected", failed_checks=failed)
                    if verification.has_suggestions:
                        context.feedback_from_previous_round = verification.suggestions

        logger.warning("generation_exhausted", max_rounds=self.max_rounds)
        return None

    def generate_batch(
        self,
        request: GenerationRequest,
        count: int,
    ) -> list[GeneratedQuestion]:
        """同步批量生成"""
        results = []
        for i in range(count):
            logger.info("batch_item", index=i + 1, total=count)
            result = self.generate_sync(request)
            if result:
                results.append(result)
                request.exclusion_ids.add(result.question.id)
        logger.info("batch_done", success=len(results), total=count)
        return results

    async def generate_batch_async(
        self,
        request: GenerationRequest,
        count: int,
        max_concurrent: int = 3,
    ) -> list[GeneratedQuestion]:
        """异步批量生成（顺序排除，控制并发）"""
        semaphore = asyncio.Semaphore(max_concurrent)
        results = []
        exclusion_ids = set(request.exclusion_ids)

        for i in range(count):
            logger.info("batch_item", index=i + 1, total=count)
            req_copy = request.model_copy(update={"exclusion_ids": set(exclusion_ids)})
            async with semaphore:
                result = await self.generate(req_copy)
            if result:
                results.append(result)
                exclusion_ids.add(result.question.id)

        logger.info("batch_done", success=len(results), total=count)
        return results

    # ── 流水线并行模式 ───────────────────────────────────

    async def generate_pipelined(
        self, request: GenerationRequest,
    ) -> GeneratedQuestion | None:
        """流水线并行模式: 生成/求解/验证/标注 阶段级并行

        相比 generate()，各阶段不等待全部候选完成再推进，
        而是一生成就求解、一求解就验证，提高吞吐量。
        """
        from mathforge.core.pipeline_orchestrator import PipelineOrchestrator

        logger.info(
            "pipeline_generation_start",
            subject=request.subject.value,
            topics=request.topics,
            difficulty=request.difficulty_target,
            strategy=request.strategy.value,
        )

        context = self.retriever.build_context(request)
        total = self.max_rounds * self.candidates_per_round

        orchestrator = PipelineOrchestrator(
            generator=self.generator,
            solver=self.solver,
            verifier=self.verifier,
            annotator=self.annotator,
            queue_size=self.candidates_per_round * 2,
        )

        result = await orchestrator.run_first_success(
            request=request,
            context=context,
            total_candidates=total,
            base_temperature=self.generator.llm.config.temperature,
        )

        if result:
            result.generation_meta = {
                "mode": "pipelined",
                "strategy": request.strategy.value,
                "context_kps": len(context.knowledge_points),
                "context_refs": len(context.reference_questions),
            }
            score = result.difficulty_score.overall
            logger.info("pipeline_generation_success", difficulty_ai=score)
        else:
            logger.warning("pipeline_generation_exhausted", total_candidates=total)

        return result
