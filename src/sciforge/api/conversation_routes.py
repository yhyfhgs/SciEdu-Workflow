"""Conversation, durable-run, SSE and knowledge-browser API routes."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from sciforge.agents.runtime import (
    TERMINAL_RUN_STATUSES,
    RunBudget,
    RunCancellationRequestedError,
    RunRecord,
    RunStatus,
    SQLiteRunStore,
)
from sciforge.api.conversations import (
    ConversationRecord,
    ConversationStore,
    MessageRecord,
    RunLinkRecord,
)
from sciforge.api.schemas import (
    ActionTemplateOut,
    ActionTemplatesResponse,
    AgentRunOut,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationMessageAccepted,
    ConversationMessageOut,
    ConversationMessageRequest,
    ConversationMessagesResponse,
    ConversationOut,
    KnowledgeSearchItem,
    KnowledgeSearchResponse,
    RunBudgetOut,
    RunCancelResponse,
    RunResumeRequest,
    RunResumeResponse,
    RunUsageOut,
)
from sciforge.api.tenant_storage import (
    TenantScopedStorageView,
    TenantStorageConfigurationError,
)
from sciforge.core.models import (
    ContextBundle,
    Discipline,
    GenerationRequest,
    Question,
    Solution,
    VariantStrategy,
    VerificationResult,
)
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.storage.query import QualityGateFilter

if TYPE_CHECKING:
    from sciforge.agents import QGenAgent
    from sciforge.storage.saas import SaasStore

logger = structlog.get_logger()

LOCAL_TENANT_ID = "local"

ChatMode = Literal[
    "new",
    "evolve",
    "variant",
    "adjust_difficulty",
    "deepen",
    "solve",
    "evaluate",
]

BackgroundTaskSpec = tuple[Callable[..., Awaitable[None]], dict[str, Any]]
ScopeAgentStorage = Callable[..., AbstractContextManager[None]]

_PROOF_QUESTION_TYPES = frozenset(
    {"proof", "existence_proof", "constructive_proof", "counterexample"}
)
_NUMERIC_QUESTION_TYPES = frozenset(
    {"calculation", "application", "numerical", "numeric", "computation"}
)
_ASSISTANT_TERMINAL_EVENTS = frozenset(
    {"assistant.final", "assistant.error", "assistant.cancelled", "assistant.paused"}
)
_SSE_TERMINAL_GRACE_SECONDS = 0.25
_SSE_HEARTBEAT_SECONDS = 15.0
_CONVERSATION_CONTEXT_MESSAGES = 8
_CONVERSATION_CONTEXT_CHARS = 6000
_CONVERSATION_CONTEXT_PER_MESSAGE_CHARS = 1200

_GENERATION_MODES = frozenset({"new", "evolve", "variant", "adjust_difficulty", "deepen"})
_SOURCE_GENERATION_MODES = frozenset({"variant", "adjust_difficulty", "deepen"})


@dataclass(frozen=True, slots=True)
class _VerificationPlan:
    solution_relevance: bool
    numerical_enabled: bool
    numerical_required: bool
    lean_enabled: bool
    lean_required: bool


def _build_verification_plan(
    policy: str,
    discipline: Discipline,
    question_type: str,
) -> _VerificationPlan:
    """Return the immutable evidence plan shared by request, run and recovery paths."""
    if policy not in {"fast", "balanced", "strict", "formal"}:
        raise ValueError(f"未知验证策略: {policy}")
    proof_applicable = discipline is Discipline.MATH and question_type in _PROOF_QUESTION_TYPES
    numeric_applicable = question_type in _NUMERIC_QUESTION_TYPES
    if policy == "formal" and not proof_applicable:
        raise ValueError("formal 验证只适用于数学证明类题目，并要求 Lean 证据")
    if policy == "fast":
        return _VerificationPlan(False, False, False, False, False)
    if policy == "balanced":
        return _VerificationPlan(True, numeric_applicable, False, False, False)
    if policy == "formal":
        return _VerificationPlan(True, False, False, True, True)
    return _VerificationPlan(
        True,
        numeric_applicable,
        numeric_applicable,
        proof_applicable,
        proof_applicable,
    )


def _validate_question_contract(
    discipline: Discipline,
    question_type: str,
    question_form: str = "",
) -> None:
    try:
        spec = DISCIPLINE_REGISTRY.get(discipline)
    except KeyError as exc:
        raise ValueError(f"学科 {discipline.value!r} 尚未配置命题规范") from exc
    if question_type not in spec.allowed_question_types:
        allowed = ", ".join(spec.allowed_question_types)
        raise ValueError(
            f"学科 {discipline.value} 不支持 question_type={question_type!r}; 允许值: {allowed}"
        )
    if question_form and question_form not in spec.allowed_question_forms:
        allowed = ", ".join(spec.allowed_question_forms)
        raise ValueError(
            f"学科 {discipline.value} 不支持 question_form={question_form!r}; 允许值: {allowed}"
        )


def _default_question_type(discipline: Discipline) -> str:
    try:
        return DISCIPLINE_REGISTRY.get(discipline).allowed_question_types[0]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"学科 {discipline.value!r} 没有可用题型") from exc


def _api_verification_plan(
    policy: str,
    discipline: Discipline,
    question_type: str,
) -> _VerificationPlan:
    try:
        return _build_verification_plan(policy, discipline, question_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


_ACTION_TEMPLATES = (
    ActionTemplateOut(
        id="new",
        label="生成新题",
        description="按学科、主题、题型和目标难度生成并验证一道新题。",
        required_fields=["topics 或 content"],
        strategy=VariantStrategy.TOPIC_GENERATION.value,
        durable_kind="qgen",
    ),
    ActionTemplateOut(
        id="evolve",
        label="自进化生成",
        description="启用失败诊断、候选进化与多目标 population selection。",
        required_fields=["topics 或 content"],
        strategy=VariantStrategy.TOPIC_GENERATION.value,
        durable_kind="qgen",
    ),
    ActionTemplateOut(
        id="variant",
        label="微调变式",
        description="以租户可见源题为锚点，执行显式 A1/A2/A3/A6 变式策略。",
        required_fields=["source_question_id"],
        strategy=VariantStrategy.PARAMETER_SHIFT.value,
        durable_kind="qgen",
    ),
    ActionTemplateOut(
        id="adjust_difficulty",
        label="调整难度",
        description="比较源题与目标难度，自动选择 A4 提升或 A5 简化。",
        required_fields=["source_question_id", "difficulty"],
        strategy="A4_escalate / A5_simplify",
        durable_kind="qgen",
    ),
    ActionTemplateOut(
        id="deepen",
        label="深入拓展",
        description="基于源题采用 B2 跨主题策略，并启用 deep solve。",
        required_fields=["source_question_id"],
        strategy=VariantStrategy.CROSS_TOPIC.value,
        durable_kind="qgen",
    ),
    ActionTemplateOut(
        id="solve",
        label="独立求解",
        description="用 SolveAgent 求解租户可见题目或消息中的完整题面。",
        required_fields=["source_question_id 或 content"],
        durable_kind="solve",
    ),
    ActionTemplateOut(
        id="evaluate",
        label="评估题目与解答",
        description="准备已有/新求解答案，再运行独立 reviewer 与配置的数值/Lean 验证。",
        required_fields=["source_question_id"],
        durable_kind="evaluate",
    ),
)


class _RunRecoveryCoordinator:
    """Own background recovery tasks for one FastAPI worker process.

    The durable SQLite store remains the source of truth.  The in-memory set is
    only a single-process duplicate-scheduling guard; deployments must not run
    multiple API workers against the same runtime DB until leases are added.
    """

    def __init__(
        self,
        *,
        qgen_factory: Callable[[Discipline], Any],
        solve_factory: Callable[[Discipline], Any],
        conversations: ConversationStore,
        runs: SQLiteRunStore,
        storage_factory: Callable[[], Any] | None,
        scope_agent_storage: ScopeAgentStorage,
        saas_store: SaasStore | None,
    ) -> None:
        self.qgen_factory = qgen_factory
        self.solve_factory = solve_factory
        self.conversations = conversations
        self.runs = runs
        self.storage_factory = storage_factory
        self.scope_agent_storage = scope_agent_storage
        self.saas_store = saas_store
        self._scheduled: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def is_scheduled(self, run_id: str) -> bool:
        return run_id in self._scheduled

    def schedule(self, record: RunRecord, *, reason: str) -> bool:
        """Rebuild and schedule a non-terminal run exactly once in this worker."""
        if record.run_id in self._scheduled:
            return False
        current = self.runs.require_run(record.run_id)
        if current.status in TERMINAL_RUN_STATUSES:
            return False
        task_fn, task_kwargs = _rebuild_background_task(
            current,
            qgen_factory=self.qgen_factory,
            solve_factory=self.solve_factory,
            conversations=self.conversations,
            runs=self.runs,
            storage_factory=self.storage_factory,
            scope_agent_storage=self.scope_agent_storage,
            saas_store=self.saas_store,
        )
        if current.status is RunStatus.RUNNING:
            # A RUNNING record at process startup has lost its worker.  Interrupt
            # the stale active step before any manual runner claims a new attempt.
            self.runs.prepare_resume(current.run_id, reason=reason)
        self.runs.append_event(current.run_id, "run.recovery_scheduled", {"reason": reason})
        self._scheduled.add(current.run_id)
        try:
            task = asyncio.create_task(
                self._execute(current.run_id, task_fn, task_kwargs),
                name=f"sciforge-recover-{current.run_id}",
            )
        except Exception:
            self._scheduled.discard(current.run_id)
            raise
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)
        return True

    async def startup(self) -> None:
        """Recover work that had a worker before shutdown; paused work is opt-in."""
        self._reconcile_succeeded_deliveries()
        for record in self.runs.list_recoverable_runs(limit=10_000):
            if record.status not in {RunStatus.PENDING, RunStatus.RUNNING}:
                continue
            try:
                self.schedule(record, reason="service startup recovery")
            except Exception as exc:  # noqa: BLE001
                error = str(exc).strip()[:500] or type(exc).__name__
                latest = self.runs.get_run(record.run_id)
                if latest is not None and latest.status is RunStatus.RUNNING:
                    try:
                        self.runs.prepare_resume(
                            record.run_id,
                            reason="startup recovery could not rebuild task",
                        )
                    except Exception:  # noqa: BLE001
                        logger.exception("run_recovery_prepare_failed", run_id=record.run_id)
                self.runs.append_event(
                    record.run_id,
                    "run.recovery_failed",
                    {"reason": "task reconstruction failed", "error": error},
                )
                logger.exception("run_recovery_schedule_failed", run_id=record.run_id)

    def _reconcile_succeeded_deliveries(self) -> None:
        """Repair product delivery after runtime success without rerunning Agents."""
        supported_kinds = {"qgen", "solve", "evaluate"}
        after_created_at = ""
        after_run_id = ""
        while True:
            page = self.conversations.list_run_links(
                limit=1_000,
                after_created_at=after_created_at,
                after_run_id=after_run_id,
            )
            if not page:
                break
            for link in page:
                self._reconcile_succeeded_link(link, supported_kinds)
            after_created_at = page[-1].created_at
            after_run_id = page[-1].run_id

    def _reconcile_succeeded_link(
        self,
        link: RunLinkRecord,
        supported_kinds: set[str],
    ) -> None:
        """Reconcile one trusted product link while isolating corrupt rows."""
        record = self.runs.get_run(link.run_id)
        if (
            record is None
            or record.status is not RunStatus.SUCCEEDED
            or record.run_kind not in supported_kinds
        ):
            return
        if (
            record.metadata.get("tenant_id") != link.tenant_id
            or record.metadata.get("conversation_id") != link.conversation_id
        ):
            # A link and runtime metadata must agree before product data is
            # published.  Corrupt or unrelated runs remain untouched.
            return
        try:
            if record.run_kind == "qgen":
                from sciforge.agents import QGenOutput

                qgen_result = QGenOutput.model_validate(record.output_payload)
                persisted_id = _persist_generated_question(
                    qgen_result,
                    storage=None,
                    storage_factory=self.storage_factory,
                    storage_tenant_id=str(record.metadata.get("storage_tenant_id", "") or ""),
                )
                if persisted_id and not _run_event_exists(
                    self.runs,
                    record.run_id,
                    "question.persisted",
                    {"question_id": persisted_id},
                ):
                    self.runs.append_event(
                        record.run_id,
                        "question.persisted",
                        {"question_id": persisted_id},
                    )
            content, artifact_kind, artifact_payload = _rebuild_product_delivery(record, self.runs)
            product_exists = self.conversations.has_assistant_result(
                link.conversation_id,
                link.tenant_id,
                record.run_id,
                artifact_kind=artifact_kind,
            )
            final_event_exists = _run_event_exists(self.runs, record.run_id, "assistant.final")
            if product_exists and final_event_exists:
                return
            _record_success(
                self.conversations,
                self.runs,
                conversation_id=link.conversation_id,
                tenant_id=link.tenant_id,
                run_id=record.run_id,
                content=content,
                artifact_kind=artifact_kind,
                artifact_payload=artifact_payload,
            )
            logger.info(
                "product_delivery_reconciled",
                run_id=record.run_id,
                product_existed=product_exists,
                final_event_existed=final_event_exists,
            )
        except Exception as exc:  # noqa: BLE001
            error = str(exc).strip()[:500] or type(exc).__name__
            payload = {"error": error}
            try:
                if not _run_event_exists(
                    self.runs,
                    record.run_id,
                    "product.reconciliation_failed",
                    payload,
                ):
                    self.runs.append_event(
                        record.run_id,
                        "product.reconciliation_failed",
                        payload,
                    )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "product_reconciliation_failure_event_failed",
                    run_id=record.run_id,
                )
            logger.exception("product_delivery_reconciliation_failed", run_id=record.run_id)

    async def shutdown(self) -> None:
        """Stop local workers; their RUNNING records are recovered next startup."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(
        self,
        run_id: str,
        task_fn: Callable[..., Awaitable[None]],
        task_kwargs: dict[str, Any],
    ) -> None:
        await task_fn(**task_kwargs)

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        run_id = task.get_name().removeprefix("sciforge-recover-")
        self._scheduled.discard(run_id)
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                "run_recovery_task_crashed",
                run_id=run_id,
                error=str(task.exception()),
            )


