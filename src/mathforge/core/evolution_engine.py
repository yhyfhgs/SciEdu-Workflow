"""进化引擎：迭代改进循环，受 Frontier-CS-Evolve 启发"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    CandidateRecord,
    ContextBundle,
    DifficultyScore,
    EvolutionSession,
    GeneratedQuestion,
    GenerationRequest,
    Question,
    VerificationResult,
)
from mathforge.llm import LLMClient, LLMConfig
from mathforge.prompts.improvement_prompts import (
    EVOLUTION_SYSTEM_PROMPT,
    IMPROVE_FROM_SOLVER_PROMPT,
    IMPROVE_FROM_VERIFICATION_PROMPT,
    ITERATION_SUMMARY_TEMPLATE,
)

if TYPE_CHECKING:
    from mathforge.core.engine import GenerationEngine

logger = structlog.get_logger()


class EvolutionEngine:
    """迭代进化引擎

    包装 GenerationEngine，实现:
    1. 初始生成阶段: 收集所有候选（含失败的）
    2. 改进循环: 对可改进候选按策略修复/调整/探索
    3. Checkpoint/Resume: 长时间运行时保存和恢复状态
    4. 世代追踪: 每个候选记录完整谱系

    灵感来源: Frontier-CS-Evolve 的 Advisor-Solver 迭代循环
    """

    def __init__(
        self,
        generation_engine: GenerationEngine,
        improve_llm: LLMClient | None = None,
        max_improvement_rounds: int = 2,
        checkpoint_dir: Path | None = None,
    ):
        self.engine = generation_engine
        self.improve_llm = improve_llm or LLMClient(LLMConfig.for_task("improve"))
        self.max_improvement_rounds = max_improvement_rounds
        self.checkpoint_dir = checkpoint_dir

    async def evolve(
        self,
        request: GenerationRequest,
    ) -> GeneratedQuestion | None:
        """主入口: 进化式生成

        Phase A: 初始生成 — 收集所有候选
        Phase B: 改进循环 — 对失败候选迭代改进
        """
        session = EvolutionSession(
            request=request,
            max_rounds=self.engine.max_rounds,
            max_improvement_rounds=self.max_improvement_rounds,
        )

        logger.info(
            "evolution_start",
            subject=request.subject.value,
            topics=request.topics,
            max_improvement_rounds=self.max_improvement_rounds,
        )

        # Phase A: 初始生成
        context = self.engine.retriever.build_context(request)
        all_candidates = await self._run_generation_pass(request, context, session)

        # 检查是否有通过的
        passed = [c for c in all_candidates if c.passed]
        if passed:
            best = self._select_best(passed, request.difficulty_target)
            session.best_result = best
            logger.info("evolution_success", phase="initial", generation=0)
            return best

        # Phase B: 改进循环
        for improve_round in range(self.max_improvement_rounds):
            session.improvement_rounds = improve_round + 1
            logger.info(
                "improvement_round_start",
                round=improve_round + 1,
                candidates_to_improve=len(self._select_improvable(all_candidates)),
            )

            improvable = self._select_improvable(all_candidates)
            if not improvable:
                logger.warning("no_improvable_candidates")
                break

            improved = await self._improve_candidates(improvable, context, session)
            all_candidates.extend(improved)

            # 检查改进后是否有通过的
            newly_passed = [c for c in improved if c.passed]
            if newly_passed:
                best = self._select_best(newly_passed, request.difficulty_target)
                session.best_result = best
                logger.info(
                    "evolution_success",
                    phase="improvement",
                    round=improve_round + 1,
                    generation=best.generation_meta.get("generation", 0),
                )
                return best

            # 生成迭代摘要
            summary = self._build_iteration_summary(session, improve_round + 1)
            session.iteration_summaries.append(summary)

            # Checkpoint
            if self.checkpoint_dir:
                await self._checkpoint(session)

        # 所有轮次耗尽，返回最接近通过的
        logger.warning(
            "evolution_exhausted",
            total_candidates=len(all_candidates),
            improvement_rounds=self.max_improvement_rounds,
        )
        return self._select_best_effort(all_candidates)

    def evolve_sync(
        self,
        request: GenerationRequest,
    ) -> GeneratedQuestion | None:
        """同步版本"""
        session = EvolutionSession(
            request=request,
            max_rounds=self.engine.max_rounds,
            max_improvement_rounds=self.max_improvement_rounds,
        )

        context = self.engine.retriever.build_context(request)
        all_candidates = self._run_generation_pass_sync(request, context, session)

        passed = [c for c in all_candidates if c.passed]
        if passed:
            best = self._select_best(passed, request.difficulty_target)
            session.best_result = best
            return best

        for improve_round in range(self.max_improvement_rounds):
            session.improvement_rounds = improve_round + 1

            improvable = self._select_improvable(all_candidates)
            if not improvable:
                break

            improved = self._improve_candidates_sync(improvable, context, session)
            all_candidates.extend(improved)

            newly_passed = [c for c in improved if c.passed]
            if newly_passed:
                best = self._select_best(newly_passed, request.difficulty_target)
                session.best_result = best
                return best

            summary = self._build_iteration_summary(session, improve_round + 1)
            session.iteration_summaries.append(summary)

        return self._select_best_effort(all_candidates)

    # ── Phase A: 初始生成 ────────────────────────────────

    async def _run_generation_pass(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        session: EvolutionSession,
    ) -> list[CandidateRecord]:
        """运行初始生成，收集所有候选（含失败的）"""
        records: list[CandidateRecord] = []

        for round_num in range(self.engine.max_rounds):
            candidates = await self.engine.generator.generate_candidates(
                request=request,
                context=context,
                n=self.engine.candidates_per_round,
                extra_temperature=round_num * 0.1,
            )

            for candidate in candidates:
                record = await self._evaluate_candidate(candidate, context)
                records.append(record)
                session.candidates.append(record)

                if record.passed:
                    return records  # 早停

            # 注入反馈
            failed_records = [r for r in records if not r.passed and r.verification]
            if failed_records:
                last_failed = failed_records[-1]
                if last_failed.verification and last_failed.verification.has_suggestions:
                    context.feedback_from_previous_round = last_failed.verification.suggestions

        return records

    def _run_generation_pass_sync(
        self,
        request: GenerationRequest,
        context: ContextBundle,
        session: EvolutionSession,
    ) -> list[CandidateRecord]:
        """同步版初始生成"""
        records: list[CandidateRecord] = []

        for round_num in range(self.engine.max_rounds):
            candidates = self.engine.generator.generate_candidates_sync(
                request=request,
                context=context,
                n=self.engine.candidates_per_round,
                extra_temperature=round_num * 0.1,
            )

            for candidate in candidates:
                record = self._evaluate_candidate_sync(candidate, context)
                records.append(record)
                session.candidates.append(record)
                if record.passed:
                    return records

            failed_records = [r for r in records if not r.passed and r.verification]
            if failed_records:
                last_failed = failed_records[-1]
                if last_failed.verification and last_failed.verification.has_suggestions:
                    context.feedback_from_previous_round = last_failed.verification.suggestions

        return records

    # ── Phase B: 改进循环 ────────────────────────────────

    async def _improve_candidates(
        self,
        candidates: list[CandidateRecord],
        context: ContextBundle,
        session: EvolutionSession,
    ) -> list[CandidateRecord]:
        """对可改进候选执行改进策略"""
        improved: list[CandidateRecord] = []

        for candidate in candidates:
            strategy, directive = self._choose_improvement_strategy(candidate)

            logger.info(
                "improving_candidate",
                parent_id=candidate.id[:8],
                strategy=strategy,
                generation=candidate.generation,
            )

            try:
                new_question = await self._generate_improvement(
                    candidate,
                    strategy,
                    directive,
                    session,
                )
                if new_question:
                    record = await self._evaluate_candidate(new_question, context)
                    record.parent_id = candidate.id
                    record.generation = candidate.generation + 1
                    record.improvement_strategy = strategy
                    record.improvement_directive = directive
                    improved.append(record)
                    session.candidates.append(record)
            except Exception as e:
                logger.warning("improvement_failed", error=str(e))

        return improved

    def _improve_candidates_sync(
        self,
        candidates: list[CandidateRecord],
        context: ContextBundle,
        session: EvolutionSession,
    ) -> list[CandidateRecord]:
        """同步版改进"""
        improved: list[CandidateRecord] = []

        for candidate in candidates:
            strategy, directive = self._choose_improvement_strategy(candidate)
            try:
                new_question = self._generate_improvement_sync(
                    candidate,
                    strategy,
                    directive,
                    session,
                )
                if new_question:
                    record = self._evaluate_candidate_sync(new_question, context)
                    record.parent_id = candidate.id
                    record.generation = candidate.generation + 1
                    record.improvement_strategy = strategy
                    record.improvement_directive = directive
                    improved.append(record)
                    session.candidates.append(record)
            except Exception as e:
                logger.warning("improvement_failed", error=str(e))

        return improved

    # ── 评估候选 ─────────────────────────────────────────

    async def _evaluate_candidate(
        self,
        question: Question,
        context: ContextBundle,
    ) -> CandidateRecord:
        """对一个候选题执行完整评估流程"""
        solution = await self.engine.solver.solve(question, context)

        if not solution or solution.confidence == "low":
            return CandidateRecord(
                question=question,
                solution=solution,
                passed=False,
                failure_reasons=["solve_low_confidence"],
            )

        verification = await self.engine.verifier.verify(question, solution, context)
        failed_checks = [k for k, v in verification.checks.items() if not v]

        if verification.passed:
            score = await self.engine.annotator.annotate(question, solution)
            question.difficulty_ai = score.overall
            return CandidateRecord(
                question=question,
                solution=solution,
                verification=verification,
                difficulty_score=score,
                passed=True,
            )

        return CandidateRecord(
            question=question,
            solution=solution,
            verification=verification,
            passed=False,
            failure_reasons=failed_checks,
        )

    def _evaluate_candidate_sync(
        self,
        question: Question,
        context: ContextBundle,
    ) -> CandidateRecord:
        """同步评估"""
        solution = self.engine.solver.solve_sync(question, context)

        if not solution or solution.confidence == "low":
            return CandidateRecord(
                question=question,
                solution=solution,
                passed=False,
                failure_reasons=["solve_low_confidence"],
            )

        verification = self.engine.verifier.verify_sync(question, solution, context)
        failed_checks = [k for k, v in verification.checks.items() if not v]

        if verification.passed:
            score = self.engine.annotator.annotate_sync(question, solution)
            question.difficulty_ai = score.overall
            return CandidateRecord(
                question=question,
                solution=solution,
                verification=verification,
                difficulty_score=score,
                passed=True,
            )

        return CandidateRecord(
            question=question,
            solution=solution,
            verification=verification,
            passed=False,
            failure_reasons=failed_checks,
        )

    # ── 改进策略选择 ─────────────────────────────────────

    @staticmethod
    def _choose_improvement_strategy(
        candidate: CandidateRecord,
    ) -> tuple[str, str]:
        """根据失败原因选择改进策略和指令

        返回: (strategy_name, directive_prompt)
        """
        reasons = set(candidate.failure_reasons)

        # non_trivial 失败 → 丢弃（不值得改进）
        if "non_trivial" in reasons:
            return "discard", ""

        # 仅 1 项失败 → repair
        if len(reasons) == 1:
            reason = list(reasons)[0]
            if reason == "math_correctness":
                return "repair_math", "修正数学错误，确保条件充分且结论正确。"
            if reason == "latex_valid":
                return "repair_latex", "修正 LaTeX 语法，确保括号匹配和环境闭合。"
            if reason == "solution_relevant":
                return "repair_clarity", "改善题目表述的清晰度，确保解题方向明确。"
            if reason == "numerical_valid":
                return "repair_numerical", "修正数值计算相关的错误。"
            if reason == "solve_confidence":
                return "adapt_solvability", "调整题目使其更容易被系统求解。"

        # 多项失败但含 math_correctness → repair
        if "math_correctness" in reasons:
            return "repair_math", "重点修正数学正确性，同时修复其他问题。"

        # solve_confidence → adapt
        if "solve_low_confidence" in reasons:
            return "adapt_solvability", "简化题目结构或补充引导条件，使其可求解。"

        # 多项失败 → explore
        return "explore", "重新设计题目，保持相同知识点和难度目标。"

    # ── 生成改进版本 ─────────────────────────────────────

    async def _generate_improvement(
        self,
        candidate: CandidateRecord,
        strategy: str,
        directive: str,
        session: EvolutionSession,
    ) -> Question | None:
        """用 LLM 生成候选的改进版本"""
        if not directive:
            return None

        prompt = self._build_improvement_prompt(candidate, strategy, directive, session)
        try:
            result = await self.improve_llm.generate_json(
                prompt=prompt,
                system=EVOLUTION_SYSTEM_PROMPT,
                temperature=0.3,
            )
            return self._parse_improved_question(result, candidate)
        except Exception as e:
            logger.warning("improvement_gen_failed", error=str(e))
            return None

    def _generate_improvement_sync(
        self,
        candidate: CandidateRecord,
        strategy: str,
        directive: str,
        session: EvolutionSession,
    ) -> Question | None:
        """同步版改进生成"""
        if not directive:
            return None

        prompt = self._build_improvement_prompt(candidate, strategy, directive, session)
        try:
            result = self.improve_llm.generate_json_sync(
                prompt=prompt,
                system=EVOLUTION_SYSTEM_PROMPT,
                temperature=0.3,
            )
            return self._parse_improved_question(result, candidate)
        except Exception as e:
            logger.warning("improvement_gen_failed", error=str(e))
            return None

    def _build_improvement_prompt(
        self,
        candidate: CandidateRecord,
        strategy: str,
        directive: str,
        session: EvolutionSession,
    ) -> str:
        """构建改进 prompt"""
        if "solve" in strategy or "adapt" in strategy:
            solver_feedback = ""
            if candidate.solution:
                solver_feedback = (
                    f"解答内容: {candidate.solution.solution_latex}\n"
                    f"使用方法: {', '.join(candidate.solution.methods_used)}"
                )
            return IMPROVE_FROM_SOLVER_PROMPT.format(
                original_question_latex=candidate.question.content_latex,
                confidence=candidate.solution.confidence if candidate.solution else "无解答",
                solver_feedback=solver_feedback or "（无详细反馈）",
            )

        # 默认: 基于验证反馈改进
        passed_checks = []
        failed_checks = []
        if candidate.verification:
            for k, v in candidate.verification.checks.items():
                (passed_checks if v else failed_checks).append(k)

        return IMPROVE_FROM_VERIFICATION_PROMPT.format(
            original_question_latex=candidate.question.content_latex,
            passed_checks=", ".join(passed_checks) or "无",
            failed_checks=", ".join(failed_checks) or "无",
            suggestions=candidate.verification.suggestions if candidate.verification else directive,
        )

    @staticmethod
    def _parse_improved_question(
        data: dict,
        parent: CandidateRecord,
    ) -> Question | None:
        """将 LLM 改进输出解析为 Question"""
        content = data.get("content_latex", "").strip()
        if not content:
            return None

        import re

        content_plain = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", content)
        content_plain = re.sub(r"[\\${}^_]", "", content_plain).strip()

        return Question(
            content_latex=content,
            content_plain=content_plain,
            difficulty=data.get("estimated_difficulty", parent.question.difficulty),
            question_type=parent.question.question_type,
            subject=parent.question.subject,
            tags=list(dict.fromkeys(data.get("knowledge_points_used", []) + parent.question.tags)),
            is_generated=True,
            parent_id=parent.question.id,
            generation_meta={
                "strategy": parent.improvement_strategy,
                "changes_made": data.get("changes_made", ""),
                "solution_sketch": data.get("solution_sketch", ""),
                "methods_required": data.get("methods_required", []),
            },
        )

    # ── 选择与排序 ───────────────────────────────────────

    @staticmethod
    def _select_best(
        passed: list[CandidateRecord],
        target_difficulty: float | None = None,
    ) -> GeneratedQuestion:
        """从通过验证的候选中选出最佳

        选择 difficulty_score 最接近目标难度的候选，
        而非简单选最高分。
        """

        def distance_to_target(c: CandidateRecord) -> float:
            if not c.difficulty_score:
                return float("inf")
            if target_difficulty is not None:
                return abs(c.difficulty_score.overall - target_difficulty)
            # 无目标时，偏好中等难度（5）
            return abs(c.difficulty_score.overall - 5.0)

        best = min(passed, key=distance_to_target)

        return GeneratedQuestion(
            question=best.question,
            solution=best.solution,  # type: ignore[arg-type]
            difficulty_score=best.difficulty_score or DifficultyScore(),
            verification=best.verification or VerificationResult(passed=True),
            generation_meta={
                "generation": best.generation,
                "improvement_strategy": best.improvement_strategy,
                "parent_id": best.parent_id,
            },
        )

    @staticmethod
    def _select_best_effort(
        all_candidates: list[CandidateRecord],
    ) -> GeneratedQuestion | None:
        """所有轮次耗尽时，返回最接近通过的候选"""
        if not all_candidates:
            return None

        # 按通过的 check 数量降序排序
        def score(c: CandidateRecord) -> int:
            if not c.verification:
                return 0
            return sum(1 for v in c.verification.checks.values() if v)

        best = max(all_candidates, key=score)
        if not best.solution:
            return None

        return GeneratedQuestion(
            question=best.question,
            solution=best.solution,
            difficulty_score=best.difficulty_score or DifficultyScore(),
            verification=best.verification or VerificationResult(passed=False),
            generation_meta={
                "generation": best.generation,
                "best_effort": True,
                "passed_checks": score(best),
                "total_candidates_tried": len(all_candidates),
            },
        )

    @staticmethod
    def _select_improvable(
        candidates: list[CandidateRecord],
    ) -> list[CandidateRecord]:
        """选择值得改进的候选

        排除: 已通过的、non_trivial 失败的、已改进过多次的
        """
        improvable = []
        for c in candidates:
            if c.passed:
                continue
            if "non_trivial" in c.failure_reasons:
                continue
            if c.generation >= 3:
                continue  # 最多改进 3 代
            improvable.append(c)

        # 按失败检查数升序（越少越容易修复）
        improvable.sort(key=lambda c: len(c.failure_reasons))
        return improvable[:3]  # 每轮最多改进 3 个

    # ── 迭代摘要 ─────────────────────────────────────────

    @staticmethod
    def _build_iteration_summary(
        session: EvolutionSession,
        round_num: int,
    ) -> str:
        """构建结构化迭代摘要"""
        total = len(session.candidates)
        passed = sum(1 for c in session.candidates if c.passed)

        candidate_summaries = []
        for i, c in enumerate(session.candidates[-6:], 1):
            status = "PASS" if c.passed else f"FAIL({','.join(c.failure_reasons)})"
            gen = f"gen{c.generation}"
            preview = c.question.content_latex[:60]
            candidate_summaries.append(f"  {i}. [{gen}] {status}: {preview}...")

        # 统计失败原因
        all_reasons: dict[str, int] = {}
        for c in session.candidates:
            for r in c.failure_reasons:
                all_reasons[r] = all_reasons.get(r, 0) + 1
        top_reasons = sorted(all_reasons.items(), key=lambda x: -x[1])[:3]

        return ITERATION_SUMMARY_TEMPLATE.format(
            round_num=round_num,
            total_candidates=total,
            candidate_summaries="\n".join(candidate_summaries),
            pass_rate=f"{passed}/{total} ({100 * passed // max(total, 1)}%)",
            top_failure_reasons=", ".join(f"{r}({n})" for r, n in top_reasons),
            improvement_notes=f"第 {round_num} 轮改进",
            next_step_suggestions="基于失败模式调整生成策略",
        )

    # ── Checkpoint/Resume ────────────────────────────────

    async def _checkpoint(self, session: EvolutionSession) -> None:
        """保存会话快照"""
        if not self.checkpoint_dir:
            return

        from datetime import datetime

        session.last_checkpoint_at = datetime.now()

        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        path = self.checkpoint_dir / f"{session.session_id}.json"
        path.write_text(
            session.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.info("evolution_checkpoint", path=str(path))

    @classmethod
    def resume(
        cls,
        session_id: str,
        checkpoint_dir: Path,
        generation_engine: GenerationEngine,
        **kwargs,
    ) -> tuple[EvolutionEngine, EvolutionSession]:
        """从 checkpoint 恢复会话"""
        path = checkpoint_dir / f"{session_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        session = EvolutionSession.model_validate(data)

        engine = cls(
            generation_engine=generation_engine,
            checkpoint_dir=checkpoint_dir,
            **kwargs,
        )
        logger.info(
            "evolution_resumed",
            session_id=session_id,
            candidates=len(session.candidates),
            improvement_rounds=session.improvement_rounds,
        )
        return engine, session
