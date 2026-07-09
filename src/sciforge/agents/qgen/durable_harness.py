"""QGen 的可恢复 long-horizon 执行器。

它将 QGenAgent 的 StateGraph 节点边界映射到 ``SQLiteRunStore``：
每个节点原子 claim step，完成后记录真实 LLM call/token/wall-time，
再保存包含 ``next_node`` 的 immutable checkpoint。恢复时不重放已完成节点。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, Field

from sciforge.agents.qgen.agent import QGenAgent, QGenInput, QGenOutput
from sciforge.agents.runtime import (
    BudgetExceededError,
    InvalidRunTransitionError,
    RunBudget,
    RunCancellationRequestedError,
    RunStatus,
    SQLiteRunStore,
)
from sciforge.core.models import (
    ContextBundle,
    GeneratedQuestion,
    GenerationRequest,
    Question,
    Solution,
    VerificationResult,
)
from sciforge.core.trace import GenerationTrace, NodeTrace


class QGenGraphSnapshot(BaseModel):
    """可 JSON 序列化的 QGen graph state；禁止 pickle/隐式 Python 对象。"""

    request: GenerationRequest
    context: ContextBundle | None = None
    queue: list[Question] = Field(default_factory=list)
    accepted: list[GeneratedQuestion] = Field(default_factory=list)
    lineage: list[dict[str, object]] = Field(default_factory=list)
    current: Question | None = None
    solution: Solution | None = None
    verification: VerificationResult | None = None
    round_num: int = 0
    improve_round: int = 0
    candidates_tried: int = 0
    prefilter_rejected: int = 0
    fail_kind: str = ""
    fail_detail: str = ""
    route: str = ""
    output: QGenOutput | None = None

    @classmethod
    def from_state(cls, state: Mapping[str, Any]) -> QGenGraphSnapshot:
        return cls.model_validate(dict(state))

    def to_state(self) -> dict[str, Any]:
        return {
            "request": self.request,
            "context": self.context,
            "queue": list(self.queue),
            "accepted": list(self.accepted),
            "lineage": list(self.lineage),
            "current": self.current,
            "solution": self.solution,
            "verification": self.verification,
            "round_num": self.round_num,
            "improve_round": self.improve_round,
            "candidates_tried": self.candidates_tried,
            "prefilter_rejected": self.prefilter_rejected,
            "fail_kind": self.fail_kind,
            "fail_detail": self.fail_detail,
            "route": self.route,
            "output": self.output,
        }


def _client_usage(agent: QGenAgent) -> tuple[int, int]:
    clients = agent.llm_clients()
    return (
        sum(int(getattr(client, "call_count", 0)) for client in clients),
        sum(int(getattr(client, "total_tokens", 0)) for client in clients),
    )


def _progress_payload(
    node: str,
    next_node: str,
    state: Mapping[str, Any],
    step_number: int,
) -> dict[str, Any]:
    """Project graph state into a bounded, UI-safe progress event.

    Checkpoints intentionally contain the full recoverable graph state.  Event
    consumers need a much smaller contract: enough to render retrieval,
    solving, verification and evolution progress, without exposing prompts,
    reference corpus contents or hidden model deliberation.
    """
    payload: dict[str, Any] = {
        "phase": node,
        "next_phase": next_node,
        "step": step_number,
        "route": str(state.get("route", "")),
        "round": int(state.get("round_num", 0)),
        "candidates_tried": int(state.get("candidates_tried", 0)),
    }

    context = state.get("context")
    if node == "retrieve" and isinstance(context, ContextBundle):
        payload["context"] = {
            "knowledge_points": len(context.knowledge_points),
            "reference_questions": len(context.reference_questions),
            "negative_examples": len(context.negative_examples),
        }

    if node == "generate":
        queue = state.get("queue")
        payload["candidate_count"] = len(queue) if isinstance(queue, list) else 0

    solution = state.get("solution")
    if node == "solve" and isinstance(solution, Solution):
        payload["solution"] = {
            "confidence": solution.confidence,
            "methods_used": list(solution.methods_used[:12]),
            "step_count": solution.step_count,
            "self_check": solution.self_check[:500],
            "length": len(solution.solution_latex or solution.solution_plain),
        }

    verification = state.get("verification")
    if node == "verify" and isinstance(verification, VerificationResult):
        payload["verification"] = {
            "passed": verification.passed,
            "checks": dict(verification.checks),
            "suggestions": verification.suggestions[:1000],
            "evidence": [
                {
                    "kind": item.kind,
                    "status": item.status.value,
                    "required": item.required,
                    "backend": item.backend,
                    "summary": item.summary[:500],
                }
                for item in verification.evidence[:24]
            ],
        }

    if node in {"annotate", "select"}:
        accepted = state.get("accepted")
        payload["accepted_count"] = len(accepted) if isinstance(accepted, list) else 0
        output = state.get("output")
        if isinstance(output, QGenOutput) and output.generated is not None:
            payload["result"] = {
                "question_id": output.generated.question.id,
                "difficulty": output.generated.difficulty_score.overall,
                "verified": output.generated.verification.passed,
            }

    if node == "evolve":
        lineage = state.get("lineage")
        if isinstance(lineage, list) and lineage and isinstance(lineage[-1], dict):
            # Lineage contains IDs and mutation labels, never prompt text.
            payload["lineage"] = dict(lineage[-1])

    return payload


class DurableQGenHarness:
    """在 durable store 上执行或恢复一次 QGen graph。"""

    def __init__(self, agent: QGenAgent, store: SQLiteRunStore) -> None:
        self.agent = agent
        self.store = store

    async def run(
        self,
        input: QGenInput,
        *,
        run_id: str | None = None,
        budget: RunBudget | None = None,
        metadata: Mapping[str, Any] | None = None,
        resume: bool = False,
    ) -> tuple[str, QGenOutput]:
        existing = self.store.get_run(run_id) if run_id else None
        if existing is not None and existing.status is RunStatus.SUCCEEDED:
            if existing.output_payload is None:
                raise InvalidRunTransitionError("completed qgen run has no output payload")
            return existing.run_id, QGenOutput.model_validate_json(
                json.dumps(existing.output_payload, ensure_ascii=False)
            )
        if existing is not None and not resume:
            raise InvalidRunTransitionError(
                f"run {existing.run_id} already exists as {existing.status.value}; use resume=True"
            )
        if existing is not None and existing.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
            raise InvalidRunTransitionError(
                f"cannot resume terminal run {existing.run_id} from {existing.status.value}"
            )

        checkpoint = None
        if existing is None:
            created = self.store.create_run(
                "qgen",
                input.request.model_dump(mode="json"),
                budget=budget,
                metadata=metadata,
                run_id=run_id,
            )
            run_id = created.run_id
        else:
            run_id = existing.run_id
            checkpoint = self.store.prepare_resume(run_id)

        assert run_id is not None
        self.store.begin_run(run_id)

        if checkpoint is None:
            state = self.agent.create_graph_state(input.request)
            trace = self.agent.create_trace(input.request)
            start_at: str | None = None
            completed_steps = 0
        else:
            graph_payload = checkpoint.state.get("graph")
            trace_payload = checkpoint.state.get("trace")
            if not isinstance(graph_payload, dict) or not isinstance(trace_payload, dict):
                raise InvalidRunTransitionError("qgen checkpoint missing graph/trace payload")
            state = QGenGraphSnapshot.model_validate(graph_payload).to_state()
            trace = GenerationTrace.model_validate_json(
                json.dumps(trace_payload, ensure_ascii=False)
            )
            cursor_node = checkpoint.cursor.get("next_node")
            if not isinstance(cursor_node, str) or not cursor_node:
                raise InvalidRunTransitionError("qgen checkpoint missing next_node cursor")
            start_at = cursor_node
            completed_steps = checkpoint.step_number

        compiled = self.agent._build_graph().compile()
        active_step: dict[str, Any] = {}
        attempts: dict[str, int] = {}
        for step in self.store.list_steps(run_id):
            attempts[step.name] = max(attempts.get(step.name, 0), step.attempt)

        def on_enter(node: str, graph_state: dict[str, Any]) -> None:
            self.store.ensure_can_continue(run_id)
            attempt = attempts.get(node, 0) + 1
            attempts[node] = attempt
            step = self.store.begin_step(
                run_id,
                node,
                attempt=attempt,
                input_payload={
                    "round": int(graph_state.get("round_num", 0)),
                    "route": str(graph_state.get("route", "")),
                },
            )
            calls, tokens = _client_usage(self.agent)
            active_step.clear()
            active_step.update(
                {
                    "id": step.step_id,
                    "node": node,
                    "started": time.perf_counter(),
                    "calls": calls,
                    "tokens": tokens,
                }
            )

        def on_exit(
            node: str,
            graph_state: dict[str, Any],
            _updates: dict[str, Any] | None,
        ) -> None:
            if active_step.get("node") != node:
                raise RuntimeError(f"durable step mismatch for node {node}")
            elapsed = max(0.0, time.perf_counter() - float(active_step["started"]))
            calls, tokens = _client_usage(self.agent)
            call_delta = max(0, calls - int(active_step["calls"]))
            token_delta = max(0, tokens - int(active_step["tokens"]))
            # 用量预算必须先于 step.complete；超额时 store 原子中断 step 并 pause。
            self.store.record_usage(
                run_id,
                llm_calls=call_delta,
                tokens=token_delta,
                wall_time_seconds=elapsed,
            )
            self.store.complete_step(
                str(active_step["id"]),
                {
                    "round": int(graph_state.get("round_num", 0)),
                    "route": str(graph_state.get("route", "")),
                },
            )
            trace.add_node(
                NodeTrace(
                    node=node,
                    duration_ms=int(elapsed * 1000),
                    prompt_tokens_est=token_delta,
                    note=str(graph_state.get("route", "")),
                )
            )
            active_step.clear()

        def on_transition(
            node: str,
            next_node: str,
            graph_state: dict[str, Any],
            step_number: int,
        ) -> None:
            snapshot = QGenGraphSnapshot.from_state(graph_state)
            self.store.save_checkpoint(
                run_id,
                step_number,
                node,
                {
                    "graph": snapshot.model_dump(mode="json"),
                    "trace": trace.model_dump(mode="json"),
                },
                cursor={"next_node": next_node},
            )
            self.store.append_event(
                run_id,
                "graph.transition",
                {"node": node, "next_node": next_node, "step": step_number},
            )
            self.store.append_event(
                run_id,
                "qgen.progress",
                _progress_payload(node, next_node, graph_state, step_number),
            )

        try:
            final = await compiled.ainvoke(
                state,
                max_steps=400,
                on_node_enter=on_enter,
                on_node_exit=on_exit,
                on_transition=on_transition,
                start_at=start_at,
                completed_steps=completed_steps,
            )
            output = self.agent.finalize_graph_state(final, trace)
            self.store.complete_run(run_id, output.model_dump(mode="json"))
            return run_id, output
        except RunCancellationRequestedError:
            self.store.acknowledge_cancel(run_id, reason="cancelled at graph node boundary")
            raise
        except BudgetExceededError:
            # store 已原子将 run 设为 paused，保留恢复可能。
            raise
        except Exception as exc:
            current = self.store.require_run(run_id)
            if current.status is RunStatus.RUNNING:
                if current.current_step_id:
                    self.store.fail_step(current.current_step_id, str(exc))
                self.store.fail_run(run_id, str(exc))
            raise


__all__ = ["DurableQGenHarness", "QGenGraphSnapshot"]