def install_conversation_routes(
    app: FastAPI,
    *,
    qgen_factory: Callable[[Discipline], Any],
    solve_factory: Callable[[Discipline], Any],
    conversation_store: ConversationStore | None,
    run_store: SQLiteRunStore | None,
    storage_factory: Callable[[], Any] | None,
    saas_store: SaasStore | None,
    authenticate_principal: Callable[..., Any],
    enforce_scope: Callable[[Any, str], Any],
    enforce_quota: Callable[[Any], Any],
    scope_agent_storage: ScopeAgentStorage,
    scopes: Mapping[str, str],
) -> None:
    """Register product routes without introducing a second task state machine."""

    def tenant_for(principal: Any) -> str:
        return str(getattr(principal, "tenant_id", "") or LOCAL_TENANT_ID)

    def require_services() -> tuple[ConversationStore, SQLiteRunStore]:
        if conversation_store is None or run_store is None:
            raise HTTPException(status_code=503, detail="会话长任务存储未启用")
        return conversation_store, run_store

    def require_conversation(
        store: ConversationStore, conversation_id: str, tenant_id: str
    ) -> ConversationRecord:
        record = store.get_conversation(conversation_id, tenant_id)
        if record is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return record

    def require_owned_run(
        conversations: ConversationStore,
        runs: SQLiteRunStore,
        run_id: str,
        tenant_id: str,
    ) -> Any:
        record = runs.get_run(run_id)
        link = conversations.get_run_link(run_id, tenant_id)
        if (
            record is None
            or link is None
            or record.metadata.get("tenant_id") != tenant_id
            or record.metadata.get("conversation_id") != link.conversation_id
        ):
            raise HTTPException(status_code=404, detail="任务不存在")
        return record

    recovery = (
        _RunRecoveryCoordinator(
            qgen_factory=qgen_factory,
            solve_factory=solve_factory,
            conversations=conversation_store,
            runs=run_store,
            storage_factory=storage_factory,
            scope_agent_storage=scope_agent_storage,
            saas_store=saas_store,
        )
        if conversation_store is not None and run_store is not None
        else None
    )
    if recovery is not None:
        app.router.add_event_handler("startup", recovery.startup)
        app.router.add_event_handler("shutdown", recovery.shutdown)
        app.state.run_recovery = recovery

    @app.post("/v1/conversations", response_model=ConversationOut)
    async def create_conversation(
        req: ConversationCreateRequest,
        principal: Any = Depends(authenticate_principal),
    ) -> ConversationOut:
        enforce_scope(principal, scopes["conversations_write"])
        conversations, _runs = require_services()
        discipline = _parse_discipline(req.discipline)
        record = conversations.create_conversation(
            tenant_for(principal),
            title=req.title,
            discipline=discipline.value,
        )
        return _conversation_out(record)

    @app.get("/v1/conversations", response_model=ConversationListResponse)
    async def list_conversations(
        limit: int = Query(default=50, ge=1, le=200),
        principal: Any = Depends(authenticate_principal),
    ) -> ConversationListResponse:
        enforce_scope(principal, scopes["conversations_read"])
        conversations, _runs = require_services()
        items = conversations.list_conversations(tenant_for(principal), limit=limit)
        return ConversationListResponse(items=[_conversation_out(item) for item in items])

    @app.get("/v1/conversations/{conversation_id}", response_model=ConversationOut)
    async def get_conversation(
        conversation_id: str,
        principal: Any = Depends(authenticate_principal),
    ) -> ConversationOut:
        enforce_scope(principal, scopes["conversations_read"])
        conversations, _runs = require_services()
        return _conversation_out(
            require_conversation(conversations, conversation_id, tenant_for(principal))
        )

    @app.get(
        "/v1/conversations/{conversation_id}/messages",
        response_model=ConversationMessagesResponse,
    )
    async def list_messages(
        conversation_id: str,
        limit: int = Query(default=500, ge=1, le=1000),
        principal: Any = Depends(authenticate_principal),
    ) -> ConversationMessagesResponse:
        enforce_scope(principal, scopes["conversations_read"])
        conversations, _runs = require_services()
        tenant_id = tenant_for(principal)
        require_conversation(conversations, conversation_id, tenant_id)
        messages = conversations.list_messages(conversation_id, tenant_id, limit=limit)
        return ConversationMessagesResponse(items=[_message_out(item) for item in messages])

    @app.post(
        "/v1/conversations/{conversation_id}/messages",
        response_model=ConversationMessageAccepted,
        status_code=202,
    )
    async def create_message(
        conversation_id: str,
        req: ConversationMessageRequest,
        background_tasks: BackgroundTasks,
        principal: Any = Depends(authenticate_principal),
    ) -> ConversationMessageAccepted:
        enforce_scope(principal, scopes["conversations_write"])
        required_scope = (
            scopes["solve_write"] if req.mode == "solve" else scopes["generation_write"]
        )
        enforce_scope(principal, required_scope)
        enforce_quota(principal)
        conversations, runs = require_services()
        tenant_id = tenant_for(principal)
        storage_tenant_id = str(getattr(principal, "tenant_id", "") or "")
        conversation = require_conversation(conversations, conversation_id, tenant_id)
        # Snapshot history before the current command is written.  The snapshot
        # becomes part of the durable QGen input, so retries and process recovery
        # cannot observe a different, later conversation state.
        conversation_context = _conversation_history_context(
            conversations,
            conversation_id,
            tenant_id,
        )

        source: Question | None = None
        if req.mode in _SOURCE_GENERATION_MODES or req.mode == "evaluate":
            if not req.source_question_id:
                raise HTTPException(
                    status_code=422,
                    detail=f"{req.mode} 模式必须提供 source_question_id",
                )
            source = _load_source_question(
                storage_factory, storage_tenant_id, req.source_question_id
            )
        elif req.mode == "solve" and req.source_question_id:
            source = _load_source_question(
                storage_factory, storage_tenant_id, req.source_question_id
            )

        run_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        budget = _run_budget(req)
        metadata = {
            "tenant_id": tenant_id,
            "storage_tenant_id": storage_tenant_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "mode": req.mode,
            "output_type": req.output_type,
            "output_format": req.output_format,
            "template_id": req.template_id,
            "verification_policy": req.verification_policy,
            "question_form": req.question_form,
            "knowledge_ids": list(req.knowledge_ids),
        }

        background_task: BackgroundTaskSpec
        if req.mode in _GENERATION_MODES:
            generation_request, mode_flags = _generation_request(
                req,
                conversation,
                source,
                conversation_context=conversation_context,
            )
            _api_verification_plan(
                req.verification_policy,
                generation_request.discipline,
                generation_request.question_type,
            )
            agent = qgen_factory(generation_request.discipline)
            if req.verification_policy in {"strict", "formal"}:
                if not _supports_configurable_verification(agent):
                    raise HTTPException(
                        status_code=503,
                        detail="strict/formal 验证策略需要真实 QGenAgent verifier",
                    )
            runs.create_run(
                "qgen",
                generation_request.model_dump(mode="json"),
                budget=budget,
                metadata=metadata,
                run_id=run_id,
            )
            background_task = (
                _run_qgen,
                {
                    "agent": agent,
                    "generation_request": generation_request,
                    "mode_flags": mode_flags,
                },
            )
        elif req.mode == "solve":
            question = source or _question_from_content(req)
            if question is None:
                raise HTTPException(
                    status_code=422,
                    detail="solve 模式需提供 source_question_id 或完整 content",
                )
            agent = solve_factory(question.discipline)
            runs.create_run(
                "solve",
                {"question": question.model_dump(mode="json")},
                budget=budget,
                metadata=metadata,
                run_id=run_id,
            )
            background_task = (_run_solve, {"agent": agent, "question": question})
        elif req.mode == "evaluate":
            if source is None:
                raise HTTPException(
                    status_code=422,
                    detail="evaluate 模式必须提供可访问的 source_question_id",
                )
            agent = qgen_factory(source.discipline)
            try:
                _validate_question_contract(
                    source.discipline,
                    source.question_type,
                    source.question_form,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            _api_verification_plan(
                req.verification_policy,
                source.discipline,
                source.question_type,
            )
            if not _supports_evaluation(agent):
                raise HTTPException(
                    status_code=503,
                    detail="evaluate 模式需要可审计的 retriever/solver/verifier",
                )
            if req.verification_policy in {"strict", "formal"}:
                if not _supports_configurable_verification(agent):
                    raise HTTPException(
                        status_code=503,
                        detail="strict/formal 评估策略需要真实 QGenAgent verifier",
                    )
            runs.create_run(
                "evaluate",
                {
                    "question": source.model_dump(mode="json"),
                    "evaluation_instruction": req.content,
                },
                budget=budget,
                metadata=metadata,
                run_id=run_id,
            )
            background_task = (
                _run_evaluate,
                {
                    "agent": agent,
                    "question": source,
                    "instruction": req.content,
                },
            )
        else:  # pragma: no cover - ChatMode validation owns this boundary
            raise HTTPException(status_code=422, detail=f"不支持的对话模式: {req.mode}")

        try:
            conversations.add_user_message_and_link_run(
                conversation_id,
                tenant_id,
                run_id,
                content=_user_message_content(req),
                mode=req.mode,
                metadata={
                    "source_question_id": req.source_question_id,
                    "discipline": req.discipline,
                    "subject": req.subject,
                    "topics": req.topics,
                    "difficulty": req.difficulty,
                    "question_type": req.question_type,
                    "question_form": req.question_form,
                    "knowledge_ids": list(req.knowledge_ids),
                    "output_type": req.output_type,
                    "template_id": req.template_id,
                    "verification_policy": req.verification_policy,
                },
                message_id=message_id,
            )
        except Exception as exc:
            runs.fail_run(run_id, f"failed to persist conversation link: {exc}")
            raise HTTPException(status_code=500, detail="无法创建会话任务") from exc

        task_fn: Callable[..., Awaitable[None]]
        task_fn, task_kwargs = background_task
        background_tasks.add_task(
            task_fn,
            **task_kwargs,
            run_id=run_id,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            storage_tenant_id=storage_tenant_id,
            output_type=req.output_type,
            verification_policy=req.verification_policy,
            conversations=conversations,
            runs=runs,
            storage_factory=storage_factory,
            scope_agent_storage=scope_agent_storage,
            saas_store=saas_store,
        )
        return ConversationMessageAccepted(
            conversation_id=conversation_id,
            message_id=message_id,
            run_id=run_id,
            status="pending",
            mode=req.mode,
            events_url=f"/v1/runs/{run_id}/events",
        )

    @app.get("/v1/runs/{run_id}", response_model=AgentRunOut)
    async def get_run(
        run_id: str,
        principal: Any = Depends(authenticate_principal),
    ) -> AgentRunOut:
        enforce_scope(principal, scopes["runs_read"])
        conversations, runs = require_services()
        record = require_owned_run(conversations, runs, run_id, tenant_for(principal))
        return _run_out(record, runs)

    @app.get("/v1/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        last_event_id: str = Header(default="", alias="Last-Event-ID"),
        principal: Any = Depends(authenticate_principal),
    ) -> StreamingResponse:
        enforce_scope(principal, scopes["runs_read"])
        conversations, runs = require_services()
        require_owned_run(conversations, runs, run_id, tenant_for(principal))
        after = _parse_last_event_id(last_event_id)
        return StreamingResponse(
            _stream_events(runs, run_id, after_sequence=after),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/v1/runs/{run_id}/cancel", response_model=RunCancelResponse)
    async def cancel_run(
        run_id: str,
        principal: Any = Depends(authenticate_principal),
    ) -> RunCancelResponse:
        enforce_scope(principal, scopes["runs_write"])
        conversations, runs = require_services()
        record = require_owned_run(conversations, runs, run_id, tenant_for(principal))
        if record.status not in TERMINAL_RUN_STATUSES:
            record = runs.request_cancel(run_id, reason="cancelled by API caller")
        return RunCancelResponse(
            id=record.run_id,
            status=record.status.value,
            cancel_requested=record.cancel_requested,
        )

    @app.post(
        "/v1/runs/{run_id}/resume",
        response_model=RunResumeResponse,
        status_code=202,
    )
    async def resume_run(
        run_id: str,
        req: RunResumeRequest | None = None,
        principal: Any = Depends(authenticate_principal),
    ) -> RunResumeResponse:
        enforce_scope(principal, scopes["runs_write"])
        enforce_quota(principal)
        conversations, runs = require_services()
        record = require_owned_run(conversations, runs, run_id, tenant_for(principal))
        if recovery is None:  # pragma: no cover - require_services guards this invariant
            raise HTTPException(status_code=503, detail="任务恢复服务未启用")

        # A retry arriving while the first resume request is already scheduled
        # is an idempotent read, not a second budget increment or worker.
        if recovery.is_scheduled(run_id):
            current = runs.require_run(run_id)
            return RunResumeResponse(
                id=current.run_id,
                status=current.status.value,
                resume_scheduled=True,
                budget=RunBudgetOut(**current.budget.to_dict()),
            )
        if record.status is not RunStatus.PAUSED:
            raise HTTPException(
                status_code=409,
                detail=f"只能恢复 paused 任务，当前状态为 {record.status.value}",
            )

        increment = (req or RunResumeRequest()).budget_increment
        new_budget = _increment_run_budget(record.budget, increment.model_dump())
        if new_budget != record.budget:
            record = runs.update_budget(run_id, new_budget)
        runs.append_event(
            run_id,
            "run.resume_requested",
            {"budget_increment": increment.model_dump(exclude_none=True)},
        )
        try:
            scheduled = recovery.schedule(record, reason="resume requested by API caller")
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=f"无法重建任务: {exc}") from exc
        current = runs.require_run(run_id)
        return RunResumeResponse(
            id=current.run_id,
            status=current.status.value,
            resume_scheduled=scheduled,
            budget=RunBudgetOut(**current.budget.to_dict()),
        )

    @app.get("/v1/templates", response_model=ActionTemplatesResponse)
    async def list_templates(
        principal: Any = Depends(authenticate_principal),
    ) -> ActionTemplatesResponse:
        enforce_scope(principal, scopes["templates_read"])
        return ActionTemplatesResponse(items=list(_ACTION_TEMPLATES))

    @app.get("/v1/knowledge/search", response_model=KnowledgeSearchResponse)
    async def search_knowledge(
        q: str = Query(default="", max_length=500),
        discipline: str = Query(default="math"),
        subject: str = Query(default=""),
        limit: int = Query(default=20, ge=1, le=100),
        principal: Any = Depends(authenticate_principal),
    ) -> KnowledgeSearchResponse:
        enforce_scope(principal, scopes["knowledge_read"])
        # Authentication is still required in hosted mode even though KPs are
        # shared corpus; this avoids accidentally publishing the whole bank.
        _ = tenant_for(principal)
        if storage_factory is None:
            raise HTTPException(status_code=503, detail="知识库检索未配置 storage")
        disc = _parse_discipline(discipline)
        storage = storage_factory()
        try:
            filters: dict[str, object] = {
                "discipline": disc.value,
                "quality_gate": QualityGateFilter(item_kind="knowledge"),
            }
            if subject:
                filters["subject"] = subject
            if q.strip():
                filters["text_query"] = q.strip()
            ids = storage.search_knowledge(filters)
            knowledge = storage.load_knowledge_by_ids(ids)
        finally:
            close = getattr(storage, "close", None)
            if callable(close):
                close()
        return KnowledgeSearchResponse(
            items=[
                KnowledgeSearchItem(
                    id=item.id,
                    discipline=item.discipline.value,
                    subject=item.subject,
                    item_type=item.kp_type,
                    number=item.number,
                    content=item.content,
                    difficulty=item.difficulty,
                    tags=item.tags,
                    source_file=item.source_file,
                    source_page=item.page,
                    start_line=item.start_line,
                    end_line=item.end_line,
                )
                for item in knowledge[:limit]
            ]
        )


def _parse_discipline(value: str) -> Discipline:
    try:
        return Discipline(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"未知学科: {value}") from exc


def _load_source_question(
    storage_factory: Callable[[], Any] | None,
    tenant_id: str,
    question_id: str,
) -> Question:
    if storage_factory is None:
        raise HTTPException(status_code=503, detail="该模式需要配置题库 storage")
    storage = storage_factory()
    scoped: TenantScopedStorageView | None = None
    try:
        try:
            if not tenant_id:
                return cast(Question, storage.load_question(question_id))
            scoped = TenantScopedStorageView(storage, tenant_id)
            return scoped.load_question(question_id)
        except TenantStorageConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="题目不存在") from exc
    finally:
        if scoped is not None:
            scoped.close()
        close = getattr(storage, "close", None)
        if callable(close):
            close()


