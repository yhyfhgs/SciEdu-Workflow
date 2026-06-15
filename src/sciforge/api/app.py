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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Response

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
)
from sciforge.api.security import sign_token, verify_token
from sciforge.core.models import (
    Discipline,
    GenerationRequest,
    Question,
    VariantStrategy,
)
from sciforge.export import render_docx, render_markdown
from sciforge.storage.saas import SaasStore, TaskInfo, UsageSummary

if TYPE_CHECKING:
    from sciforge.agents import QGenAgent, SolveAgent
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

logger = structlog.get_logger()

QGenFactory = Callable[[Discipline], "QGenAgent"]
SolveFactory = Callable[[Discipline], "SolveAgent"]
StorageFactory = Callable[[], "SQLiteStorageBackend"]


def _parse_discipline(value: str) -> Discipline:
    try:
        return Discipline(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"未知学科: {value}") from exc


def _trace_tokens(result: Any) -> int:
    """从 QGenOutput.trace 估算 token 用量（用于计费）。"""
    trace = getattr(result, "trace", None)
    return int(getattr(trace, "total_tokens_est", 0)) if trace is not None else 0


async def _run_batch(
    task_id: str,
    gen_req: GenerationRequest,
    count: int,
    discipline: Discipline,
    qgen_factory: QGenFactory,
    saas_store: SaasStore,
    tenant: str,
) -> None:
    """后台批量生成：逐题生成，实时更新任务进度/结果；异常标 failed。"""
    from sciforge.agents import QGenInput

    saas_store.update_task(task_id, status="running")
    questions: list[dict[str, Any]] = []
    try:
        for i in range(count):
            agent = qgen_factory(discipline)
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
                if tenant:
                    saas_store.record_usage(
                        tenant, "/v1/generate/batch", tokens_est=_trace_tokens(result)
                    )
            saas_store.update_task(
                task_id, done=i + 1, result_json=json.dumps(questions, ensure_ascii=False)
            )
        saas_store.update_task(
            task_id, status="completed", result_json=json.dumps(questions, ensure_ascii=False)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("batch_run_failed", task_id=task_id, error=str(exc)[:200])
        saas_store.update_task(task_id, status="failed", error=str(exc)[:300])


def _assemble_paper_items(storage_factory: StorageFactory, req: PaperRequest) -> list[PaperItem]:
    """按 req 过滤条件从题库抽题（用完释放 storage 连接）。"""
    discipline = _parse_discipline(req.discipline)
    storage = storage_factory()
    try:
        filters: dict[str, object] = {
            "discipline": discipline.value,
            "difficulty_range": (req.difficulty_min, req.difficulty_max),
        }
        if req.subject:
            filters["subject"] = req.subject
        if req.question_type:
            filters["question_type"] = req.question_type
        ids = storage.search_questions(filters)
        bank = storage.load_questions_by_ids(list(ids)[: req.count])
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
) -> FastAPI:
    """构造 FastAPI app；agent 经工厂注入，saas_store 提供时启用鉴权+计费。"""
    app = FastAPI(title="SciForge API", version="0.1.0")

    async def require_tenant(
        x_api_key: str = Header(default="", alias="X-API-Key"),
        authorization: str = Header(default=""),
    ) -> str:
        """鉴权：开放模式返回空租户；否则 Bearer token（C 端）或 X-API-Key（B2B）→ tenant_id。"""
        if saas_store is None:
            return ""
        if authorization.startswith("Bearer "):
            payload = verify_token(authorization[7:], jwt_secret)
            if payload is None:
                raise HTTPException(status_code=401, detail="无效或过期的 token")
            tenant_id = str(payload.get("tenant_id", ""))
        else:
            info = saas_store.verify_api_key(x_api_key)
            if info is None:
                raise HTTPException(status_code=401, detail="无效或缺失 API Key / token")
            tenant_id = info.tenant_id
        if not saas_store.has_quota(tenant_id):
            raise HTTPException(status_code=429, detail="配额已用尽")
        return tenant_id

    async def require_identity(
        x_api_key: str = Header(default="", alias="X-API-Key"),
        authorization: str = Header(default=""),
    ) -> tuple[str, str]:
        """返回 (user_id, tenant_id)。轻量端点（行为/推荐）用：鉴权但不计配额。

        Bearer token（C 端）带 user_id；API Key（B2B）无终端用户 → user_id 为空。
        """
        if saas_store is None:
            return "", ""
        if authorization.startswith("Bearer "):
            payload = verify_token(authorization[7:], jwt_secret)
            if payload is None:
                raise HTTPException(status_code=401, detail="无效或过期的 token")
            return str(payload.get("user_id", "")), str(payload.get("tenant_id", ""))
        info = saas_store.verify_api_key(x_api_key)
        if info is None:
            raise HTTPException(status_code=401, detail="无效或缺失 API Key / token")
        return "", info.tenant_id

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
        req: GenerateRequest, tenant: str = Depends(require_tenant)
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
        result = await agent.run(QGenInput(request=gen_req))
        if result.generated is None:
            return GenerateResponse(
                success=False, rounds_used=result.rounds_used, reason=result.reason
            )
        if saas_store is not None and tenant:
            saas_store.record_usage(
                tenant, "/v1/generate", billable_units=1, tokens_est=_trace_tokens(result)
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
    async def solve(req: SolveRequest, tenant: str = Depends(require_tenant)) -> SolveResponse:
        from sciforge.agents import SolveInput

        if not req.question_id and not req.content:
            raise HTTPException(status_code=422, detail="需提供 question_id 或 content")

        agent = solve_factory(Discipline.MATH)
        inp = (
            SolveInput(question=Question(content=req.content))
            if req.content
            else SolveInput(question_id=req.question_id)
        )
        result = await agent.run(inp)
        if result.solution is None:
            return SolveResponse(success=False)
        if saas_store is not None and tenant:
            saas_store.record_usage(tenant, "/v1/solve", billable_units=1)
        return SolveResponse(
            success=True,
            solution=result.solution.solution_latex,
            confidence=result.confidence,
        )

    @app.get("/v1/usage", response_model=UsageSummary)
    async def usage(tenant: str = Depends(require_tenant)) -> UsageSummary:
        if saas_store is None:
            raise HTTPException(status_code=404, detail="鉴权/计费未启用")
        return saas_store.get_usage(tenant)

    @app.post("/v1/behavior")
    async def record_behavior(
        req: BehaviorRequest, identity: tuple[str, str] = Depends(require_identity)
    ) -> dict[str, str]:
        """记录一次练习行为（P4 推荐数据源）。需 C 端用户登录（Bearer token）。"""
        if saas_store is None:
            raise HTTPException(status_code=404, detail="用户系统未启用")
        user_id, _tenant = identity
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
        identity: tuple[str, str] = Depends(require_identity),
    ) -> RecommendResponse:
        """按用户练习画像推荐题目（难度贴合最近发展区 + 命中薄弱标签）。"""
        if saas_store is None:
            raise HTTPException(status_code=404, detail="用户系统未启用")
        if storage_factory is None:
            raise HTTPException(status_code=503, detail="推荐需注入题库 storage")
        user_id, _tenant = identity
        if not user_id:
            raise HTTPException(status_code=400, detail="推荐需 C 端用户身份（Bearer token）")

        from sciforge.agents.recommend import RecommendEngine

        disc = _parse_discipline(discipline)
        storage = storage_factory()
        try:
            filters: dict[str, object] = {"discipline": disc.value}
            if subject:
                filters["subject"] = subject
            ids = storage.search_questions(filters)
            candidates = storage.load_questions_by_ids(list(ids))
        finally:
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
        tenant: str = Depends(require_tenant),
    ) -> dict[str, Any]:
        if saas_store is None:
            raise HTTPException(status_code=503, detail="异步批量需启用 saas_store（鉴权/计费）")
        discipline = _parse_discipline(req.discipline)
        if not req.topics:
            raise HTTPException(status_code=422, detail="topics 不能为空")
        task_id = saas_store.create_task(tenant, req.count)
        gen_req = GenerationRequest(
            discipline=discipline,
            subject=req.subject,
            topics=req.topics,
            difficulty_target=req.difficulty,
            question_type=req.question_type,
            strategy=VariantStrategy.TOPIC_GENERATION,
        )
        background_tasks.add_task(
            _run_batch, task_id, gen_req, req.count, discipline, qgen_factory, saas_store, tenant
        )
        return {"task_id": task_id, "status": "pending", "total": req.count}

    @app.get("/v1/tasks/{task_id}", response_model=TaskInfo)
    async def get_task(task_id: str, tenant: str = Depends(require_tenant)) -> TaskInfo:
        if saas_store is None:
            raise HTTPException(status_code=404, detail="任务系统未启用")
        info = saas_store.get_task(task_id)
        if info is None:
            raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
        return info

    @app.post("/v1/papers", response_model=PaperResponse)
    async def papers(req: PaperRequest, tenant: str = Depends(require_tenant)) -> PaperResponse:
        if storage_factory is None:
            raise HTTPException(status_code=503, detail="组卷需注入题库 storage")
        if req.difficulty_min > req.difficulty_max:
            raise HTTPException(status_code=422, detail="difficulty_min 不能大于 difficulty_max")
        items = _assemble_paper_items(storage_factory, req)
        if tenant and saas_store is not None and items:
            saas_store.record_usage(tenant, "/v1/papers", billable_units=len(items))
        title = req.title or f"{_parse_discipline(req.discipline).value} 试卷"
        return PaperResponse(title=title, count=len(items), questions=items)

    @app.post("/v1/papers/export")
    async def papers_export(
        req: PaperExportRequest, tenant: str = Depends(require_tenant)
    ) -> Response:
        if storage_factory is None:
            raise HTTPException(status_code=503, detail="组卷导出需注入题库 storage")
        if req.difficulty_min > req.difficulty_max:
            raise HTTPException(status_code=422, detail="difficulty_min 不能大于 difficulty_max")
        items = _assemble_paper_items(storage_factory, req)
        title = req.title or f"{_parse_discipline(req.discipline).value} 试卷"
        item_dicts = [it.model_dump() for it in items]
        if tenant and saas_store is not None and items:
            saas_store.record_usage(tenant, "/v1/papers/export", billable_units=len(items))
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

    return app
