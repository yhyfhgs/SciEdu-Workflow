"""MathForge 核心数据模型 (Pydantic v2)"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field


# ── 枚举 ──────────────────────────────────────────────


class Subject(str, Enum):
    ANALYSIS = "数学分析"
    LINEAR_ALGEBRA = "高等代数"
    GROUP_THEORY = "群论"


class QuestionType(str, Enum):
    CALCULATION = "calculation"
    PROOF = "proof"
    CONSTRUCTION = "construction"
    TRUE_FALSE = "true_false"
    CHOICE = "choice"


class KPType(str, Enum):
    DEFINITION = "definition"
    THEOREM = "theorem"
    LEMMA = "lemma"
    COROLLARY = "corollary"
    PROPOSITION = "proposition"
    EXAMPLE = "example"
    REMARK = "remark"
    PROOF_TECHNIQUE = "proof_technique"
    FORMULA = "formula"


class VariantStrategy(str, Enum):
    PARAMETER_SHIFT = "A1_parameter_shift"
    METHOD_SHIFT = "A2_method_shift"
    TYPE_SHIFT = "A3_type_shift"
    ESCALATE = "A4_escalate"
    SIMPLIFY = "A5_simplify"
    REVERSE = "A6_reverse"
    TOPIC_GENERATION = "B1_topic_generation"
    CROSS_TOPIC = "B2_cross_topic"


class ExamType(str, Enum):
    LINGSHI = "零试"
    YISHI = "一试"
    NVZI = "女子"


class VerifyStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class MdBlockType(str, Enum):
    DEFINITION = "definition"
    THEOREM = "theorem"
    LEMMA = "lemma"
    COROLLARY = "corollary"
    PROPOSITION = "proposition"
    EXAMPLE = "example"
    EXERCISE = "exercise"
    REMARK = "remark"
    PROOF = "proof"
    PROPERTY = "property"
    TEXT = "text"


class MdSourceType(str, Enum):
    LECTURE = "lecture"
    TEXTBOOK = "textbook"


class DocumentFormat(str, Enum):
    TEX = "tex"
    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"
    CSV = "csv"


# ── 来源元数据 ────────────────────────────────────────


class QuestionSource(BaseModel):
    """题目结构化来源信息"""
    exam_name: str | None = None
    exam_type: ExamType | None = None
    year: int | None = None
    month: int | None = None
    problem_number: int | None = None
    book_title: str | None = None
    chapter: str | None = None
    page: int | None = None
    exercise_number: str | None = None


class KPSource(BaseModel):
    """知识点来源信息"""
    book_title: str | None = None
    chapter: str | None = None
    section: str | None = None
    page: int | None = None
    source_file: str | None = None
    line_range: tuple[int, int] | None = None


# ── 核心实体 ──────────────────────────────────────────


class KnowledgePoint(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    content_latex: str = ""
    content_plain: str = ""
    kp_type: KPType = KPType.THEOREM
    difficulty: int = Field(ge=1, le=10, default=5)
    tags: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    source: KPSource | None = None
    embedding: list[float] | None = None
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(validate_assignment=True)


class Question(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content_latex: str = ""
    content_plain: str = ""
    difficulty: int = Field(ge=1, le=10, default=5)
    difficulty_ai: float | None = None
    question_type: QuestionType = QuestionType.PROOF
    subject: Subject = Subject.ANALYSIS
    tags: list[str] = Field(default_factory=list)
    source: QuestionSource | None = None
    is_generated: bool = False
    parent_id: str | None = None
    generation_meta: dict = Field(default_factory=dict)
    embedding: list[float] | None = None
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(validate_assignment=True)


class Solution(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question_id: str = ""
    solution_latex: str = ""
    methods_used: list[str] = Field(default_factory=list)
    step_count: int = Field(ge=0, default=0)
    confidence: str = "medium"
    is_verified: bool = False


class DifficultyScore(BaseModel):
    concept_count: int = Field(ge=1, le=10, default=5)
    step_count: int = Field(ge=1, le=10, default=5)
    abstraction_level: int = Field(ge=1, le=10, default=5)
    technique_novelty: int = Field(ge=1, le=10, default=5)
    condition_parsing: int = Field(ge=1, le=10, default=5)
    computation_load: int = Field(ge=1, le=10, default=5)
    proof_structure: int = Field(ge=1, le=10, default=5)
    prerequisite_depth: int = Field(ge=1, le=10, default=5)
    multi_representation: int = Field(ge=1, le=10, default=5)
    metacognition: int = Field(ge=1, le=10, default=5)
    overall: float = 5.0
    reasoning: str = ""

    WEIGHTS: ClassVar[dict[str, float]] = {
        "concept_count": 0.15,
        "step_count": 0.15,
        "abstraction_level": 0.12,
        "technique_novelty": 0.12,
        "condition_parsing": 0.10,
        "computation_load": 0.08,
        "proof_structure": 0.10,
        "prerequisite_depth": 0.08,
        "multi_representation": 0.05,
        "metacognition": 0.05,
    }

    def compute_overall(self) -> float:
        score = sum(
            getattr(self, dim) * w
            for dim, w in self.WEIGHTS.items()
        )
        self.overall = round(score, 1)
        return self.overall


# ── 请求/响应 ─────────────────────────────────────────


class GenerationRequest(BaseModel):
    subject: Subject
    topics: list[str]
    difficulty_target: int = Field(ge=1, le=10)
    question_type: QuestionType
    strategy: VariantStrategy
    source_question: Question | None = None
    exclusion_ids: set[str] = Field(default_factory=set)
    count: int = 1


class ContextBundle(BaseModel):
    knowledge_points: list[KnowledgePoint] = Field(default_factory=list)
    reference_questions: list[Question] = Field(default_factory=list)
    negative_examples: list[Question] = Field(default_factory=list)
    feedback_from_previous_round: str = ""

    def to_prompt_block(self) -> str:
        """格式化为 Markdown，注入 LLM 提示词"""
        sections: list[str] = []
        if self.knowledge_points:
            sections.append("## 相关知识点\n")
            for i, kp in enumerate(self.knowledge_points, 1):
                sections.append(
                    f"### 知识点 {i}: {kp.title} [{kp.kp_type.value}] "
                    f"(难度 {kp.difficulty}/10)\n{kp.content_latex}\n"
                )
        if self.reference_questions:
            sections.append("\n## 参考题目（供参考，不要照搬）\n")
            for i, q in enumerate(self.reference_questions, 1):
                sections.append(
                    f"### 参考题 {i} (难度 {q.difficulty}/10)\n{q.content_latex}\n"
                )
        if self.negative_examples:
            sections.append("\n## 已有题（新题必须与之有实质区别）\n")
            for i, q in enumerate(self.negative_examples, 1):
                sections.append(f"### 已有题 {i}\n{q.content_latex}\n")
        if self.feedback_from_previous_round:
            sections.append(f"\n## 上一轮反馈\n{self.feedback_from_previous_round}\n")
        return "\n".join(sections)


class VerificationResult(BaseModel):
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    suggestions: str = ""

    @property
    def has_suggestions(self) -> bool:
        return bool(self.suggestions.strip())


class GeneratedQuestion(BaseModel):
    question: Question
    solution: Solution
    difficulty_score: DifficultyScore
    verification: VerificationResult
    generation_meta: dict = Field(default_factory=dict)


# ── Pipeline 中间模型 ─────────────────────────────────


class ParsedProblem(BaseModel):
    """TeX 解析器的中间表示"""
    problem_number: int
    problem_latex: str
    solution_latex: str = ""
    has_solution: bool = True
    source_file: str = ""
    exam_type: ExamType = ExamType.LINGSHI
    year: int = 0
    month: int | None = None
    difficulty_estimate: int = 5
    is_exercise: bool = False
    exercise_label: str = ""
    parent_problem_number: int | None = None


class TexExamMetadata(BaseModel):
    """TeX 文件的考试级元数据"""
    year: int
    exam_type: ExamType
    month: int | None = None
    source_file: str = ""
    section_title: str = ""
    total_problems: int = 0
    total_exercises: int = 0


class MdBlock(BaseModel):
    """从 MD/PDF 解析出的结构化块"""
    block_type: MdBlockType
    number: str | None = None
    name: str | None = None
    content: str = ""
    proof_content: str | None = None
    source_file: str = ""
    line_start: int = 0
    line_end: int = 0
    page_start: int | None = None
    page_end: int | None = None
    images: list[str] = Field(default_factory=list)
    has_garbled_content: bool = False


class MdSection(BaseModel):
    title: str = ""
    number: str = ""
    blocks: list[MdBlock] = Field(default_factory=list)


class MdChapter(BaseModel):
    title: str = ""
    sections: list[MdSection] = Field(default_factory=list)


# ── PDF Pipeline 模型 ─────────────────────────────────


class TocEntry(BaseModel):
    """目录条目"""
    title: str
    level: int = 1
    page: int = 0


class DocumentSkeleton(BaseModel):
    """文档结构骨架"""
    total_pages: int = 0
    toc_entries: list[TocEntry] = Field(default_factory=list)
    page_types: dict[int, str] = Field(default_factory=dict)
    content_page_ranges: list[tuple[int, int]] = Field(default_factory=list)


class SpanningBlock(BaseModel):
    """跨页内容块"""
    block_type: MdBlockType
    number: str | None = None
    name: str | None = None
    content: str = ""
    page_start: int = 0
    page_end: int = 0
    is_complete: bool = False
    open_marker: str = ""
    close_marker: str | None = None


class BatchProgress(BaseModel):
    """批次处理进度"""
    pdf_hash: str = ""
    pdf_path: str = ""
    total_pages: int = 0
    phase: int = 1
    batches_completed: int = 0
    batches_total: int = 0
    blocks_extracted: int = 0
    errors: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    last_checkpoint_at: datetime | None = None


# ── 验证报告 ──────────────────────────────────────────


class ValidationIssue(BaseModel):
    """单个验证问题"""
    level: str = "warning"  # warning | error
    category: str = ""      # bracket, semantic, reference, numbering
    message: str = ""
    source_file: str = ""
    block_id: str = ""
    line: int | None = None


class ValidationReport(BaseModel):
    """内容验证报告"""
    total_blocks: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    bracket_errors: int = 0
    semantic_warnings: int = 0
    dangling_references: int = 0
    numbering_gaps: int = 0

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "warning")