def _question_from_content(req: ConversationMessageRequest) -> Question | None:
    content = req.content.strip()
    if not content:
        return None
    discipline = _parse_discipline(req.discipline or Discipline.MATH.value)
    try:
        question_type = req.question_type or _default_question_type(discipline)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        _validate_question_contract(discipline, question_type, req.question_form)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Question(
        discipline=discipline,
        subject=req.subject,
        question_type=question_type,
        question_form=req.question_form,
        difficulty=req.difficulty,
        content=content,
    )


def _generation_request(
    req: ConversationMessageRequest,
    conversation: ConversationRecord,
    source: Question | None,
    *,
    conversation_context: str = "",
) -> tuple[GenerationRequest, dict[str, bool]]:
    mode = req.mode
    if mode not in _GENERATION_MODES:
        raise ValueError(f"not a generation mode: {mode}")
    if source is None:
        discipline = _parse_discipline(req.discipline or conversation.discipline)
        topics = [topic.strip() for topic in req.topics if topic.strip()]
        if not topics and req.content.strip():
            topics = [req.content.strip()]
        if not topics:
            raise HTTPException(status_code=422, detail=f"{mode} 模式需提供 topics 或 content")
        strategy = VariantStrategy.TOPIC_GENERATION
        subject = req.subject
        try:
            question_type = req.question_type or _default_question_type(discipline)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        question_form = req.question_form
    else:
        discipline = source.discipline
        topics = [topic.strip() for topic in req.topics if topic.strip()]
        topics = topics or list(source.tags) or [source.subject or "general:通用"]
        subject = req.subject or source.subject
        question_type = req.question_type or source.question_type
        question_form = req.question_form or source.question_form
        if mode == "variant":
            strategy = VariantStrategy(req.variant_strategy)
        elif mode == "adjust_difficulty":
            if req.difficulty > source.difficulty:
                strategy = VariantStrategy.ESCALATE
            elif req.difficulty < source.difficulty:
                strategy = VariantStrategy.SIMPLIFY
            else:
                strategy = VariantStrategy.PARAMETER_SHIFT
        elif mode == "deepen":
            strategy = VariantStrategy.CROSS_TOPIC
        else:  # pragma: no cover - guarded by the generation-mode set above
            raise ValueError(f"not a generation mode: {mode}")

    try:
        _validate_question_contract(discipline, question_type, question_form)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    evolution = mode == "evolve"
    generation_request = GenerationRequest(
        discipline=discipline,
        subject=subject,
        topics=topics,
        difficulty_target=req.difficulty,
        question_type=question_type,
        question_form=question_form,
        knowledge_ids=list(req.knowledge_ids),
        conversation_context=conversation_context,
        strategy=strategy,
        source_question=source,
        enable_evolution=evolution,
    )
    return generation_request, {
        "enable_evolution": evolution,
        "population_selection_enabled": evolution,
        "deep_solve_enabled": mode == "deepen",
    }


