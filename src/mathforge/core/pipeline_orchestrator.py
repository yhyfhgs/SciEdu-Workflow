"""流水线并行编排器：asyncio.Queue 实现阶段级流水线"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    ContextBundle,
    GeneratedQuestion,
    GenerationRequest,
    PipelineStageResult,
)

if TYPE_CHECKING:
    from mathforge.core.annotator import DifficultyAnnotator
    from mathforge.core.generator import QuestionGenerator
    from mathforge.core.solver import SolveEngine
    from mathforge.core.verifier import VerificationPipeline

logger = structlog.get_logger()

# Sentinel 值，用于级联关闭
_SENTINEL = None


class PipelineOrchestrator:
    """asyncio.Queue 流水线并行编排器

    架构:
        Generator ──[Q1]──> Solver ──[Q2]──> Verifier ──[Q3]──> Annotator ──[Q4]──> Results

    特性:
    - 背压控制: bounded queue 防止内存溢出
    - 级联关闭: sentinel 逐级传递关闭信号
    - 提前终止: asyncio.Event 标记成功，generator 停止生成新候选
    - 错误隔离: 单个候选失败不阻塞流水线
    """

    def __init__(
        self,
        generator: QuestionGenerator,
        solver: SolveEngine,
        verifier: VerificationPipeline,
        annotator: DifficultyAnnotator,
        queue_size: int = 6,
    ):
        self.generator = generator
        self.solver = solver
        self.verifier = verifier
        self.annotator = annotator
        self.queue_size = queue_size

    async def run(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        total_candidates: int = 9,
        base_temperature: float = 0.7,
    ) -> list[PipelineStageResult]:
        """运行流水线，返回所有阶段结果（含通过和失败的）

        Args:
            request: 生成请求
            context: 检索到的上下文
            total_candidates: 总候选数
            base_temperature: 基础温度
        """
        # 创建 bounded queues
        gen_to_solve: asyncio.Queue[PipelineStageResult | None] = asyncio.Queue(
            maxsize=self.queue_size,
        )
        solve_to_verify: asyncio.Queue[PipelineStageResult | None] = asyncio.Queue(
            maxsize=self.queue_size,
        )
        verify_to_annotate: asyncio.Queue[PipelineStageResult | None] = asyncio.Queue(
            maxsize=self.queue_size,
        )
        results_queue: asyncio.Queue[PipelineStageResult | None] = asyncio.Queue()

        # 提前终止信号
        success_event = asyncio.Event()

        # 启动 worker tasks
        tasks = [
            asyncio.create_task(self._generator_worker(
                request, context, total_candidates, base_temperature,
                gen_to_solve, success_event,
            )),
            asyncio.create_task(self._solver_worker(
                context, gen_to_solve, solve_to_verify, success_event,
            )),
            asyncio.create_task(self._verifier_worker(
                context, solve_to_verify, verify_to_annotate, success_event,
            )),
            asyncio.create_task(self._annotator_worker(
                verify_to_annotate, results_queue, success_event,
            )),
        ]

        # 收集结果
        all_results: list[PipelineStageResult] = []
        while True:
            item = await results_queue.get()
            if item is _SENTINEL:
                break
            all_results.append(item)

        # 等待所有 worker 完成
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            "pipeline_complete",
            total=len(all_results),
            passed=sum(1 for r in all_results if r.verification and r.verification.passed),
        )
        return all_results

    async def run_first_success(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        total_candidates: int = 9,
        base_temperature: float = 0.7,
    ) -> GeneratedQuestion | None:
        """运行流水线，返回第一个通过验证的结果"""
        results = await self.run(request, context, total_candidates, base_temperature)

        for r in results:
            if (
                r.verification
                and r.verification.passed
                and r.candidate
                and r.solution
                and r.difficulty_score
            ):
                return GeneratedQuestion(
                    question=r.candidate,
                    solution=r.solution,
                    difficulty_score=r.difficulty_score,
                    verification=r.verification,
                )

        return None

    # ── Worker Tasks ─────────────────────────────────────

    async def _generator_worker(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        total: int,
        base_temp: float,
        out_queue: asyncio.Queue[PipelineStageResult | None],
        success_event: asyncio.Event,
    ) -> None:
        """生成器 worker: 逐个生成候选题并推入队列"""
        for i in range(total):
            if success_event.is_set():
                logger.info("generator_early_stop", generated=i)
                break

            temp = min(base_temp + i * 0.05, 1.0)
            try:
                candidates = await self.generator.generate_candidates(
                    request=request, context=context, n=1, extra_temperature=temp - base_temp,
                )
                if candidates:
                    item = PipelineStageResult(candidate=candidates[0], stage="generated")
                    await out_queue.put(item)
                    logger.debug("pipeline_generated", index=i + 1)
            except Exception as e:
                logger.warning("pipeline_gen_error", index=i + 1, error=str(e))

        await out_queue.put(_SENTINEL)

    async def _solver_worker(
        self,
        context: ContextBundle,
        in_queue: asyncio.Queue[PipelineStageResult | None],
        out_queue: asyncio.Queue[PipelineStageResult | None],
        success_event: asyncio.Event,
    ) -> None:
        """求解 worker: 从队列取候选，求解后推入下游"""
        while True:
            item = await in_queue.get()
            if item is _SENTINEL:
                await out_queue.put(_SENTINEL)
                break
            if success_event.is_set():
                continue  # drain queue

            try:
                solution = await self.solver.solve(item.candidate, context)
                if solution and solution.confidence != "low":
                    item.solution = solution
                    item.stage = "solved"
                    await out_queue.put(item)
                else:
                    item.stage = "failed"
                    item.error = "solve_low_confidence"
                    await out_queue.put(item)
            except Exception as e:
                item.stage = "failed"
                item.error = f"solve_error: {e}"
                await out_queue.put(item)

    async def _verifier_worker(
        self,
        context: ContextBundle,
        in_queue: asyncio.Queue[PipelineStageResult | None],
        out_queue: asyncio.Queue[PipelineStageResult | None],
        success_event: asyncio.Event,
    ) -> None:
        """验证 worker: 验证候选 + 解答"""
        while True:
            item = await in_queue.get()
            if item is _SENTINEL:
                await out_queue.put(_SENTINEL)
                break

            # 失败的直接传递
            if item.stage == "failed":
                await out_queue.put(item)
                continue

            try:
                verification = await self.verifier.verify(
                    item.candidate, item.solution, context,
                )
                item.verification = verification
                item.stage = "verified"

                if verification.passed:
                    success_event.set()  # 通知提前终止

                await out_queue.put(item)
            except Exception as e:
                item.stage = "failed"
                item.error = f"verify_error: {e}"
                await out_queue.put(item)

    async def _annotator_worker(
        self,
        in_queue: asyncio.Queue[PipelineStageResult | None],
        out_queue: asyncio.Queue[PipelineStageResult | None],
        success_event: asyncio.Event,
    ) -> None:
        """标注 worker: 对通过验证的候选进行难度评分"""
        while True:
            item = await in_queue.get()
            if item is _SENTINEL:
                await out_queue.put(_SENTINEL)
                break

            if item.verification and item.verification.passed and item.solution:
                try:
                    score = await self.annotator.annotate(item.candidate, item.solution)
                    item.difficulty_score = score
                    item.candidate.difficulty_ai = score.overall
                    item.stage = "annotated"
                except Exception as e:
                    logger.warning("pipeline_annotate_error", error=str(e))
                    item.stage = "annotated"  # 标注失败不阻碍

            await out_queue.put(item)
