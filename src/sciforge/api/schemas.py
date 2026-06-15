"""SciForge API 请求/响应模型（P2 FastAPI 网关）"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    discipline: str = "math"
    subject: str = ""
    topics: list[str] = Field(default_factory=list)
    difficulty: int = Field(default=5, ge=1, le=10)
    question_type: str = "proof"


class GeneratedQuestionOut(BaseModel):
    id: str
    content: str
    solution: str
    difficulty: float
    question_type: str
    verified: bool


class GenerateResponse(BaseModel):
    success: bool
    question: GeneratedQuestionOut | None = None
    rounds_used: int = 0
    reason: str = ""


class GenerateBatchRequest(GenerateRequest):
    count: int = Field(default=5, ge=1, le=50)


class SolveRequest(BaseModel):
    question_id: str = ""
    content: str = ""


class SolveResponse(BaseModel):
    success: bool
    solution: str = ""
    confidence: str = ""
