"""SciForge FastAPI 网关（P2）

    app = create_app(qgen_factory, solve_factory, saas_store=...)

- saas_store=None → 开放模式（无鉴权/计费，便于本地与测试）
- 提供 saas_store → 启用 X-API-Key 鉴权 + 配额检查 + 用量计费（多租户）

端点：
- GET  /v1/health    健康检查
- POST /v1/generate  单题生成（调 QGenAgent；成功后按租户计费）
- POST /v1/solve     求解（调 SolveAgent；支持 question_id 或 content）
- GET  /v1/usage     当前租户用量与配额

注：JWT 用户登录 / 异步批量 / 组卷导出 / PG 后端为 P2 后续；当前 token 计费用 GenerationTrace 估算。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from sciforge.api.schemas import (
    AuthRequest,
    AuthResponse,
    BehaviorRequest,
    GenerateBatchRequest,
    GeneratedQuestionOut,
    GenerateRequest,
    GenerateResponse,
    PaperExportRequest,
    PaperItem,
    PaperRequest,
    PaperResponse,
    RecommendItem,
    RecommendResponse,
    SolveRequest,
    SolveResponse,
    TaskResponse,
)
from sciforge.api.security import sign_token, verify_token
from sciforge.api.tenant_storage import (
    TenantScopedStorageView,
    TenantStorageConfigurationError,
)
from sciforge.core.models import (
    Discipline,
    GenerationRequest,
    Question,
    VariantStrategy,
)
from sciforge.export import render_docx, render_markdown
from sciforge.storage.query import QualityGateFilter
from sciforge.storage.saas import SaasStore, UsageSummary

if TYPE_CHECKING:
    from sciforge.agents import QGenAgent, SolveAgent
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

logger = structlog.get_logger()

QGenFactory = Callable[[Discipline], "QGenAgent"]
SolveFactory = Callable[[Discipline], "SolveAgent"]
StorageFactory = Callable[[], "SQLiteStorageBackend"]

SCOPE_GENERATION_WRITE = "generation:write"
SCOPE_SOLVE_WRITE = "solve:write"
SCOPE_TASKS_READ = "tasks:read"
SCOPE_USAGE_READ = "usage:read"
SCOPE_PAPERS_WRITE = "papers:write"

_LEGACY_SCOPE_ALIASES: dict[str, frozenset[str]] = {
    # Existing keys were issued with only generate/solve.  Expand those values
    # to the least-privilege capabilities needed by their existing workflows.
    "generate": frozenset(
        {SCOPE_GENERATION_WRITE, SCOPE_TASKS_READ, SCOPE_USAGE_READ, SCOPE_PAPERS_WRITE}
    ),
    "solve": frozenset({SCOPE_SOLVE_WRITE, SCOPE_USAGE_READ}),
}


@dataclass(frozen=True)
class Principal:
    """Authenticated caller; authorization and quota checks are separate layers."""

    tenant_id: str
    user_id: str = ""
    scopes: frozenset[str] = frozenset()
    auth_kind: str = "api_key"  # open / bearer / api_key


def _expand_scopes(scopes: list[str]) -> frozenset[str]:
    expanded: set[str] = set()
    for scope in scopes:
        normalized = scope.strip()
        if not normalized:
            continue
        expanded.add(normalized)
        expanded.update(_LEGACY_SCOPE_ALIASES.get(normalized, ()))
    return frozenset(expanded)


def _parse_discipline(value: str) -> Discipline:
    try:
        return Discipline(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"未知学科: {value}") from exc


def _trace_tokens(result: Any) -> int:
    """从 QGenOutput.trace 估算 token 用量（用于计费）。"""
    trace = getattr(result, "trace", None)
    return int(getattr(trace, "total_tokens_est", 0)) if trace is not None else 0


@contextmanager
def _tenant_scoped_agent_storage(
    agent: Any,
    tenant_id: str,
    storage_factory: StorageFactory | None,
) -> Iterator[None]:
    """Bind real QGen/Solve agents to a request-scoped tenant storage view.

    Test/facade agents without storage are left untouched.  Production agents
    receive a fresh storage connection when ``storage_factory`` is available;
    this avoids mutating the long-lived unscoped retrieval backend used by the
    CLI serve composition.
    """
    if not tenant_id:
        yield
        return

    from sciforge.agents import QGenAgent, SolveAgent

    if not isinstance(agent, (QGenAgent, SolveAgent)):
        yield
        return

    agent_any: Any = agent
    original_storage = agent_any.storage
    base_storage = storage_factory() if storage_factory is not None else original_storage
    if base_storage is None:
        raise HTTPException(
            status_code=503,
            detail="鉴权模式下 Agent 必须配置可执行租户隔离的题库 storage",
        )

    try:
        scoped = TenantScopedStorageView(base_storage, tenant_id)
    except TenantStorageConfigurationError as exc:
        if storage_factory is not None:
            close = getattr(base_storage, "close", None)
            if callable(close):
                close()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    original_retriever_storage: Any | None = None
    agent_any.storage = scoped
    if isinstance(agent, QGenAgent):
        original_retriever_storage = agent_any.retriever.storage
        agent_any.retriever.storage = scoped
    try:
        yield
    finally:
        agent_any.storage = original_storage
        if isinstance(agent, QGenAgent):
            agent_any.retriever.storage = original_retriever_storage
        scoped.close()
        if storage_factory is not None:
            close = getattr(base_storage, "close", None)
            if callable(close):
                close()


async def _run_batch(
    task_id: str,
    gen_req: GenerationRequest,
    count: int,
    discipline: Discipline,
    qgen_factory: QGenFactory,
    storage_factory: StorageFactory | None,
    saas_store: SaasStore,
    tenant: str,
) -> None:
    """后台批量生成：逐题生成，实时更新任务进度/结果；异常标 failed。"""
    from sciforge.agents import QGenInput

    saas_store.update_task(task_id, tenant, status="running")
    questions: list[dict[str, Any]] = []
    try:
        for i in range(count):
            agent = qgen_factory(discipline)
            with _tenant_scoped_agent_storage(agent, tenant, storage_factory):
                result = await agent.run(QGenInput(request=gen_req))
            if result.generated is not None:
                gq = result.generated
                questions.append(
                    {
                        "id": gq.question.id,
                        "content": gq.question.content,
                        "solution": gq.solution.solution_latex,
                        "difficulty": gq.difficulty_score.overall,
                        "verified": gq.verification.passed,
                    }
                )
                saas_store.update_task_and_record_usage(
                    task_id,
                    tenant,
                    done=i + 1,
                    result_json=json.dumps(questions, ensure_ascii=False),
                    endpoint="/v1/generate/batch",
                    tokens_est=_trace_tokens(result),
                )
            else:
                saas_store.update_task(
                    task_id,
                    tenant,
                    done=i + 1,
                    result_json=json.dumps(questions, ensure_ascii=False),
                )
        saas_store.update_task(
            task_id,
            tenant,
            status="completed",
            result_json=json.dumps(questions, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch_run_failed", task_id=task_id, error=str(exc)[:200])
        saas_store.update_task(task_id, tenant, status="failed", error=str(exc)[:300])


def _assemble_paper_items(
    storage_factory: StorageFactory,
    req: PaperRequest,
    *,
    tenant_id: str = "",
) -> list[PaperItem]:
    """按 req 过滤条件从题库抽题（用完释放 storage 连接）。"""
    discipline = _parse_discipline(req.discipline)
    storage = storage_factory()
    scoped: TenantScopedStorageView | None = None
    try:
        if tenant_id:
            try:
                scoped = TenantScopedStorageView(storage, tenant_id)
            except TenantStorageConfigurationError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
        read_storage: Any = scoped or storage
        filters: dict[str, object] = {
            "discipline": discipline.value,
            "difficulty_range": (req.difficulty_min, req.difficulty_max),
            "quality_gate": QualityGateFilter(item_kind="question"),
        }
        if req.subject:
            filters["subject"] = req.subject
        if req.question_type:
            filters["question_type"] = req.question_type
        ids = read_storage.search_questions(filters)
        bank = read_storage.load_questions_by_ids(list(ids)[: req.count])
        return [
            PaperItem(
                id=q.id,
                content=q.content,
                difficulty=q.difficulty,
                question_type=str(q.question_type),
                solution=q.solution_content or "",
            )
            for q in bank
        ]
    finally:
        if scoped is not None:
            scoped.close()
        close = getattr(storage, "close", None)
        if callable(close):
            close()


def create_app(
    qgen_factory: QGenFactory,
    solve_factory: SolveFactory,
    *,
    saas_store: SaasStore | None = None,
    storage_factory: StorageFactory | None = None,
    jwt_secret: str = "dev-secret-change-me",
    cors_origins: list[str] | None = None,
    serve_console: bool = True,
) -> FastAPI:
    """构造 FastAPI app；agent 经工厂注入，saas_store 提供时启用鉴权+计费。

    - ``cors_origins``：允许的跨域来源（默认 ``["*"]``，为未来独立部署的
      Web/App/小程序前端铺垫）。鉴权走 Authorization 头（非 Cookie），故
      ``allow_credentials=False`` 与 ``*`` 兼容。
    - ``serve_console``：是否挂载内置 SaaS 控制台静态页到 ``/app``（``/`` 重定向至此）。
    """
    app = FastAPI(title="SciForge API", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def authenticate_principal(
        x_api_key: str = Header(default="", alias="X-API-Key"),
        authorization: str = Header(default=""),
    ) -> Principal:
        """Authenticate only; this layer deliberately does not enforce scope or quota."""
        if saas_store is None:
            return Principal(tenant_id="", scopes=frozenset({"*"}), auth_kind="open")
        if authorization.startswith("Bearer "):
            payload = verify_token(authorization[7:], jwt_secret)
            if payload is None:
                raise HTTPException(status_code=401, detail="无效或过期的 token")
            tenant_id = str(payload.get("tenant_id", ""))
            if not tenant_id or saas_store.get_tenant(tenant_id) is None:
                raise HTTPException(status_code=401, detail="token 缺少有效租户身份")
            return Principal(
                tenant_id=tenant_id,
                user_id=str(payload.get("user_id", "")),
                scopes=frozenset({"*"}),
                auth_kind="bearer",
            )
        else:
            info = saas_store.verify_api_key(x_api_key)
            if info is None or saas_store.get_tenant(info.tenant_id) is None:
                raise HTTPException(status_code=401, detail="无效或缺失 API Key / token")
            return Principal(
                tenant_id=info.tenant_id,
                scopes=_expand_scopes(info.scopes),
                auth_kind="api_key",
            )

    def enforce_scope(principal: Principal, required_scope: str) -> Principal:
        """Authorize an authenticated principal for one canonical capability."""
        if "*" not in principal.scopes and required_scope not in principal.scopes:
            raise HTTPException(status_code=403, detail=f"缺少所需 scope: {required_scope}")
        return principal

    def enforce_quota(principal: Principal) -> Principal:
        """Apply quota only after authentication and authorization have succeeded."""
        if saas_store is not None and not saas_store.has_quota(principal.tenant_id):
            raise HTTPException(status_code=429, detail="配额已用尽")
        return principal

    async def require_generation_scope(
        principal: Principal = Depends(authenticate_principal),
    ) -> Principal:
        return enforce_scope(principal, SCOPE_GENERATION_WRITE)

    async def require_solve_scope(
        principal: Principal = Depends(authenticate_principal),
    ) -> Principal:
        return enforce_scope(principal, SCOPE_SOLVE_WRITE)

    async def require_tasks_scope(
        principal: Principal = Depends(authenticate_principal),
    ) -> Principal:
        return enforce_scope(principal, SCOPE_TASKS_READ)

    async def require_usage_scope(
        principal: Principal = Depends(authenticate_principal),
    ) -> Principal:
        return enforce_scope(principal, SCOPE_USAGE_READ)

    async def require_papers_scope(
        principal: Principal = Depends(authenticate_principal),
    ) -> Principal:
        return enforce_scope(principal, SCOPE_PAPERS_WRITE)

    async def require_generation_quota(
        principal: Principal = Depends(require_generation_scope),
    ) -> Principal:
        return enforce_quota(principal)

    async def require_solve_quota(
        principal: Principal = Depends(require_solve_scope),
    ) -> Principal:
        return enforce_quota(principal)

    async def require_papers_quota(
        principal: Principal = Depends(require_papers_scope),
    ) -> Principal:
        return enforce_quota(principal)

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "sciforge"}

    @app.post("/v1/auth/register", response_model=AuthResponse)
    async def register(req: AuthRequest) -> AuthResponse:
        if saas_store is None:
            raise HTTPException(status_code=404, detail="用户系统未启用")
        try:
            user_id, tenant_id = saas_store.create_user(req.username, req.password)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        token = sign_token({"user_id": user_id, "tenant_id": tenant_id}, jwt_secret)
        return AuthResponse(token=token, user_id=user_id, tenant_id=tenant_id)

    @app.post("/v1/auth/login", response_model=AuthResponse)
    async def login(req: AuthRequest) -> AuthResponse:
        if saas_store is None:
            raise HTTPException(status_code=404, detail="用户系统未启用")
        result = saas_store.authenticate(req.username, req.password)
        if result is None:
            raise HTTPException(status_code=401, detail="用户名或密码错误")
        user_id, tenant_id = result
        token = sign_token({"user_id": user_id, "tenant_id": tenant_id}, jwt_secret)
        return AuthResponse(token=token, user_id=user_id, tenant_id=tenant_id)

    @app.post("/v1/generate", response_model=GenerateResponse)
    async def generate(
        req: GenerateRequest, principal: Principal = Depends(require_generation_quota)
    ) -> GenerateResponse:
        from sciforge.agents import QGenInput

        discipline = _parse_discipline(req.discipline)
        if not req.topics:
            raise HTTPException(status_code=422, detail="topics 不能为空")

        agent = qgen_factory(discipline)
        gen_req = GenerationRequest(
            discipline=discipline,
            subject=req.subject,
            topics=req.topics,
            difficulty_target=req.difficulty,
            question_type=req.question_type,
            strategy=VariantStrategy.TOPIC_GENERATION,
        )
        with _tenant_scoped_agent_storage(agent, principal.tenant_id, storage_factory):
            result = await agent.run(QGenInput(request=gen_req))
        if result.generated is None:
            return GenerateResponse(
                success=False, rounds_used=result.rounds_used, reason=result.reason
            )
        if saas_store is not None and principal.tenant_id:
            saas_store.record_usage(
                principal.tenant_id,
                "/v1/generate",
                billable_units=1,
                tokens_est=_trace_tokens(result),
            )
        gq = result.generated
        return GenerateResponse(
            success=True,
            rounds_used=result.rounds_used,
            question=GeneratedQuestionOut(
                id=gq.question.id,
                content=gq.question.content,
                solution=gq.solution.solution_latex,
                difficulty=gq.difficulty_score.overall,
                question_type=str(gq.question.question_type),
                verified=gq.verification.passed,
            ),
        )

    @app.post("/v1/solve", response_model=SolveResponse)
    async def solve(
        req: SolveRequest, principal: Principal = Depends(require_solve_quota)
    ) -> SolveResponse:
        from sciforge.agents import SolveInput

        if not req.question_id and not req.content:
            raise HTTPException(status_code=422, detail="需提供 question_id 或 content")

        agent = solve_factory(Discipline.MATH)
        inp = (
            SolveInput(question=Question(content=req.content))
            if req.content
            else SolveInput(question_id=req.question_id)
        )
        try:
            with _tenant_scoped_agent_storage(agent, principal.tenant_id, storage_factory):
                result = await agent.run(inp)
        except FileNotFoundError as exc:
            # Missing and foreign-owned IDs intentionally share one response to
            # prevent existence probing across tenants.
            raise HTTPException(status_code=404, detail="题目不存在") from exc
        if result.solution is None:
            return SolveResponse(success=False)
        if saas_store is not None and principal.tenant_id:
            saas_store.record_usage(principal.tenant_id, "/v1/solve", billable_units=1)
        return SolveResponse(
            success=True,
            solution=result.solution.solution_latex,
            confidence=result.confidence,
        )

    @app.get("/v1/usage", response_model=UsageSummary)
    async def usage(principal: Principal = Depends(require_usage_scope)) -> UsageSummary:
        if saas_store is None:
            raise HTTPException(status_code=404, detail="鉴权/计费未启用")
        return saas_store.get_usage(principal.tenant_id)

    @app.post("/v1/behavior")
    async def record_behavior(
        req: BehaviorRequest, principal: Principal = Depends(authenticate_principal)
    ) -> dict[str, str]:
        """记录一次练习行为（P4 推荐数据源）。需 C 端用户登录（Bearer token）。"""
        if saas_store is None:
            raise HTTPException(status_code=404, detail="用户系统未启用")
        user_id = principal.user_id
        if not user_id:
            raise HTTPException(status_code=400, detail="练习行为需 C 端用户身份（Bearer token）")
        saas_store.record_behavior(
            user_id,
            req.question_id,
            req.outcome,
            difficulty=req.difficulty,
            tags=req.tags,
            time_spent_ms=req.time_spent_ms,
        )
        return {"status": "recorded"}

    @app.get("/v1/recommend", response_model=RecommendResponse)
    async def recommend(
        discipline: str = Query(default="math"),
        subject: str = Query(default=""),
        limit: int = Query(default=10, ge=1, le=50),
        principal: Principal = Depends(authenticate_principal),
    ) -> RecommendResponse:
        """按用户练习画像推荐题目（难度贴合最近发展区 + 命中薄弱标签）。"""
        if saas_store is None:
            raise HTTPException(status_code=404, detail="用户系统未启用")
        if storage_factory is None:
            raise HTTPException(status_code=503, detail="推荐需注入题库 storage")
        user_id = principal.user_id
        if not user_id:
            raise HTTPException(status_code=400, detail="推荐需 C 端用户身份（Bearer token）")

        from sciforge.agents.recommend import RecommendEngine

        disc = _parse_discipline(discipline)
        storage = storage_factory()
        scoped: TenantScopedStorageView | None = None
        try:
            try:
                scoped = TenantScopedStorageView(storage, principal.tenant_id)
            except TenantStorageConfigurationError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            filters: dict[str, object] = {
                "discipline": disc.value,
                "quality_gate": QualityGateFilter(item_kind="question"),
            }
            if subject:
                filters["subject"] = subject
            ids = scoped.search_questions(filters)
            candidates = scoped.load_questions_by_ids(list(ids))
        finally:
            if scoped is not None:
                scoped.close()
            close = getattr(storage, "close", None)
            if callable(close):
                close()

        engine = RecommendEngine()
        profile = engine.build_profile(user_id, saas_store.get_behavior(user_id))
        recs = engine.recommend(profile, candidates, limit=limit)
        by_id = {q.id: q for q in candidates}
        items = [
            RecommendItem(
                question_id=r.question_id,
                content=by_id[r.question_id].content if r.question_id in by_id else "",
                difficulty=r.difficulty,
                score=r.score,
                matched_weak_tags=r.matched_weak_tags,
                reason=r.reason,
            )
            for r in recs
        ]
        return RecommendResponse(
            user_id=user_id,
            mastered_difficulty=profile.mastered_difficulty,
            accuracy=profile.accuracy,
            weak_tags=profile.weak_tags,
            items=items,
        )

    @app.post("/v1/generate/batch")
    async def generate_batch(
        req: GenerateBatchRequest,
        background_tasks: BackgroundTasks,
        principal: Principal = Depends(require_generation_scope),
    ) -> dict[str, Any]:
        if saas_store is None:
            raise HTTPException(status_code=503, detail="异步批量需启用 saas_store（鉴权/计费）")
        discipline = _parse_discipline(req.discipline)
        if not req.topics:
            raise HTTPException(status_code=422, detail="topics 不能为空")
        task_id = saas_store.create_task_if_quota_available(principal.tenant_id, req.count)
        if task_id is None:
            raise HTTPException(status_code=429, detail="请求数量超过本月剩余额度")
        gen_req = GenerationRequest(
            discipline=discipline,
            subject=req.subject,
            topics=req.topics,
            difficulty_target=req.difficulty,
            question_type=req.question_type,
            strategy=VariantStrategy.TOPIC_GENERATION,
        )
        background_tasks.add_task(
            _run_batch,
            task_id,
            gen_req,
            req.count,
            discipline,
            qgen_factory,
            storage_factory,
            saas_store,
            principal.tenant_id,
        )
        return {"task_id": task_id, "status": "pending", "total": req.count}

    @app.get("/v1/tasks/{task_id}", response_model=TaskResponse)
    async def get_task(
        task_id: str, principal: Principal = Depends(require_tasks_scope)
    ) -> TaskResponse:
        if saas_store is None:
            raise HTTPException(status_code=404, detail="任务系统未启用")
        info = saas_store.get_task(task_id, principal.tenant_id)
        if info is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return TaskResponse.model_validate(info.model_dump())

    @app.post("/v1/papers", response_model=PaperResponse)
    async def papers(
        req: PaperRequest, principal: Principal = Depends(require_papers_quota)
    ) -> PaperResponse:
        if storage_factory is None:
            raise HTTPException(status_code=503, detail="组卷需注入题库 storage")
        if req.difficulty_min > req.difficulty_max:
            raise HTTPException(status_code=422, detail="difficulty_min 不能大于 difficulty_max")
        items = _assemble_paper_items(
            storage_factory,
            req,
            tenant_id=principal.tenant_id,
        )
        if principal.tenant_id and saas_store is not None and items:
            saas_store.record_usage(principal.tenant_id, "/v1/papers", billable_units=len(items))
        title = req.title or f"{_parse_discipline(req.discipline).value} 试卷"
        return PaperResponse(title=title, count=len(items), questions=items)

    @app.post("/v1/papers/export")
    async def papers_export(
        req: PaperExportRequest, principal: Principal = Depends(require_papers_quota)
    ) -> Response:
        if storage_factory is None:
            raise HTTPException(status_code=503, detail="组卷导出需注入题库 storage")
        if req.difficulty_min > req.difficulty_max:
            raise HTTPException(status_code=422, detail="difficulty_min 不能大于 difficulty_max")
        items = _assemble_paper_items(
            storage_factory,
            req,
            tenant_id=principal.tenant_id,
        )
        title = req.title or f"{_parse_discipline(req.discipline).value} 试卷"
        item_dicts = [it.model_dump() for it in items]
        if principal.tenant_id and saas_store is not None and items:
            saas_store.record_usage(
                principal.tenant_id, "/v1/papers/export", billable_units=len(items)
            )
        if req.format == "docx":
            try:
                data = render_docx(title, item_dicts, include_solutions=req.include_solutions)
            except ImportError as exc:
                raise HTTPException(
                    status_code=501, detail="DOCX 导出需安装 python-docx（docx extra）"
                ) from exc
            return Response(
                content=data,
                media_type=(
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                ),
                headers={"Content-Disposition": 'attachment; filename="paper.docx"'},
            )
        text = render_markdown(title, item_dicts, include_solutions=req.include_solutions)
        return Response(content=text, media_type="text/markdown; charset=utf-8")

    # ── 内置 SaaS 控制台（静态 SPA，调 /v1/* API）──
    static_dir = Path(__file__).parent / "static"
    if serve_console and static_dir.is_dir():

        @app.get("/")
        async def _root() -> RedirectResponse:
            return RedirectResponse(url="/app/")

        # html=True → "/app/" 返回 index.html；放在路由注册之后，不影响 /v1/* 与 /docs
        app.mount("/app", StaticFiles(directory=str(static_dir), html=True), name="console")

    return app