def _run_budget(req: ConversationMessageRequest) -> RunBudget:
    return RunBudget(**req.budget.model_dump())


def _increment_run_budget(
    current: RunBudget,
    increment: Mapping[str, Any],
) -> RunBudget:
    def add_int(name: str, value: int | None) -> int | None:
        delta = increment.get(name)
        if delta is None or value is None:
            return value
        return value + int(delta)

    def add_float(name: str, value: float | None) -> float | None:
        delta = increment.get(name)
        if delta is None or value is None:
            return value
        return value + float(delta)

    return RunBudget(
        max_steps=add_int("max_steps", current.max_steps),
        max_llm_calls=add_int("max_llm_calls", current.max_llm_calls),
        max_tokens=add_int("max_tokens", current.max_tokens),
        max_wall_time_seconds=add_float("max_wall_time_seconds", current.max_wall_time_seconds),
    )


def _rebuild_background_task(
    record: RunRecord,
    *,
    qgen_factory: Callable[[Discipline], Any],
    solve_factory: Callable[[Discipline], Any],
    conversations: ConversationStore,
    runs: SQLiteRunStore,
    storage_factory: Callable[[], Any] | None,
    scope_agent_storage: ScopeAgentStorage,
    saas_store: SaasStore | None,
) -> BackgroundTaskSpec:
    """Reconstruct one worker exclusively from durable input and trusted ownership."""
    tenant_id = _metadata_text(record, "tenant_id")
    conversation_id = _metadata_text(record, "conversation_id")
    link = conversations.get_run_link(record.run_id, tenant_id)
    conversation = conversations.get_conversation(conversation_id, tenant_id)
    if link is None or link.conversation_id != conversation_id or conversation is None:
        raise ValueError("run ownership link is missing or inconsistent")

    # Never trust persisted storage_tenant_id as an authority boundary.  Derive
    # it from the authenticated product owner; open mode deliberately sees the
    # unscoped local bank while hosted tenants get their own scoped view.
    storage_tenant_id = "" if tenant_id == LOCAL_TENANT_ID else tenant_id
    persisted_storage_tenant = record.metadata.get("storage_tenant_id")
    if persisted_storage_tenant is not None and str(persisted_storage_tenant) != (
        storage_tenant_id
    ):
        raise ValueError("persisted storage tenant does not match run owner")

    output_type = str(record.metadata.get("output_type", "question_solution"))
    if output_type not in {
        "question_solution",
        "question_only",
        "structured_json",
        "lecture_note",
    }:
        output_type = "question_solution"
    raw_policy = record.metadata.get("verification_policy")
    if raw_policy is None:
        verification_policy = "fast" if record.run_kind == "solve" else "balanced"
    elif not isinstance(raw_policy, str) or raw_policy not in {
        "fast",
        "balanced",
        "strict",
        "formal",
    }:
        raise ValueError(f"persisted verification policy is invalid: {raw_policy!r}")
    else:
        verification_policy = raw_policy
    if record.run_kind == "solve":
        if verification_policy == "balanced":
            # Legacy omitted solve policy was persisted as the old schema default.
            verification_policy = "fast"
        elif verification_policy != "fast":
            raise ValueError("solve recovery cannot honor a non-fast verification policy")
    common: dict[str, Any] = {
        "run_id": record.run_id,
        "conversation_id": conversation_id,
        "tenant_id": tenant_id,
        "storage_tenant_id": storage_tenant_id,
        "output_type": output_type,
        "verification_policy": verification_policy,
        "conversations": conversations,
        "runs": runs,
        "storage_factory": storage_factory,
        "scope_agent_storage": scope_agent_storage,
        "saas_store": saas_store,
    }

    if record.run_kind == "qgen":
        generation_request = GenerationRequest.model_validate(record.input_payload)
        _validate_embedded_question_tenant(generation_request.source_question, storage_tenant_id)
        _validate_question_contract(
            generation_request.discipline,
            generation_request.question_type,
            generation_request.question_form,
        )
        _build_verification_plan(
            verification_policy,
            generation_request.discipline,
            generation_request.question_type,
        )
        agent = qgen_factory(generation_request.discipline)
        if verification_policy in {"strict", "formal"} and not (
            _supports_configurable_verification(agent)
        ):
            raise ValueError("strict/formal verification requires a configurable QGen verifier")
        mode = str(record.metadata.get("mode", "new"))
        return (
            _run_qgen,
            {
                "agent": agent,
                "generation_request": generation_request,
                "mode_flags": {
                    "enable_evolution": mode == "evolve",
                    "population_selection_enabled": mode == "evolve",
                    "deep_solve_enabled": mode == "deepen",
                },
                **common,
            },
        )
    if record.run_kind == "solve":
        question = Question.model_validate(record.input_payload.get("question"))
        _validate_embedded_question_tenant(question, storage_tenant_id)
        return (
            _run_solve,
            {
                "agent": solve_factory(question.discipline),
                "question": question,
                **common,
            },
        )
    if record.run_kind == "evaluate":
        question = Question.model_validate(record.input_payload.get("question"))
        _validate_embedded_question_tenant(question, storage_tenant_id)
        _validate_question_contract(
            question.discipline,
            question.question_type,
            question.question_form,
        )
        _build_verification_plan(
            verification_policy,
            question.discipline,
            question.question_type,
        )
        agent = qgen_factory(question.discipline)
        if not _supports_evaluation(agent):
            raise ValueError("evaluate recovery requires retriever, solver and verifier")
        if verification_policy in {"strict", "formal"} and not (
            _supports_configurable_verification(agent)
        ):
            raise ValueError("strict/formal verification requires a configurable QGen verifier")
        instruction = str(record.input_payload.get("evaluation_instruction", ""))
        return (
            _run_evaluate,
            {
                "agent": agent,
                "question": question,
                "instruction": instruction,
                **common,
            },
        )
    raise ValueError(f"unsupported recoverable run kind: {record.run_kind}")


