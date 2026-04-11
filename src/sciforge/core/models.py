"""SciForge 核心数据模型 (Pydantic v2)"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

# ── 枚举 ──────────────────────────────────────────────


class Discipline(StrEnum):
    """顶层学科分类（稳定 API，新增需改代码）

    用于路由 Pipeline/Agent 的学科特化逻辑。二级学科（subject）保持字符串，
    由 DisciplineSpec 约束允许值。
    """

    MATH = "math"
    PHYSICS = "physics"
    CHEMISTRY = "chemistry"
    BIOLOGY = "biology"
    OTHER = "other"


# 注意：二级 subject、kp_type、question_type、exam_type 都改为 str 字段。
# 合法值由 DisciplineSpec 在运行时约束（见 sciforge/disciplines/）。
# 历史枚举 Subject/KPType/QuestionType/ExamType 已删除。


class VariantStrategy(StrEnum):
    PARAMETER_SHIFT = "A1_parameter_shift"
    METHOD_SHIFT = "A2_method_shift"
    TYPE_SHIFT = "A3_type_shift"
    ESCALATE = "A4_escalate"
    SIMPLIFY = "A5_simplify"
    REVERSE = "A6_reverse"
    TOPIC_GENERATION = "B1_topic_generation"
    CROSS_TOPIC = "B2_cross_topic"


class VerifyStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


class MdBlockType(StrEnum):
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


class MdSourceType(StrEnum):
    LECTURE = "lecture"
    TEXTBOOK = "textbook"


class DocumentFormat(StrEnum):
    TEX = "tex"
    MARKDOWN = "markdown"
    PDF = "pdf"
    DOCX = "docx"
    CSV = "csv"
    PPTX = "pptx"
    EPUB = "epub"


class RelationType(StrEnum):
    PREREQUISITE = "prerequisite"
    SUCCESSOR = "successor"
    COROLLARY_OF = "corollary_of"
    GENERALIZATION = "generalization"
    SPECIALIZATION = "specialization"
    EQUIVALENT = "equivalent"
    RELATED = "related"
    TESTS = "tests"
    ILLUSTRATES = "illustrates"
    APPLIES_METHOD = "applies_method"
    VARIANT_OF = "variant_of"
    HARDER_THAN = "harder_than"
    SIMILAR_TO = "similar_to"
    PART_OF = "part_of"
    PROOF_OF = "proof_of"


# ── 来源元数据 ────────────────────────────────────────


class QuestionSource(BaseModel):
    """题目结构化来源信息"""

    exam_name: str | None = None
    exam_type: str | None = None  # 形如 "零试" / "一试" / "奥赛" 等，不限 enum
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
    """知识点存储模型 — 与 DB schema 一一对应"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    discipline: Discipline = Discipline.MATH  # 顶层学科（math/physics/...）
    kp_type: str = "theorem"  # 由 DisciplineSpec.allowed_kp_types 约束
    number: str = ""
    content: str = ""  # LaTeX+文本混排，唯一内容字段
    proof_content: str | None = None  # 证明文本（定理/引理/命题）
    proof_methods: list[str] = Field(default_factory=list)
    difficulty: int = Field(ge=1, le=10, default=5)
    subject: str = ""
    taxonomy_path: str = ""
    tags: list[str] = Field(default_factory=list)  # 有序段列表 ["math:数学", "algebra:代数", ...]
    relations: list[ItemRelation] = Field(default_factory=list)  # 内存字段，不持久化
    source_file: str = ""
    source_chapter: str = ""
    source_section: str = ""
    # 位置/顺序字段（pipeline 提取时填充）
    start_line: int = 0  # 全局累计行号 (跨 mdblock)
    end_line: int = 0
    order_index: int = 0  # 文件内全局递增序号 (从 0 起)
    page_start: int | None = None  # 来自 mdblock 元数据 (1-based)
    page_end: int | None = None
    quality_grade: str = "B"
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(validate_assignment=True)


class Solution(BaseModel):
    """题目解答 — 用于生成流程的内存对象（parse pipeline 已直接嵌入到 Question.solution_content）"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question_id: str = ""
    solution_latex: str = ""
    solution_plain: str = ""
    methods_used: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    is_verified: bool = False


class Question(BaseModel):
    """题目存储模型 — solution 直接嵌入，不再有独立 solutions 表"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    discipline: Discipline = Discipline.MATH  # 顶层学科
    question_type: str = "proof"  # 由 DisciplineSpec.allowed_question_types 约束
    number: str = ""
    content: str = ""  # 题目内容
    solution_content: str | None = None  # 解答文本
    solution_methods: list[str] = Field(default_factory=list)
    answer: str = ""  # 简答（选择题答案、填空答案等）
    difficulty: int = Field(ge=1, le=10, default=5)
    subject: str = ""
    taxonomy_path: str = ""
    tags: list[str] = Field(default_factory=list)  # 有序段列表 ["math:数学", "algebra:代数", ...]
    relations: list[ItemRelation] = Field(default_factory=list)  # 内存字段，不持久化
    source_file: str = ""
    source_chapter: str = ""
    exam_type: str | None = None
    exam_year: int | None = None
    # 位置/顺序字段（pipeline 提取时填充）
    start_line: int = 0
    end_line: int = 0
    order_index: int = 0
    page_start: int | None = None
    page_end: int | None = None
    quality_grade: str = "B"
    is_generated: bool = False
    parent_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(validate_assignment=True)


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
        score = sum(getattr(self, dim) * w for dim, w in self.WEIGHTS.items())
        self.overall = round(score, 1)
        return self.overall


