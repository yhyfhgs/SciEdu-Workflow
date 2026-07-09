"""QGenAgent — HarnessGraph 图驱动的 5 阶段生成闭环（含进化回边）

把原"双层 for 循环"重构为显式状态图（src/sciforge/agents/graph.py 的 HarnessGraph）：

    retrieve → generate → next → prefilter → solve → verify → annotate → END(成功)
    预筛/求解/验证失败 → diagnose → evolve → solve(改进件直送)  或  next(换候选/下一轮)
    队列空且 round 耗尽 → END(失败)。高清状态机图见 Docs/diagrams/01_harness_state_machine.svg

- 失败（预筛/求解/验证）统一汇入 diagnose 路由；diagnose 在 enable_evolution 且未超
  max_improvement_rounds 时走 evolve（用 improve_* prompt 定向改进后直送 solve），
  否则放弃当前候选、取队列下一个或进入下一轮 generate。
- 每个节点经 HarnessGraph 钩子记入 GenerationTrace（可观测 / 计费 / SLA 数据源）。
- enable_evolution 默认关：此时 diagnose 恒走 next，行为等价于原 for 循环（向后兼容）。
- 熔断：max_steps（图级保险丝）+ round_num / improve_round 双计数器。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from sciforge.agents.base import BaseAgent
from sciforge.agents.graph import END, StateGraph
from sciforge.agents.qgen.annotator import DifficultyAnnotator
from sciforge.agents.qgen.deep_solver import DeepSolveEngine
from sciforge.agents.qgen.evolver import Evolver
from sciforge.agents.qgen.generator import QuestionGenerator
from sciforge.agents.qgen.population import select_candidate
from sciforge.agents.qgen.prefilter import prefilter_candidate
from sciforge.agents.qgen.retriever import ContextRetriever
from sciforge.agents.qgen.solver import SolveEngine
from sciforge.agents.qgen.verifier import VerificationPipeline
from sciforge.core.models import (
    Discipline,
    GeneratedQuestion,
    GenerationRequest,
)
from sciforge.core.trace import GenerationTrace, NodeTrace

if TYPE_CHECKING:
    from sciforge.agents.qgen.verification_sandbox import VerificationSandbox
    from sciforge.config.settings import AppConfig
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
    trace: GenerationTrace | None = None  # 可观测：每节点 token/耗时/路由


# 图级死循环保险丝：max_rounds × candidates × (prefilter+solve+verify+diagnose+evolve) 的宽松上界
_MAX_GRAPH_STEPS = 400


class QGenAgent(BaseAgent[QGenInput, QGenOutput]):
    """题目生成的多阶段 Agent，内部用 HarnessGraph 编排 5 阶段 + 进化回边"""

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
        evolve_llm: LLMClient | None = None,
        max_rounds: int | None = None,
        candidates_per_round: int | None = None,
        retriever_max_kps: int | None = None,
        retriever_max_refs: int | None = None,
        retriever_max_negatives: int | None = None,
        merge_difficulty_into_verify: bool = False,
        lightweight_prefilter: bool = True,
        enable_evolution: bool = False,
        max_improvement_rounds: int = 2,
        enable_solution_relevance: bool = True,
        enable_numerical_verification: bool = False,
        require_numerical_verification: bool = False,
        enable_lean_verification: bool = False,
        require_lean_verification: bool = False,
        sandbox_timeout: int = 10,
        verification_sandbox: VerificationSandbox | None = None,
        sandbox_trust_policy: str = "container_required",
        deep_solve_enabled: bool = False,
        solver_max_turns: int = 5,
        population_selection_enabled: bool = False,
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
        self.enable_evolution = enable_evolution
        self.deep_solve_enabled = deep_solve_enabled
        self.population_selection_enabled = population_selection_enabled
        self.max_improvement_rounds = int(max_improvement_rounds)
        self.retriever = ContextRetriever(
            storage,
            max_kps=retriever_max_kps,
            max_refs=retriever_max_refs,
            max_negatives=retriever_max_negatives,
        )
        self.generator = QuestionGenerator(llm, discipline)
        self.solver = SolveEngine(solve_llm or llm, discipline)
        self.deep_solver = DeepSolveEngine(
            solve_llm or llm,
            discipline,
            sandbox_timeout=sandbox_timeout,
            max_turns=solver_max_turns,
            sandbox=verification_sandbox,
            sandbox_trust_policy=sandbox_trust_policy,
        )
        self.verifier = VerificationPipeline(
            review_llm or llm,
            discipline,
            enable_solution_relevance=enable_solution_relevance,
            enable_numerical_verification=enable_numerical_verification,
            require_numerical_verification=require_numerical_verification,
            enable_lean_verification=enable_lean_verification,
            require_lean_verification=require_lean_verification,
            sandbox_timeout=sandbox_timeout,
            sandbox=verification_sandbox,
            sandbox_trust_policy=sandbox_trust_policy,
        )
        self.annotator = DifficultyAnnotator(tag_llm or llm, discipline)
        self.evolver = Evolver(evolve_llm or llm, discipline)

    @classmethod
    def from_config(
        cls,
        *,
        llm: LLMClient,
        storage: SQLiteStorageBackend,
        config: AppConfig,
        discipline: Discipline = Discipline.MATH,
        review_llm: LLMClient | None = None,
        solve_llm: LLMClient | None = None,
        tag_llm: LLMClient | None = None,
        evolve_llm: LLMClient | None = None,
        **overrides: Any,
    ) -> QGenAgent:
        """将 AppConfig 一次性绑定到 QGen，避免 CLI/API 漏传验证开关。"""
        from sciforge.agents.qgen.verification_sandbox import build_verification_sandbox

        sandbox = build_verification_sandbox(config)
        params: dict[str, Any] = {
            "max_rounds": config.generation_max_rounds,
            "candidates_per_round": config.generation_candidates_per_round,
            "retriever_max_kps": config.qgen_retriever_max_kps,
            "retriever_max_refs": config.qgen_retriever_max_refs,
            "retriever_max_negatives": config.qgen_retriever_max_negatives,
            "merge_difficulty_into_verify": config.qgen_merge_difficulty_into_verify,
            "lightweight_prefilter": config.qgen_lightweight_prefilter,
            "enable_evolution": config.evolution_enabled,
            "max_improvement_rounds": config.evolution_max_improvement_rounds,
            "enable_numerical_verification": config.enable_numerical_verification,
            "require_numerical_verification": config.require_numerical_verification,
            "enable_lean_verification": config.enable_lean_verification,
            "require_lean_verification": config.require_lean_verification,
            "sandbox_timeout": config.sandbox_timeout,
            "verification_sandbox": sandbox,
            "sandbox_trust_policy": config.sandbox_trust_policy,
            "deep_solve_enabled": config.qgen_deep_solve_enabled,
            "solver_max_turns": config.solver_max_turns,
            "population_selection_enabled": config.qgen_population_selection_enabled,
        }
        params.update(overrides)
        return cls(
            llm=llm,
            storage=storage,
            discipline=discipline,
            review_llm=review_llm,
            solve_llm=solve_llm,
            tag_llm=tag_llm,
            evolve_llm=evolve_llm,
            **params,
        )

    def create_trace(self, request: GenerationRequest) -> GenerationTrace:
        """创建可持久化的生成追踪；持久 Harness 与单次 run 共用。"""
        return GenerationTrace(
            request_summary={
                "subject": request.subject,
                "topics": list(request.topics),
                "difficulty_target": request.difficulty_target,
                "question_type": request.question_type,
                "strategy": request.strategy.value,
                "discipline": self.discipline.value,
            }
        )

    @staticmethod
    def create_graph_state(request: GenerationRequest) -> dict[str, Any]:
        """构造 QGen 图的唯一初始状态，便于 checkpoint 精确恢复。"""
        return {
            "request": request,
            "context": None,
            "queue": [],
            "accepted": [],
            "lineage": [],
            "current": None,
            "solution": None,
            "verification": None,
            "round_num": 0,
            "improve_round": 0,
            "candidates_tried": 0,
            "prefilter_rejected": 0,
            "fail_kind": "",
            "fail_detail": "",
            "route": "",
            "output": None,
        }

    @staticmethod
    def finalize_graph_state(final: dict[str, Any], trace: GenerationTrace) -> QGenOutput:
        """将终止图状态收敛为公共输出；恢复路径不复制逻辑。"""
        output: QGenOutput = final.get("output") or QGenOutput(
            generated=None,
            rounds_used=int(final.get("round_num", 0)),
            candidates_tried=int(final.get("candidates_tried", 0)),
            reason="未产出候选",
        )
        trace.rounds_used = int(final.get("round_num", 0))
        trace.improve_rounds_used = int(final.get("improve_round", 0))
        trace.outcome = "success" if output.generated is not None else "fail"
        if output.generated is not None:
            trace.final_question_id = output.generated.question.id
        output.trace = trace
        return output

    def llm_clients(self) -> tuple[LLMClient, ...]:
        """返回 Harness 可观测/预算需要统计的去重 client 集合。"""
        candidates = (
            self.llm,
            self.generator.llm,
            self.solver.llm,
            self.deep_solver.llm,
            self.verifier.review_llm,
            self.annotator.llm,
            self.evolver.llm,
        )
        unique: list[LLMClient] = []
        seen: set[int] = set()
        for client in candidates:
            if id(client) not in seen:
                seen.add(id(client))
                unique.append(client)
        return tuple(unique)

    async def run(self, input: QGenInput) -> QGenOutput:
        request = input.request
        self._log_start(
            subject=request.subject,
            topics=request.topics,
            target=request.difficulty_target,
            max_rounds=self.max_rounds,
            enable_evolution=self.enable_evolution,
        )

        trace = self.create_trace(request)
        compiled = self._build_graph().compile()
        state = self.create_graph_state(request)

        enter_at: dict[str, float] = {}

        def on_enter(node: str, _st: dict[str, Any]) -> None:
            enter_at[node] = time.perf_counter()

        def on_exit(node: str, st: dict[str, Any], _updates: dict[str, Any] | None) -> None:
            dt_ms = int((time.perf_counter() - enter_at.get(node, time.perf_counter())) * 1000)
            trace.add_node(NodeTrace(node=node, duration_ms=dt_ms, note=str(st.get("route", ""))))

        final = await compiled.ainvoke(
            state,
            _MAX_GRAPH_STEPS,
            on_node_enter=on_enter,
            on_node_exit=on_exit,
        )

        output = self.finalize_graph_state(final, trace)

        self._log_end(
            success=output.generated is not None,
            rounds=trace.rounds_used,
            candidates_tried=output.candidates_tried,
            prefilter_rejected=int(final.get("prefilter_rejected", 0)),
            trace_nodes=len(trace.nodes),
        )
        return output

    # ── 状态图构造 ──────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        g = StateGraph()
        g.add_node("retrieve", self._n_retrieve)
        g.add_node("generate", self._n_generate)
        g.add_node("next", self._n_next)
        g.add_node("prefilter", self._n_prefilter)
        g.add_node("solve", self._n_solve)
        g.add_node("verify", self._n_verify)
        g.add_node("annotate", self._n_annotate)
        g.add_node("diagnose", self._n_diagnose)
        g.add_node("evolve", self._n_evolve)
        g.add_node("select", self._n_select)

        g.set_entry_point("retrieve")
        g.add_edge("retrieve", "generate")
        g.add_edge("generate", "next")
        g.add_conditional_edges(
            "next",
            lambda s: str(s["route"]),
            {"have": "prefilter", "regen": "generate", "select": "select", "end": END},
        )
        g.add_conditional_edges(
            "prefilter", lambda s: str(s["route"]), {"pass": "solve", "fail": "next"}
        )
        g.add_conditional_edges(
            "solve", lambda s: str(s["route"]), {"ok": "verify", "bad": "diagnose"}
        )
        g.add_conditional_edges(
            "verify", lambda s: str(s["route"]), {"pass": "annotate", "fail": "diagnose"}
        )
        g.add_conditional_edges(
            "annotate",
            lambda _s: "continue" if self.population_selection_enabled else "end",
            {"continue": "next", "end": END},
        )
        g.add_conditional_edges(
            "diagnose", lambda s: str(s["route"]), {"evolve": "evolve", "next": "next"}
        )
        g.add_conditional_edges(
            "evolve", lambda s: str(s["route"]), {"evolved": "solve", "next": "next"}
        )
        g.add_edge("select", END)
        return g

    # ── 节点实现（async (state) -> None，原地更新 state）────────

    async def _n_retrieve(self, s: dict[str, Any]) -> None:
        s["context"] = self.retriever.build_context(s["request"], self.discipline)

    async def _n_generate(self, s: dict[str, Any]) -> None:
        candidates = await self.generator.generate_candidates(
            s["request"], s["context"], n=self.candidates_per_round
        )
        s["queue"] = list(candidates)
        s["round_num"] += 1
        s["candidates_tried"] += len(candidates)
        s["lineage"].extend(
            {
                "candidate_id": candidate.id,
                "parent_id": "",
                "generation": 0,
                "mutation": "generate",
                "round": s["round_num"],
            }
            for candidate in candidates
        )

    async def _n_next(self, s: dict[str, Any]) -> None:
        if s["queue"]:
            s["current"] = s["queue"].pop(0)
            s["improve_round"] = 0  # 新候选重置进化计数
            s["route"] = "have"
        elif self.population_selection_enabled and s["accepted"]:
            s["route"] = "select"
        elif s["round_num"] < self.max_rounds:
            s["route"] = "regen"
        else:
            s["route"] = "end"
            s["output"] = QGenOutput(
                generated=None,
                rounds_used=s["round_num"],
                candidates_tried=s["candidates_tried"],
                reason=(
                    f"{self.max_rounds} 轮 × {self.candidates_per_round} 候选后仍未通过验证"
                    f"（预筛拒绝 {s['prefilter_rejected']} 次）"
                ),
            )

    async def _n_prefilter(self, s: dict[str, Any]) -> None:
        if self.lightweight_prefilter:
            passed, _reason = prefilter_candidate(s["current"], s["context"].negative_examples)
            if not passed:
                s["prefilter_rejected"] += 1
                s["route"] = "fail"
                return
        s["route"] = "pass"

    async def _n_solve(self, s: dict[str, Any]) -> None:
        solution = (
            await self.deep_solver.solve_deep(s["current"], s["context"])
            if self.deep_solve_enabled
            else await self.solver.solve(s["current"], s["context"])
        )
        s["solution"] = solution
        if solution is None or solution.confidence == "low":
            s["route"] = "bad"
            s["fail_kind"] = "solver"
            s["fail_detail"] = "求解引擎置信度低或无解，题目可能存在问题"
        else:
            s["route"] = "ok"

    async def _n_verify(self, s: dict[str, Any]) -> None:
        verification = await self.verifier.verify(s["current"], s["solution"], s["context"])
        s["verification"] = verification
        if verification.passed:
            s["route"] = "pass"
        else:
            s["route"] = "fail"
            s["fail_kind"] = "verification"
            s["fail_detail"] = verification.suggestions
            if verification.has_suggestions:
                s["context"].feedback_from_previous_round = verification.suggestions

    async def _n_annotate(self, s: dict[str, Any]) -> None:
        verification = s["verification"]
        difficulty_score = (
            verification.difficulty_score if self.merge_difficulty_into_verify else None
        )
        skipped_annotate_call = difficulty_score is not None
        if difficulty_score is None:
            difficulty_score = await self.annotator.annotate(s["current"], s["solution"])
        candidate = s["current"]
        candidate.difficulty = int(round(difficulty_score.overall))
        # 标记生成元数据（schema v9）：区分生成题 vs 手动题，支撑版权/计费/统计
        candidate.is_generated = True
        _model_id = getattr(self.llm, "model", "")
        candidate.generator_model = _model_id if isinstance(_model_id, str) else ""
        candidate.generated_at = datetime.now()
        generated = GeneratedQuestion(
            question=candidate,
            solution=s["solution"],
            difficulty_score=difficulty_score,
            verification=verification,
            generation_meta={
                "round": s["round_num"],
                "improve_round": s["improve_round"],
                "strategy": s["request"].strategy.value,
                "discipline": self.discipline.value,
                "prefilter_rejected": s["prefilter_rejected"],
                "skipped_annotate_call": skipped_annotate_call,
                "lineage": list(s["lineage"]),
            },
        )
        if self.population_selection_enabled:
            s["accepted"].append(generated)
            s["route"] = "continue"
        else:
            s["output"] = QGenOutput(
                generated=generated,
                rounds_used=s["round_num"],
                candidates_tried=s["candidates_tried"],
            )

    async def _n_diagnose(self, s: dict[str, Any]) -> None:
        if self.enable_evolution and s["improve_round"] < self.max_improvement_rounds:
            s["route"] = "evolve"
        else:
            s["route"] = "next"

    async def _n_evolve(self, s: dict[str, Any]) -> None:
        record = await self.evolver.evolve(
            s["current"],
            failure_kind=s["fail_kind"] or "verification",
            failure_detail=s["fail_detail"],
            parent_generation=s["improve_round"],
            context=s["context"],
        )
        if record is None:
            s["route"] = "next"  # 进化失败 → 放弃当前候选，试下一个
        else:
            s["current"] = record.question
            s["improve_round"] += 1
            s["lineage"].append(
                {
                    "candidate_id": record.question.id,
                    "parent_id": record.parent_id,
                    "generation": record.generation,
                    "mutation": record.improvement_strategy,
                    "directive": record.improvement_directive,
                    "round": s["round_num"],
                }
            )
            s["route"] = "evolved"  # 改进件直送 solve

    async def _n_select(self, s: dict[str, Any]) -> None:
        selected, objectives = select_candidate(s["accepted"], s["request"], s["context"])
        objective_by_id = {objective.candidate_id: objective for objective in objectives}
        selected.generation_meta["population_size"] = len(s["accepted"])
        selected.generation_meta["objective"] = objective_by_id[selected.question.id].model_dump()
        selected.generation_meta["population_objectives"] = [
            objective.model_dump() for objective in objectives
        ]
        s["output"] = QGenOutput(
            generated=selected,
            rounds_used=s["round_num"],
            candidates_tried=s["candidates_tried"],
        )
        s["route"] = "selected"
