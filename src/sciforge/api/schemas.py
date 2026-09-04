"""SciForge API 请求/响应模型（P2 FastAPI 网关）"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator


class GenerateRequest(BaseModel):
    discipline: str = "math"
    subject: str = ""
    topics: list[str] = Field(default_factory=list)
    difficulty: int = Field(default=5, ge=1, le=10)
    question_type: str = ""
    question_form: str = ""


class GeneratedQuestionOut(BaseModel):
    id: str
    content: str
    solution: str
    difficulty: float
    question_type: str
    question_form: str = ""
    verified: bool


class GenerateResponse(BaseModel):
    success: bool
    question: GeneratedQuestionOut | None = None
    rounds_used: int = 0
    reason: str = ""


class GenerateBatchRequest(GenerateRequest):
    count: int = Field(default=5, ge=1, le=50)


class TaskResponse(BaseModel):
    """Public batch-task view; tenant ownership is intentionally not serialized."""

    id: str
    status: str = "pending"
    total: int = 0
    done: int = 0
    result: list[dict[str, object]] = Field(default_factory=list)
    error: str = ""


class PaperItem(BaseModel):
    id: str
    content: str
    difficulty: int
    question_type: str
    question_form: str = ""
    solution: str = ""


class PaperRequest(BaseModel):
    discipline: str = "math"
    subject: str = ""
    question_type: str = ""
    question_form: str = ""
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


# ── 对话式长任务 ──


ChatMode = Literal[
    "new",
    "evolve",
    "variant",
    "adjust_difficulty",
    "deepen",
    "solve",
    "evaluate",
]


class ConversationCreateRequest(BaseModel):
    title: str = Field(default="", max_length=200)
    discipline: str = "math"


class ConversationOut(BaseModel):
    id: str
    title: str = ""
    discipline: str = "math"
    created_at: str
    updated_at: str


class ConversationListResponse(BaseModel):
    items: list[ConversationOut] = Field(default_factory=list)


class RunBudgetRequest(BaseModel):
    max_steps: int | None = Field(default=None, ge=1, le=10_000)
    max_llm_calls: int | None = Field(default=None, ge=1, le=10_000)
    max_tokens: int | None = Field(default=None, ge=1, le=100_000_000)
    max_wall_time_seconds: float | None = Field(default=None, gt=0, le=86_400)


class ConversationMessageRequest(BaseModel):
    """One explicit chat command; modes have deterministic backend semantics."""

    mode: ChatMode = "new"
    content: str = Field(default="", max_length=100_000)
    source_question_id: str = Field(default="", max_length=200)
    knowledge_ids: list[
        Annotated[
            str,
            StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
        ]
    ] = Field(default_factory=list, max_length=20)
    discipline: str = ""
    subject: str = ""
    topics: list[str] = Field(default_factory=list, max_length=50)
    difficulty: int = Field(default=5, ge=1, le=10)
    question_type: str = ""
    question_form: str = ""
    variant_strategy: Literal[
        "A1_parameter_shift",
        "A2_method_shift",
        "A3_type_shift",
        "A6_reverse",
    ] = "A1_parameter_shift"
    output_type: Literal[
        "question_solution",
        "question_only",
        "structured_json",
        "lecture_note",
    ] = "question_solution"
    template_id: str = Field(default="", max_length=100)
    verification_policy: Literal["fast", "balanced", "strict", "formal"] = "balanced"
    # Deprecated compatibility field for clients shipped before output_type.
    output_format: Literal["markdown", "json"] = "markdown"
    budget: RunBudgetRequest = Field(default_factory=RunBudgetRequest)

    @model_validator(mode="after")
    def _validate_mode_contract(self) -> ConversationMessageRequest:
        if self.mode == "adjust_difficulty" and "difficulty" not in self.model_fields_set:
            raise ValueError("adjust_difficulty 模式必须显式提供 difficulty")
        if self.template_id and self.template_id != self.mode:
            raise ValueError("内置 template_id 必须与 mode 一致")
        if (
            self.mode == "solve"
            and "verification_policy" in self.model_fields_set
            and self.verification_policy != "fast"
        ):
            raise ValueError("solve 只提供求解器置信度；独立验证请使用 evaluate 模式")
        if self.mode == "solve":
            # Omitted policy used to remain the model default "balanced" even
            # though SolveAgent cannot execute it. Persist the honest contract.
            self.verification_policy = "fast"
        if self.output_format == "json" and "output_type" not in self.model_fields_set:
            self.output_type = "structured_json"
        return self


class ConversationMessageOut(BaseModel):
    id: str
    conversation_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    mode: str = ""
    run_id: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: str


class ConversationMessagesResponse(BaseModel):
    items: list[ConversationMessageOut] = Field(default_factory=list)


class ConversationMessageAccepted(BaseModel):
    conversation_id: str
    message_id: str
    run_id: str
    status: str = "pending"
    mode: ChatMode
    events_url: str


class RunUsageOut(BaseModel):
    steps: int = 0
    llm_calls: int = 0
    tokens: int = 0
    wall_time_seconds: float = 0.0


class RunBudgetOut(BaseModel):
    max_steps: int | None = None
    max_llm_calls: int | None = None
    max_tokens: int | None = None
    max_wall_time_seconds: float | None = None


class AgentRunOut(BaseModel):
    id: str
    kind: str
    status: str
    conversation_id: str
    mode: str = ""
    usage: RunUsageOut
    budget: RunBudgetOut
    cancel_requested: bool = False
    output: dict[str, object] | None = None
    error: str = ""
    created_at: str
    updated_at: str
    completed_at: str | None = None


class RunCancelResponse(BaseModel):
    id: str
    status: str
    cancel_requested: bool


class RunBudgetIncrementRequest(BaseModel):
    """Positive additions to a paused run's existing hard limits."""

    max_steps: int | None = Field(default=None, ge=1, le=10_000)
    max_llm_calls: int | None = Field(default=None, ge=1, le=10_000)
    max_tokens: int | None = Field(default=None, ge=1, le=100_000_000)
    max_wall_time_seconds: float | None = Field(default=None, gt=0, le=86_400)


class RunResumeRequest(BaseModel):
    budget_increment: RunBudgetIncrementRequest = Field(default_factory=RunBudgetIncrementRequest)


class RunResumeResponse(BaseModel):
    id: str
    status: str
    resume_scheduled: bool
    budget: RunBudgetOut


class ActionTemplateOut(BaseModel):
    id: ChatMode
    label: str
    description: str
    required_fields: list[str] = Field(default_factory=list)
    strategy: str = ""
    durable_kind: str


class ActionTemplatesResponse(BaseModel):
    items: list[ActionTemplateOut] = Field(default_factory=list)


class KnowledgeSearchItem(BaseModel):
    id: str
    kind: Literal["knowledge"] = "knowledge"
    discipline: str
    subject: str = ""
    item_type: str = ""
    number: str = ""
    content: str
    difficulty: int = 5
    tags: list[str] = Field(default_factory=list)
    source_file: str = ""
    source_page: int | None = None
    start_line: int = Field(default=0, ge=0)
    end_line: int = Field(default=0, ge=0)


class KnowledgeSearchResponse(BaseModel):
    items: list[KnowledgeSearchItem] = Field(default_factory=list)