# ── LLM Tool Calling ────────────────────────────────────


class ToolDefinition(BaseModel):
    """LLM 可调用工具的定义"""

    name: str
    description: str
    parameters: dict[str, object] = Field(default_factory=dict)


class ToolCall(BaseModel):
    """LLM 发出的工具调用"""

    tool_name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class ToolResult(BaseModel):
    """工具执行结果"""

    call_id: str
    output: str = ""
    is_error: bool = False


class ConversationMessage(BaseModel):
    """多轮对话中的一条消息"""

    role: Literal["user", "assistant", "tool"] = "user"
    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)


# ── 代码执行与数值验证 ──────────────────────────────────


class CodeExecutionResult(BaseModel):
    """沙箱代码执行结果"""

    code: str = ""
    stdout: str = ""
    stderr: str = ""
    return_value: str | None = None
    success: bool = True
    timeout: bool = False
    execution_time_ms: int = 0


class NumericalVerification(BaseModel):
    """数值验证结果"""

    test_cases: list[dict[str, object]] = Field(default_factory=list)
    all_passed: bool = False
    discrepancies: list[str] = Field(default_factory=list)
    code_used: str = ""
    execution: CodeExecutionResult | None = None


# ── 流水线阶段中间结果 ──────────────────────────────────


class PipelineStageResult(BaseModel):
    """流水线各阶段间传递的中间结果"""

    candidate: Question | None = None
    solution: Solution | None = None
    verification: VerificationResult | None = None
    difficulty_score: DifficultyScore | None = None
    stage: Literal["generated", "solved", "verified", "annotated", "failed"] = "generated"
    error: str | None = None


# ── 进化引擎 ────────────────────────────────────────────