def _materialized_run_output(
    record: RunRecord,
    runs: SQLiteRunStore,
) -> dict[str, Any] | None:
    """Project canonical question IDs without rewriting immutable generation output."""
    output = record.output_payload
    if record.run_kind != "qgen" or output is None:
        return output

    persisted_id: str | None = None
    after = 0
    while True:
        page = runs.list_events(record.run_id, after_sequence=after, limit=500)
        for event in page:
            if event.event_type == "question.persisted":
                persisted_id = str(event.payload["question_id"])
        if len(page) < 500:
            break
        after = page[-1].sequence
    if persisted_id is None:
        return output

    from sciforge.agents import QGenOutput

    result = QGenOutput.model_validate(output)
    generated = result.generated
    if generated is None:
        raise ValueError("question.persisted event requires a generated question")
    solution_text = (generated.solution.solution_latex or generated.solution.solution_plain).strip()
    updates: dict[str, object] = {"id": persisted_id, "solution_content": solution_text or None}
    storage_tenant_id = str(record.metadata.get("storage_tenant_id", "") or "")
    if storage_tenant_id:
        updates["tenant_id"] = storage_tenant_id
    generated.question = generated.question.model_copy(update=updates)
    generated.solution = generated.solution.model_copy(update={"question_id": persisted_id})
    return result.model_dump(mode="json")


def _rebuild_product_delivery(
    record: RunRecord,
    runs: SQLiteRunStore,
) -> tuple[str, str, Mapping[str, Any]]:
    """Render a successful immutable run output without invoking an Agent."""
    from sciforge.agents import QGenOutput, SolveOutput

    output = _materialized_run_output(record, runs)
    if output is None:
        raise ValueError("successful conversation run has no output payload")
    output_type = str(record.metadata.get("output_type", "question_solution"))
    if output_type not in {
        "question_solution",
        "question_only",
        "structured_json",
        "lecture_note",
    }:
        output_type = "question_solution"

    if record.run_kind == "qgen":
        qgen_result = QGenOutput.model_validate(output)
        return (
            _render_qgen_result(qgen_result, output_type=output_type),
            "generated_question",
            output,
        )
    if record.run_kind == "solve":
        question = Question.model_validate(record.input_payload.get("question"))
        solve_result = SolveOutput.model_validate(output)
        solution_text = ""
        if solve_result.solution is not None:
            solution_text = (
                solve_result.solution.solution_latex or solve_result.solution.solution_plain
            )
        artifact_payload = {"question": question.model_dump(mode="json"), **output}
        return (
            _render_solution_result(
                question,
                solution_text,
                solve_result.confidence,
                artifact_payload,
                output_type=output_type,
            ),
            "solution",
            artifact_payload,
        )
    if record.run_kind == "evaluate":
        question = Question.model_validate(output.get("question"))
        solution = Solution.model_validate(output.get("solution"))
        verification = VerificationResult.model_validate(output.get("verification"))
        return (
            _render_evaluation(
                question,
                solution,
                verification,
                output,
                output_type=output_type,
            ),
            "evaluation",
            output,
        )
    raise ValueError(f"unsupported product delivery kind: {record.run_kind}")


def _metadata_text(record: RunRecord, key: str) -> str:
    value = record.metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"run metadata missing {key}")
    return value


def _validate_embedded_question_tenant(
    question: Question | None,
    storage_tenant_id: str,
) -> None:
    if (
        question is not None
        and storage_tenant_id
        and question.tenant_id
        and question.tenant_id != storage_tenant_id
    ):
        raise ValueError("run input contains a question owned by another tenant")


def _next_step_attempt(runs: SQLiteRunStore, run_id: str, name: str) -> int:
    return (
        max(
            (step.attempt for step in runs.list_steps(run_id) if step.name == name),
            default=0,
        )
        + 1
    )


def _user_message_content(req: ConversationMessageRequest) -> str:
    if req.content.strip():
        return req.content.strip()
    if req.source_question_id:
        return f"{req.mode}: {req.source_question_id}"
    if req.topics:
        return f"{req.mode}: {', '.join(req.topics)}"
    return req.mode


