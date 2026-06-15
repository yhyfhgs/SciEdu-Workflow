"""SciForge FastAPI 网关（P2 第一步）

工厂模式注入 agent，便于测试与依赖/资源管理：

    app = create_app(qgen_factory, solve_factory)

端点：
- GET  /v1/health   健康检查
- POST /v1/generate 单题生成（调 QGenAgent）
- POST /v1/solve    求解（调 SolveAgent；支持 question_id 或直接传 content）

注：身份鉴权 / 计费 / 多租户 / 异步批量 / 组卷导出需 storage schema 稳定后接入（P2 后续）。
当前未做鉴权——生产前必须加 API Key / JWT 中间件。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException

from sciforge.api.schemas import (
    GeneratedQuestionOut,
    GenerateRequest,
    GenerateResponse,
    SolveRequest,
    SolveResponse,
)
from sciforge.core.models import (
    Discipline,
    GenerationRequest,
    Question,
    VariantStrategy,
)

if TYPE_CHECKING:
    from sciforge.agents import QGenAgent, SolveAgent

QGenFactory = Callable[[Discipline], "QGenAgent"]
SolveFactory = Callable[[Discipline], "SolveAgent"]


def _parse_discipline(value: str) -> Discipline:
    try:
        return Discipline(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"未知学科: {value}") from exc


def create_app(qgen_factory: QGenFactory, solve_factory: SolveFactory) -> FastAPI:
    """构造 FastAPI app；agent 经工厂注入（生产传真实工厂，测试传 mock）。"""
    app = FastAPI(title="SciForge API", version="0.1.0")

    @app.get("/v1/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "sciforge"}

    @app.post("/v1/generate", response_model=GenerateResponse)
    async def generate(req: GenerateRequest) -> GenerateResponse:
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
    async def solve(req: SolveRequest) -> SolveResponse:
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
        return SolveResponse(
            success=True,
            solution=result.solution.solution_latex,
            confidence=result.confidence,
        )

    return app