class CandidateRecord(BaseModel):
    """候选题的完整记录（含通过/失败状态），用于进化追踪"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question: Question
    solution: Solution | None = None
    verification: VerificationResult | None = None
    difficulty_score: DifficultyScore | None = None
    passed: bool = False
    failure_reasons: list[str] = Field(default_factory=list)
    parent_id: str | None = None
    generation: int = 0
    improvement_strategy: str = ""
    improvement_directive: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class EvolutionSession(BaseModel):
    """进化会话快照，用于 checkpoint/resume"""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request: GenerationRequest | None = None
    candidates: list[CandidateRecord] = Field(default_factory=list)
    best_result: GeneratedQuestion | None = None
    round_num: int = 0
    max_rounds: int = 3
    improvement_rounds: int = 0
    max_improvement_rounds: int = 2
    iteration_summaries: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    last_checkpoint_at: datetime | None = None


# ── 请求/响应 ─────────────────────────────────────────


class GenerationRequest(BaseModel):
    discipline: Discipline = Discipline.MATH
    subject: str = ""  # 形如 "real_analysis:实分析"
    topics: list[str]
    difficulty_target: int = Field(ge=1, le=10)
    question_type: str = "proof"
    strategy: VariantStrategy
    source_question: Question | None = None
    exclusion_ids: set[str] = Field(default_factory=set)
    count: int = 1
    enable_evolution: bool = False
    max_improvement_rounds: int = 2


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
                    f"### 知识点 {i}: {kp.number} [{kp.kp_type}] "
                    f"(难度 {kp.difficulty}/10)\n{kp.content}\n"
                )
        if self.reference_questions:
            sections.append("\n## 参考题目（供参考，不要照搬）\n")
            for i, q in enumerate(self.reference_questions, 1):
                sections.append(f"### 参考题 {i} (难度 {q.difficulty}/10)\n{q.content}\n")
        if self.negative_examples:
            sections.append("\n## 已有题（新题必须与之有实质区别）\n")
            for i, q in enumerate(self.negative_examples, 1):
                sections.append(f"### 已有题 {i}\n{q.content}\n")
        if self.feedback_from_previous_round:
            sections.append(f"\n## 上一轮反馈\n{self.feedback_from_previous_round}\n")
        return "\n".join(sections)


class VerificationResult(BaseModel):
    passed: bool
    checks: dict[str, bool] = Field(default_factory=dict)
    suggestions: str = ""
    numerical_verification: NumericalVerification | None = None

    @property
    def has_suggestions(self) -> bool:
        return bool(self.suggestions.strip())


class GeneratedQuestion(BaseModel):
    question: Question
    solution: Solution
    difficulty_score: DifficultyScore
    verification: VerificationResult
    generation_meta: dict[str, object] = Field(default_factory=dict)


# ── Pipeline 中间模型 ─────────────────────────────────


class ParsedProblem(BaseModel):
    """TeX 解析器的中间表示"""

    problem_number: int
    problem_latex: str
    solution_latex: str = ""
    has_solution: bool = True
    source_file: str = ""
    exam_type: str = ""  # 形如 "零试" / "一试" / "奥赛" 等
    year: int = 0
    month: int | None = None
    difficulty_estimate: int = 5
    is_exercise: bool = False
    exercise_label: str = ""
    parent_problem_number: int | None = None


class TexExamMetadata(BaseModel):
    """TeX 文件的考试级元数据"""

    year: int
    exam_type: str = ""
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
    category: str = ""  # bracket, semantic, reference, numbering
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


# ── Pipeline Phase 1: 格式描述 + 拆分 ────────────────────


class ImageAsset(BaseModel):
    """文档中提取的图像资产"""

    asset_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    file_path: str = ""
    source_file: str = ""
    source_page: int | None = None
    context_text: str = ""
    description: str = ""
    linked_block_id: str | None = None
    linked_item_id: str | None = None


# ── Pipeline 关系模型（内存字段，不持久化）─────────────────


class ItemRelation(BaseModel):
    """条目间的一条关系（KnowledgePoint/Question 的 relations 字段使用）"""

    relation_type: RelationType = RelationType.RELATED
    target_id: str = ""
    target_title: str = ""
    confidence: float = 1.0
    source: Literal["llm", "structural", "manual"] = "llm"
    note: str = ""


# ── Pipeline Producer-Consumer 消息 ───────────────────────


class MdBlockSource(BaseModel):
    """Producer (Task0) 输出的单个 5 页批次，由 Producer 推入 queue"""

    file_path: str  # logical document 中的源文件
    page_start: int  # 1-based
    page_end: int  # 1-based, inclusive
    md_path: str  # checkpoint dir 中的 page_XXX-XXX.md 路径（字符串以便 JSON 序列化）
    md_text: str  # 已读取的 markdown 内容
    block_index: int  # 在该 logical document 中的全局序号 (从 0 起)


class FileExtractionState(BaseModel):
    """Pipeline 断点续传的核心状态对象（Producer + Consumer 共用）"""

    file_path: str = ""
    file_hash: str = ""
    phase: Literal["running", "completed"] = "running"

    # ── Producer (Task0) 端 ──
    # 已识别完成的 batch，元组 (file_idx, page_start, page_end)
    vlm_batches_done: list[tuple[int, int, int]] = Field(default_factory=list)
    failed_batches: list[tuple[int, int, int]] = Field(default_factory=list)
    task0_completed: bool = False  # Producer 无错误完成所有批次后置 True，用于直通判定

    # ── Consumer (Task1) 端 ──
    current_block_idx: int = 0  # 已完成入库的最后一个 block_index + 1
    line_offset: int = 0  # 已消费 + commit 的全局行数
    line_buffer_carry: list[str] = Field(default_factory=list)  # buffer 中已注入行号的剩余行
    order_index_next: int = 0
    tag_tree_json: str = ""
    saved_item_count: int = 0
    failed_blocks: list[int] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    started_at: datetime = Field(default_factory=datetime.now)
    last_checkpoint_at: datetime | None = None


class StoredItemBatch(BaseModel):
    """Task1 一次循环提取并入库的产物（用于日志/进度追踪）"""

    chunk_index: int = 0
    line_range: tuple[int, int] = (0, 0)
    kp_count: int = 0
    question_count: int = 0
    order_index_range: tuple[int, int] = (0, 0)


class DirectoryProgress(BaseModel):
    """目录级 pipeline 进度追踪"""

    dir_path: str = ""
    completed_files: dict[str, str] = Field(default_factory=dict)  # file_path → file_hash
    failed_files: dict[str, str] = Field(default_factory=dict)  # file_path → error
    started_at: datetime = Field(default_factory=datetime.now)


# ── 讲义 (DocAssembly Agent 输出) ─────────────────────


class LectureSection(BaseModel):
    """讲义的一节"""

    title: str = ""
    intro: str = ""  # 引言/动机
    knowledge_point_ids: list[str] = Field(default_factory=list)
    worked_example_ids: list[str] = Field(default_factory=list)  # 作为例题的 Question id
    exercise_ids: list[str] = Field(default_factory=list)  # 作为练习的 Question id
    notes: str = ""  # Agent 串讲文本


class Lecture(BaseModel):
    """AI 生成的讲义 (DocAssemblyAgent lite 版输出)"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    discipline: Discipline = Discipline.MATH
    topic_tags: list[str] = Field(default_factory=list)
    sections: list[LectureSection] = Field(default_factory=list)
    target_audience: str = ""  # "高中" / "大学一年级"
    estimated_duration_min: int = 60
    generated_at: datetime = Field(default_factory=datetime.now)
    generation_meta: dict[str, object] = Field(default_factory=dict)


# ── 前向引用解析 ──────────────────────────────────────
# KnowledgePoint/Question 引用了后续定义的 ItemRelation
KnowledgePoint.model_rebuild()
Question.model_rebuild()