def _conversation_history_context(
    conversations: ConversationStore,
    conversation_id: str,
    tenant_id: str,
) -> str:
    """Build a bounded, role-labelled snapshot of the newest completed turns."""

    messages = conversations.list_recent_messages(
        conversation_id,
        tenant_id,
        limit=_CONVERSATION_CONTEXT_MESSAGES,
    )
    newest_first: list[str] = []
    remaining = _CONVERSATION_CONTEXT_CHARS
    for message in reversed(messages):
        if message.role not in {"user", "assistant"}:
            continue
        content = message.content.strip()
        if not content:
            continue
        content = content[:_CONVERSATION_CONTEXT_PER_MESSAGE_CHARS]
        mode = f"/{message.mode}" if message.mode else ""
        fragment = f"[{message.role}{mode}]\n{content}"
        if len(fragment) > remaining:
            fragment = fragment[:remaining]
        if not fragment:
            break
        newest_first.append(fragment)
        remaining -= len(fragment) + 2
        if remaining <= 0:
            break
    return "\n\n".join(reversed(newest_first))


def _supports_evaluation(agent: Any) -> bool:
    """Require the three real evaluation capabilities, not a pre-baked verdict."""

    return all(
        callable(getattr(getattr(agent, component, None), method, None))
        for component, method in (
            ("retriever", "build_context"),
            ("solver", "solve"),
            ("verifier", "verify"),
        )
    )


def _supports_configurable_verification(agent: Any) -> bool:
    from sciforge.agents import QGenAgent
    from sciforge.agents.qgen.verifier import VerificationPipeline

    return isinstance(agent, QGenAgent) and isinstance(
        getattr(agent, "verifier", None), VerificationPipeline
    )


@contextmanager
def _temporary_qgen_flags(agent: QGenAgent, flags: Mapping[str, bool]) -> Iterator[None]:
    original = {name: bool(getattr(agent, name)) for name in flags}
    try:
        for name, enabled in flags.items():
            setattr(agent, name, enabled)
        yield
    finally:
        for name, enabled in original.items():
            setattr(agent, name, enabled)


@contextmanager
def _temporary_verification_policy(
    agent: Any,
    *,
    policy: str,
    discipline: Discipline,
    question_type: str,
) -> Iterator[None]:
    """Apply one request's evidence policy without mutating a shared agent permanently."""

    plan = _build_verification_plan(policy, discipline, question_type)
    if not _supports_configurable_verification(agent):
        yield
        return

    verifier: Any = agent.verifier
    names = (
        "enable_solution_relevance",
        "enable_numerical_verification",
        "require_numerical_verification",
        "enable_lean_verification",
        "require_lean_verification",
        "_relevance_template",
        "_numerical_template",
        "_lean_template",
    )
    original = {name: getattr(verifier, name) for name in names}
    try:
        verifier.enable_solution_relevance = plan.solution_relevance
        verifier.enable_numerical_verification = plan.numerical_enabled
        verifier.require_numerical_verification = plan.numerical_required
        verifier.enable_lean_verification = plan.lean_enabled
        verifier.require_lean_verification = plan.lean_required

        if verifier.enable_solution_relevance and not verifier._relevance_template:
            from sciforge.prompts.templates import get_generation_prompt_template

            verifier._relevance_template = get_generation_prompt_template(
                "verification/solution_relevance.txt", discipline
            )

        if verifier.enable_numerical_verification and not verifier._numerical_template:
            from sciforge.prompts.templates import get_generation_prompt_template

            verifier._numerical_template = get_generation_prompt_template(
                "verification/generate_numerical_test.txt", discipline
            )
        if verifier.enable_lean_verification and not verifier._lean_template:
            from sciforge.prompts.templates import get_generation_prompt_template

            verifier._lean_template = get_generation_prompt_template(
                "verification/generate_lean_test.txt", discipline
            )
        yield
    finally:
        for name, value in original.items():
            setattr(verifier, name, value)


def _agent_usage(agent: Any) -> tuple[int, int]:
    """Read monotonic LLM counters from the clients actually owned by a runner."""

    candidates = [
        getattr(agent, "llm", None),
        getattr(getattr(agent, "solver", None), "llm", None),
        getattr(getattr(agent, "engine", None), "llm", None),
        getattr(getattr(agent, "verifier", None), "review_llm", None),
    ]
    seen: set[int] = set()
    calls = 0
    tokens = 0
    for client in candidates:
        if client is None or id(client) in seen:
            continue
        seen.add(id(client))
        client_calls = getattr(client, "call_count", None)
        client_tokens = getattr(client, "total_tokens", None)
        if isinstance(client_calls, int):
            calls += client_calls
        if isinstance(client_tokens, int):
            tokens += client_tokens
    return calls, tokens


def _record_measured_usage(
    runs: SQLiteRunStore,
    run_id: str,
    agent: Any,
    before: tuple[int, int],
    started_at: float,
) -> None:
    calls, tokens = _agent_usage(agent)
    runs.record_usage(
        run_id,
        llm_calls=max(0, calls - before[0]),
        tokens=max(0, tokens - before[1]),
        wall_time_seconds=max(0.0, time.perf_counter() - started_at),
    )


async def _run_qgen(
    *,
    agent: Any,
    generation_request: GenerationRequest,
    mode_flags: Mapping[str, bool],
    run_id: str,
    conversation_id: str,
    tenant_id: str,
    storage_tenant_id: str,
    output_type: str,
    verification_policy: str,
    conversations: ConversationStore,
    runs: SQLiteRunStore,
    storage_factory: Callable[[], Any] | None,
    scope_agent_storage: ScopeAgentStorage,
    saas_store: SaasStore | None,
) -> None:
    from sciforge.agents import DurableQGenHarness, QGenAgent, QGenInput, QGenOutput

    try:
        persisted_id: str | None = None
        with scope_agent_storage(agent, storage_tenant_id, storage_factory):
            if isinstance(agent, QGenAgent):
                with (
                    _temporary_qgen_flags(agent, mode_flags),
                    _temporary_verification_policy(
                        agent,
                        policy=verification_policy,
                        discipline=generation_request.discipline,
                        question_type=generation_request.question_type,
                    ),
                ):
                    _identifier, result = await DurableQGenHarness(agent, runs).run(
                        QGenInput(request=generation_request),
                        run_id=run_id,
                        resume=True,
                    )
            else:
                runs.begin_run(run_id)
                step = runs.begin_step(
                    run_id,
                    "qgen_facade",
                    attempt=_next_step_attempt(runs, run_id, "qgen_facade"),
                )
                usage_before = _agent_usage(agent)
                started_at = time.perf_counter()
                raw = await agent.run(QGenInput(request=generation_request))
                result = raw if isinstance(raw, QGenOutput) else QGenOutput.model_validate(raw)
                _record_measured_usage(runs, run_id, agent, usage_before, started_at)
                runs.complete_step(step.step_id, result.model_dump(mode="json"))
                runs.complete_run(run_id, result.model_dump(mode="json"))

            persisted_id = _persist_generated_question(
                result,
                storage=getattr(agent, "storage", None),
                storage_factory=storage_factory,
                storage_tenant_id=storage_tenant_id,
            )

        if persisted_id:
            runs.append_event(
                run_id,
                "question.persisted",
                {"question_id": persisted_id},
            )

        assistant, artifact_kind, output = _rebuild_product_delivery(runs.require_run(run_id), runs)
        _record_success(
            conversations,
            runs,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            run_id=run_id,
            content=assistant,
            artifact_kind=artifact_kind,
            artifact_payload=output,
        )
        _record_usage_safely(saas_store, runs, run_id, tenant_id, "/v1/conversations/messages")
    except Exception as exc:  # noqa: BLE001
        _record_failure(conversations, runs, conversation_id, tenant_id, run_id, exc)


def _persist_generated_question(
    result: Any,
    *,
    storage: Any | None,
    storage_factory: Callable[[], Any] | None,
    storage_tenant_id: str,
) -> str | None:
    """Materialize a verified QGen result as a reusable source question.

    Runtime output keeps ``Question`` and ``Solution`` separate for auditability.
    The question bank stores the learner-facing solution on ``solution_content``
    so every later mode can reconstruct the same source by ID.  A missing storage
    is a supported artifact-only deployment; a configured-but-failing storage is
    allowed to fail so startup reconciliation can retry the product post-process.
    """

    generated = getattr(result, "generated", None)
    if generated is None or not bool(generated.verification.passed):
        return None
    solution_text = (generated.solution.solution_latex or generated.solution.solution_plain).strip()
    updates: dict[str, object] = {"solution_content": solution_text or None}
    if storage_tenant_id:
        updates["tenant_id"] = storage_tenant_id
    question = generated.question.model_copy(update=updates)
    target = storage
    owned_base: Any | None = None
    scoped: TenantScopedStorageView | None = None
    if target is None and storage_factory is not None:
        owned_base = storage_factory()
        if storage_tenant_id:
            scoped = TenantScopedStorageView(owned_base, storage_tenant_id)
            target = scoped
        else:
            target = owned_base
    if target is None:
        return None
    try:
        persisted_id: str = target.save_question(question)
    finally:
        if scoped is not None:
            scoped.close()
        if owned_base is not None:
            close = getattr(owned_base, "close", None)
            if callable(close):
                close()
    return persisted_id


