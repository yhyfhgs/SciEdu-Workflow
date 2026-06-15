"""SciForge API 请求/响应模型（P2 FastAPI 网关）"""

from __future__ import annotations

from typing import Literal

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


class PaperItem(BaseModel):
    id: str
    content: str
    difficulty: int
    question_type: str
    solution: str = ""


class PaperRequest(BaseModel):
    discipline: str = "math"
    subject: str = ""
    question_type: str = ""
    difficulty_min: int = Field(default=1, ge=1, le=10)
    difficulty_max: int = Field(default=10, ge=1, le=10)
    count: int = Field(default=10, ge=1, le=100)
    title: str = ""


class PaperResponse(BaseModel):
    title: str
    count: int
    questions: list[PaperItem] = Field(default_factory=list)


class PaperExportRequest(PaperRequest):
    format: Literal["markdown", "docx"] = "markdown"
    include_solutions: bool = True


class SolveRequest(BaseModel):
    question_id: str = ""
    content: str = ""


class SolveResponse(BaseModel):
    success: bool
    solution: str = ""
    confidence: str = ""


class AuthRequest(BaseModel):
    username: str
    password: str


class AuthResponse(BaseModel):
    token: str
    user_id: str
    tenant_id: str


# ── P4 个性化推荐 ──


class BehaviorRequest(BaseModel):
    question_id: str = ""
    outcome: Literal["correct", "incorrect", "skipped"] = "skipped"
    difficulty: int = Field(default=5, ge=1, le=10)
    tags: list[str] = Field(default_factory=list)
    time_spent_ms: int = Field(default=0, ge=0)


class RecommendItem(BaseModel):
    question_id: str
    content: str = ""
    difficulty: int = 5
    score: float = 0.0
    matched_weak_tags: list[str] = Field(default_factory=list)
    reason: str = ""


class RecommendResponse(BaseModel):
    user_id: str = ""
    mastered_difficulty: float = 3.0
    accuracy: float = 0.0
    weak_tags: list[str] = Field(default_factory=list)
    items: list[RecommendItem] = Field(default_factory=list)