async def _run_solve(
    *,
    agent: Any,
    question: Question,
    run_id: str,
    conversation_id: str,
    tenant_id: str,
    storage_tenant_id: str,
    output_type: str,
    verification_policy: str,
    conversations: ConversationStore,
    runs: SQLiteRunStore,
    storage_factory: Callable[[], Any] | None,
    scope_agent_storage: ScopeAgentStorage,
    saas_store: SaasStore | None,
) -> None:
    from sciforge.agents import SolveInput, SolveOutput

    try:
        runs.begin_run(run_id)
        with scope_agent_storage(agent, storage_tenant_id, storage_factory):
            step = runs.begin_step(
                run_id,
                "solve",
                attempt=_next_step_attempt(runs, run_id, "solve"),
                input_payload={"question_id": question.id},
            )
            usage_before = _agent_usage(agent)
            started_at = time.perf_counter()
            raw = await agent.run(SolveInput(question=question))
            result = raw if isinstance(raw, SolveOutput) else SolveOutput.model_validate(raw)
            output = result.model_dump(mode="json")
            _record_measured_usage(runs, run_id, agent, usage_before, started_at)
            runs.complete_step(step.step_id, output)
        runs.append_event(
            run_id,
            "solution.ready",
            {"question_id": question.id, "confidence": result.confidence},
        )
        runs.complete_run(run_id, output)
        solution_text = (
            result.solution.solution_latex or result.solution.solution_plain
            if result.solution is not None
            else ""
        )
        artifact_payload = {"question": question.model_dump(mode="json"), **output}
        assistant = _render_solution_result(
            question,
            solution_text,
            result.confidence,
            artifact_payload,
            output_type=output_type,
        )
        _record_success(
            conversations,
            runs,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            run_id=run_id,
            content=assistant,
            artifact_kind="solution",
            artifact_payload=artifact_payload,
        )
        _record_usage_safely(saas_store, runs, run_id, tenant_id, "/v1/conversations/solve")
    except Exception as exc:  # noqa: BLE001
        _record_failure(conversations, runs, conversation_id, tenant_id, run_id, exc)


async def _run_evaluate(
    *,
    agent: Any,
    question: Question,
    instruction: str,
    run_id: str,
    conversation_id: str,
    tenant_id: str,
    storage_tenant_id: str,
    output_type: str,
    verification_policy: str,
    conversations: ConversationStore,
    runs: SQLiteRunStore,
    storage_factory: Callable[[], Any] | None,
    scope_agent_storage: ScopeAgentStorage,
    saas_store: SaasStore | None,
) -> None:
    try:
        runs.begin_run(run_id)
        with (
            scope_agent_storage(agent, storage_tenant_id, storage_factory),
            (
                _temporary_verification_policy(
                    agent,
                    policy=verification_policy,
                    discipline=question.discipline,
                    question_type=question.question_type,
                )
            ),
        ):
            context_request = GenerationRequest(
                discipline=question.discipline,
                subject=question.subject,
                topics=list(question.tags) or [question.subject or "general:通用"],
                difficulty_target=question.difficulty,
                question_type=question.question_type,
                question_form=question.question_form,
                strategy=VariantStrategy.PARAMETER_SHIFT,
                source_question=question,
            )
            prepare = runs.begin_step(
                run_id,
                "prepare_solution",
                attempt=_next_step_attempt(runs, run_id, "prepare_solution"),
                input_payload={"question_id": question.id},
            )
            usage_before = _agent_usage(agent)
            started_at = time.perf_counter()
            context: ContextBundle = agent.retriever.build_context(
                context_request, question.discipline
            )
            solution: Solution | None
            if question.solution_content and question.solution_content.strip():
                solution = Solution(
                    question_id=question.id,
                    solution_latex=question.solution_content,
                    solution_plain=question.solution_content,
                    confidence="high",
                    is_verified=False,
                )
                solution_source = "knowledge_base"
            else:
                solution = await agent.solver.solve(question, context)
                solution_source = "independent_solver"
            if solution is None or not (solution.solution_latex or solution.solution_plain).strip():
                raise RuntimeError("无法准备可评估的题目解答")
            _record_measured_usage(runs, run_id, agent, usage_before, started_at)
            runs.complete_step(
                prepare.step_id,
                {"solution_source": solution_source, "confidence": solution.confidence},
            )
            runs.append_event(
                run_id,
                "evaluation.solution_ready",
                {"source": solution_source, "confidence": solution.confidence},
            )

            verify_step = runs.begin_step(
                run_id,
                "verify",
                attempt=_next_step_attempt(runs, run_id, "verify"),
                input_payload={"instruction": instruction},
            )
            usage_before = _agent_usage(agent)
            started_at = time.perf_counter()
            verification = await agent.verifier.verify(
                question,
                solution,
                context,
                review_instruction=instruction,
            )
            _record_measured_usage(runs, run_id, agent, usage_before, started_at)
            runs.complete_step(
                verify_step.step_id,
                {
                    "passed": verification.passed,
                    "checks": verification.checks,
                    "evidence_count": len(verification.evidence),
                },
            )
            runs.append_event(
                run_id,
                "evaluation.verified",
                {
                    "passed": verification.passed,
                    "checks": verification.checks,
                    "evidence": [item.model_dump(mode="json") for item in verification.evidence],
                },
            )

        output = {
            "question": question.model_dump(mode="json"),
            "solution": solution.model_dump(mode="json"),
            "difficulty": question.difficulty,
            "verification": verification.model_dump(mode="json"),
            "evaluation_instruction": instruction,
        }
        runs.complete_run(run_id, output)
        assistant = _render_evaluation(
            question,
            solution,
            verification,
            output,
            output_type=output_type,
        )
        _record_success(
            conversations,
            runs,
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            run_id=run_id,
            content=assistant,
            artifact_kind="evaluation",
            artifact_payload=output,
        )
        _record_usage_safely(saas_store, runs, run_id, tenant_id, "/v1/conversations/evaluate")
    except Exception as exc:  # noqa: BLE001
        _record_failure(conversations, runs, conversation_id, tenant_id, run_id, exc)


def _record_success(
    conversations: ConversationStore,
    runs: SQLiteRunStore,
    *,
    conversation_id: str,
    tenant_id: str,
    run_id: str,
    content: str,
    artifact_kind: str,
    artifact_payload: Mapping[str, Any],
) -> None:
    message, artifact = conversations.record_assistant_result(
        conversation_id,
        tenant_id,
        run_id=run_id,
        content=content,
        artifact_kind=artifact_kind,
        artifact_payload=artifact_payload,
        artifact_name=artifact_kind,
    )
    event_payload = {
        "message_id": message.message_id,
        "artifact_id": artifact.artifact_id,
        "artifact_kind": artifact.kind,
    }
    if not _run_event_exists(runs, run_id, "assistant.final", event_payload):
        runs.append_event(run_id, "assistant.final", event_payload)


def _record_failure(
    conversations: ConversationStore,
    runs: SQLiteRunStore,
    conversation_id: str,
    tenant_id: str,
    run_id: str,
    exc: Exception,
) -> None:
    error = str(exc).strip()[:500] or type(exc).__name__
    record = runs.get_run(run_id)
    if record is not None and record.status not in TERMINAL_RUN_STATUSES:
        if record.status is RunStatus.PAUSED:
            # Budget exhaustion is a resumable state owned by the durable
            # harness.  Never collapse it into FAILED in the product adapter.
            pass
        elif record.cancel_requested or isinstance(exc, RunCancellationRequestedError):
            try:
                record = runs.acknowledge_cancel(run_id, reason=error)
            except Exception:  # noqa: BLE001
                logger.exception("conversation_cancel_ack_failed", run_id=run_id)
        else:
            if record.current_step_id:
                try:
                    runs.fail_step(record.current_step_id, error)
                except Exception:  # noqa: BLE001
                    logger.exception("conversation_step_fail_record_failed", run_id=run_id)
            latest = runs.get_run(run_id)
            if latest is not None and latest.status not in TERMINAL_RUN_STATUSES:
                try:
                    runs.fail_run(run_id, error)
                except Exception:  # noqa: BLE001
                    logger.exception("conversation_run_fail_record_failed", run_id=run_id)

    final = runs.get_run(run_id)
    if final is not None and final.status is RunStatus.SUCCEEDED:
        # Runtime and product data live in separate SQLite databases.  If the
        # product-side delivery (or subsequent billing) fails after runtime
        # success, never contradict the immutable successful run with an
        # assistant.error message.  Persist a repairable operational event.
        delivered = _run_event_exists(runs, run_id, "assistant.final")
        event_type = "product.postprocess_failed" if delivered else "product.delivery_failed"
        try:
            runs.append_event(run_id, event_type, {"error": error})
        except Exception:  # noqa: BLE001
            logger.exception("conversation_delivery_failure_event_failed", run_id=run_id)
        logger.error(event_type.replace(".", "_"), run_id=run_id, error=error)
        return
    cancelled = final is not None and final.status is RunStatus.CANCELLED
    paused = final is not None and final.status is RunStatus.PAUSED
    prefix = (
        "任务已暂停，可调整预算后恢复" if paused else "任务已取消" if cancelled else "任务执行失败"
    )
    try:
        message = conversations.add_message(
            conversation_id,
            tenant_id,
            role="assistant",
            content=f"{prefix}：{error}",
            run_id=run_id,
            metadata={"error": error, "cancelled": cancelled, "paused": paused},
        )
        runs.append_event(
            run_id,
            "assistant.paused"
            if paused
            else "assistant.cancelled"
            if cancelled
            else "assistant.error",
            {"message_id": message.message_id, "error": error},
        )
    except Exception:  # noqa: BLE001
        logger.exception("conversation_error_message_failed", run_id=run_id)
    logger.warning("conversation_background_failed", run_id=run_id, error=error)


def _run_event_exists(
    runs: SQLiteRunStore,
    run_id: str,
    event_type: str,
    payload: Mapping[str, Any] | None = None,
) -> bool:
    """Search an append-only stream without assuming it fits one page."""

    after = 0
    while True:
        page = runs.list_events(run_id, after_sequence=after, limit=500)
        for event in page:
            if event.event_type == event_type and (
                payload is None or event.payload == dict(payload)
            ):
                return True
        if len(page) < 500:
            return False
        after = page[-1].sequence


def _record_usage(
    saas_store: SaasStore | None,
    runs: SQLiteRunStore,
    run_id: str,
    tenant_id: str,
    endpoint: str,
) -> None:
    if saas_store is None or tenant_id == LOCAL_TENANT_ID:
        return
    record = runs.require_run(run_id)
    saas_store.record_usage(
        tenant_id,
        endpoint,
        billable_units=1,
        tokens_est=record.usage.tokens,
    )


def _record_usage_safely(
    saas_store: SaasStore | None,
    runs: SQLiteRunStore,
    run_id: str,
    tenant_id: str,
    endpoint: str,
) -> None:
    """Keep post-success billing faults out of the execution state machine."""

    try:
        _record_usage(saas_store, runs, run_id, tenant_id, endpoint)
    except Exception as exc:  # noqa: BLE001
        error = str(exc).strip()[:500] or type(exc).__name__
        payload = {"endpoint": endpoint, "error": error}
        try:
            if not _run_event_exists(runs, run_id, "billing.record_failed", payload):
                runs.append_event(run_id, "billing.record_failed", payload)
        except Exception:  # noqa: BLE001
            logger.exception("conversation_billing_failure_event_failed", run_id=run_id)
        logger.exception(
            "conversation_billing_record_failed",
            run_id=run_id,
            endpoint=endpoint,
            error=error,
        )


def _render_qgen_result(result: Any, *, output_type: str) -> str:
    payload = result.model_dump(mode="json")
    if result.generated is None:
        markdown = f"本次未生成通过验证的题目。\n\n原因：{result.reason or '未提供'}"
        return _render_output(
            markdown,
            payload,
            output_type=output_type,
            question_content="",
            learning_goal="分析生成失败原因并调整约束。",
        )
    generated = result.generated
    status = "通过" if generated.verification.passed else "未通过"
    solution_text = generated.solution.solution_latex or generated.solution.solution_plain
    markdown = (
        f"## 题目\n\n{generated.question.content}\n\n"
        f"## 解答\n\n{solution_text}\n\n"
        f"难度：{generated.difficulty_score.overall:.1f}/10 · 验证：{status}"
    )
    return _render_output(
        markdown,
        payload,
        output_type=output_type,
        question_content=generated.question.content,
        learning_goal=f"掌握 {generated.question.subject or '目标主题'} 的建模与推理方法。",
    )


def _render_solution_result(
    question: Question,
    solution_text: str,
    confidence: str,
    payload: Mapping[str, Any],
    *,
    output_type: str,
) -> str:
    markdown = (
        f"## 解答\n\n{solution_text}\n\n置信度：{confidence}"
        if solution_text
        else "求解器未能产出可用解答。"
    )
    return _render_output(
        markdown,
        payload,
        output_type=output_type,
        question_content=question.content,
        learning_goal=f"理解 {question.subject or '该题'} 的完整求解路径。",
    )


def _render_evaluation(
    question: Question,
    solution: Solution,
    verification: Any,
    payload: Mapping[str, Any],
    *,
    output_type: str,
) -> str:
    verdict = "通过" if verification.passed else "未通过"
    evidence = "\n".join(
        f"- {item.kind}: {item.status.value} — {item.summary}" for item in verification.evidence
    )
    suggestions = verification.suggestions or "无额外修正建议。"
    markdown = (
        f"## 评估结果：{verdict}\n\n"
        f"题目难度：{question.difficulty}/10\n\n"
        f"### 解答\n\n{solution.solution_latex or solution.solution_plain}\n\n"
        f"### 验证证据\n\n{evidence or '- 无证据记录'}\n\n"
        f"### 建议\n\n{suggestions}"
    )
    return _render_output(
        markdown,
        payload,
        output_type=output_type,
        question_content=question.content,
        learning_goal="理解题目、解答与验证证据之间的对应关系。",
    )


def _render_output(
    markdown: str,
    payload: Mapping[str, Any],
    *,
    output_type: str,
    question_content: str,
    learning_goal: str,
) -> str:
    if output_type == "structured_json":
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if output_type == "question_only":
        return question_content or markdown
    if output_type == "lecture_note":
        return (
            f"# 学习讲义\n\n## 学习目标\n\n{learning_goal}\n\n"
            f"{markdown}\n\n## 自检提示\n\n"
            "请先独立复述关键条件，再逐项核对结构化验证证据。"
        )
    return markdown


def _conversation_out(record: ConversationRecord) -> ConversationOut:
    return ConversationOut(
        id=record.conversation_id,
        title=record.title,
        discipline=record.discipline,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _message_out(record: MessageRecord) -> ConversationMessageOut:
    return ConversationMessageOut(
        id=record.message_id,
        conversation_id=record.conversation_id,
        role=record.role,  # type: ignore[arg-type]
        content=record.content,
        mode=record.mode,
        run_id=record.run_id,
        metadata=record.metadata,
        created_at=record.created_at,
    )


def _run_out(record: RunRecord, runs: SQLiteRunStore) -> AgentRunOut:
    return AgentRunOut(
        id=record.run_id,
        kind=record.run_kind,
        status=record.status.value,
        conversation_id=str(record.metadata.get("conversation_id", "")),
        mode=str(record.metadata.get("mode", "")),
        usage=RunUsageOut(**record.usage.to_dict()),
        budget=RunBudgetOut(**record.budget.to_dict()),
        cancel_requested=record.cancel_requested,
        output=_materialized_run_output(record, runs),
        error=record.last_error,
        created_at=record.created_at,
        updated_at=record.updated_at,
        completed_at=record.completed_at,
    )


def _parse_last_event_id(value: str) -> int:
    if not value:
        return 0
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID 必须是非负整数") from exc
    if parsed < 0:
        raise HTTPException(status_code=400, detail="Last-Event-ID 必须是非负整数")
    return parsed


async def _stream_events(
    runs: SQLiteRunStore,
    run_id: str,
    *,
    after_sequence: int,
) -> Any:
    cursor = after_sequence
    assistant_terminal_seen = False
    terminal_deadline: float | None = None
    last_emit_at = time.monotonic()
    while True:
        events = runs.list_events(run_id, after_sequence=cursor, limit=200)
        for event in events:
            cursor = event.sequence
            assistant_terminal_seen = (
                assistant_terminal_seen or event.event_type in _ASSISTANT_TERMINAL_EVENTS
            )
            data = json.dumps(
                {
                    "id": event.sequence,
                    "type": event.event_type,
                    "payload": event.payload,
                    "created_at": event.created_at,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"
            last_emit_at = time.monotonic()

        record = runs.require_run(run_id)
        if record.status in TERMINAL_RUN_STATUSES or record.status is RunStatus.PAUSED:
            if assistant_terminal_seen:
                break
            if terminal_deadline is None:
                terminal_deadline = time.monotonic() + _SSE_TERMINAL_GRACE_SECONDS
            if time.monotonic() >= terminal_deadline:
                break
            await asyncio.sleep(0.02)
            continue
        now = time.monotonic()
        if now - last_emit_at >= _SSE_HEARTBEAT_SECONDS:
            yield ": heartbeat\n\n"
            last_emit_at = now
        await asyncio.sleep(0.2)


__all__ = ["LOCAL_TENANT_ID", "install_conversation_routes"]
