"""SciForge 核心数据模型 (Pydantic v2)"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sciforge.core.numbering import canonicalize_number, parse_number_components

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
    """知识点存储模型 — 与 DB schema v7 一一对应"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    discipline: Discipline = Discipline.MATH
    kp_type: str = "theorem"  # 由 DisciplineSpec.allowed_kp_types 约束
    number: str = ""  # 原文逐字题号（如 "例 3.2" / "第1题"）
    number_canonical: str = ""  # 规范化题号（如 "3.2"）；由 number 自动派生
    seq_chapter: int | None = None  # 结构化序号：章（编号连续性 audit 用）
    seq_section: int | None = None  # 节
    seq_index: int | None = None  # 章/节内运行序号
    content: str = ""
    proof_content: str | None = None
    difficulty: int = Field(ge=1, le=10, default=5)
    subject: str = ""
    taxonomy_path: str = ""
    tags: list[str] = Field(default_factory=list)  # 有序段 ["math:数学", "algebra:代数", ...]
    relations: list[ItemRelation] = Field(default_factory=list)  # 内存字段，不持久化
    source_file: str = ""  # 源 logical_document 的文件路径（首个分卷路径，便于追溯）
    start_line: int = 0
    end_line: int = 0
    order_index: int = 0

    model_config = ConfigDict(validate_assignment=True)

    @model_validator(mode="after")
    def _derive_numbering(self) -> KnowledgePoint:
        """从原文 number 自动派生 number_canonical + seq_*（仅当未显式提供时）。

        用 object.__setattr__ 绕过 validate_assignment 的递归校验。
        """
        if self.number and not self.number_canonical:
            object.__setattr__(self, "number_canonical", canonicalize_number(self.number))
        if self.number and self.seq_index is None:
            ch, sec, idx = parse_number_components(self.number)
            object.__setattr__(self, "seq_chapter", ch)
            object.__setattr__(self, "seq_section", sec)
            object.__setattr__(self, "seq_index", idx)
        return self


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
    """题目存储模型 — 与 DB schema v9 一一对应"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    discipline: Discipline = Discipline.MATH
    # question_type = 认知类别（proof/calculation/construction/...），由 DisciplineSpec 约束
    question_type: str = "proof"
    # question_form = 题目形式（choice/fill_blank/true_false/short_answer/...）；
    # 与 question_type 正交：一道选择题在形式上是 choice、认知类别上可能是 calculation。
    # Phase 4 在提取阶段填充；Phase 0 仅建列，默认空。
    question_form: str = ""
    number: str = ""  # 原文逐字题号
    number_canonical: str = ""  # 规范化题号；由 number 自动派生
    seq_chapter: int | None = None
    seq_section: int | None = None
    seq_index: int | None = None
    content: str = ""
    solution_content: str | None = None
    difficulty: int = Field(ge=1, le=10, default=5)
    subject: str = ""
    taxonomy_path: str = ""
    tags: list[str] = Field(default_factory=list)
    relations: list[ItemRelation] = Field(default_factory=list)  # 内存字段，不持久化
    source_file: str = ""  # 源 logical_document 的文件路径（首个分卷路径，便于追溯）
    start_line: int = 0
    end_line: int = 0
    order_index: int = 0
    # ── 生成元数据（v9）：区分生成题/手动题，支撑版权归属、计费与统计追溯 ──
    is_generated: bool = False
    generator_model: str = ""  # 生成所用模型 id（如 claude-opus-4-8）
    generated_at: datetime | None = None
    generation_session_id: str = ""  # 关联 EvolutionSession.session_id
    tenant_id: str = ""  # 多租户归属（共享后端用；私有化/单机恒空）

    model_config = ConfigDict(validate_assignment=True)

    @model_validator(mode="after")
    def _derive_numbering(self) -> Question:
        """从原文 number 自动派生 number_canonical + seq_*（仅当未显式提供时）。"""
        if self.number and not self.number_canonical:
            object.__setattr__(self, "number_canonical", canonicalize_number(self.number))
        if self.number and self.seq_index is None:
            ch, sec, idx = parse_number_components(self.number)
            object.__setattr__(self, "seq_chapter", ch)
            object.__setattr__(self, "seq_section", sec)
            object.__setattr__(self, "seq_index", idx)
        return self


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
    # Phase 6c：verdict=PASS 时可顺带返回 10 维难度评分（若 verifier 产出）
    # 启用 QGenAgent(merge_difficulty_into_verify=True) 时跳过独立 annotator 调用
    difficulty_score: DifficultyScore | None = None

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
    # 结构化边界（Phase 2）：regex 预检得到的 section/chapter 行号
    # 每项格式：(line_no, level, title) — line_no 是 block 内的 0-based 行号
    section_boundaries: list[tuple[int, int, str]] = Field(default_factory=list)


class FileExtractionState(BaseModel):
    """Pipeline 断点续传的核心状态对象（Task0 + 5 Stage 共用）

    5 阶段：Task0 → Stage1Extract → Stage1Review
    → Stage2Annotate → Stage2Review → Stage3Persist
    """

    file_path: str = ""
    file_hash: str = ""
    phase: Literal["running", "completed"] = "running"
    schema_version: int = 2  # 2 = 5-stage pipeline; 旧值 1/缺失视为不兼容

    # ── Producer (Task0) 端 ──
    vlm_batches_done: list[tuple[int, int, int]] = Field(default_factory=list)
    failed_batches: list[tuple[int, int, int]] = Field(default_factory=list)
    task0_completed: bool = False

    # ── Stage1Extract 端 ──
    current_block_idx: int = 0  # Stage1 已处理最后一个 block_index + 1
    line_offset: int = 0
    line_buffer_carry: list[str] = Field(default_factory=list)
    next_item_id: int = 0  # 全局自增 item id 分配器
    current_window_size: int = 500
    consecutive_empty: int = 0  # 连续 0 条提取次数

    # ── 各 Stage 计数/完成标记 ──
    stage1_extracted_count: int = 0
    stage1_reviewed_count: int = 0
    stage2_annotated_count: int = 0
    stage2_reviewed_count: int = 0
    stage3_persisted_count: int = 0
    stage3_deduped_count: int = 0  # Stage3 因内容重复跳过入库的条目数（#8 可观测）

    stage1_completed: bool = False
    stage2_completed: bool = False
    stage3_completed: bool = False

    last_persisted_item_id: int = -1  # Stage3 保序断言用；起始 -1 代表尚无入库
    failed_blocks: list[int] = Field(default_factory=list)  # Stage1 LLM 失败的 block
    out_of_order_items: list[int] = Field(default_factory=list)  # Stage3 乱序兜底

    tag_tree_json: str = ""  # Stage3 维护，跨 item 累积

    errors: list[str] = Field(default_factory=list)

    started_at: datetime = Field(default_factory=datetime.now)
    last_checkpoint_at: datetime | None = None


class StoredItemBatch(BaseModel):
    """Stage3 一次入库产物（日志/进度追踪）"""

    chunk_index: int = 0
    line_range: tuple[int, int] = (0, 0)
    kp_count: int = 0
    question_count: int = 0
    order_index_range: tuple[int, int] = (0, 0)


# ── 5 Stage Pipeline Payloads ─────────────────────────
# 注：这些类引用 pipeline.llm_schemas 中的 LLM schema 作为 item/annotation 字段。
# 由于 llm_schemas 不反向依赖 core.models，不会循环。

from sciforge.pipeline.llm_schemas import (  # noqa: E402
    SingleAnnotationLLM,
    SingleItemExtractLLM,
)  # noqa: E501


class ExtractedSingleItem(BaseModel):
    """Stage1Extract → q_raw → Stage1Review 的 payload（旧版单条单元，保留兼容）

    携带原始 window 行（含全局行号前缀）供 Stage1Review 对照校验。
    source_file 从 MdBlockSource.file_path 透传，一直带到 Stage3Persist 入库。
    """

    item_id: int = Field(ge=0)
    item: SingleItemExtractLLM
    window_lines: list[str] = Field(default_factory=list)
    window_line_range: tuple[int, int] = (0, 0)
    window_last_line: int = 0  # 供 Stage1Review 再次触发 exercise near-tail 校验
    block_index: int = 0
    source_file: str = ""
    page_start: int | None = None
    page_end: int | None = None


class ExtractedBatch(BaseModel):
    """Stage1Extract → q_raw → Stage1Review 的**批量** payload（v3 单次提取多条）

    一次 LLM 调用提取的 N 条 items 共享同一个 window 上下文。Stage1Review 用
    单次 LLM 调用对全部 items 做批量审核，然后按 item_id 顺序拆分推 q_rev。

    item_ids[i] 与 items[i] 一一对应。Stage1Extract 在分配 item_ids 时按
    items 顺序连续递增 next_item_id。
    """

    item_ids: list[int] = Field(default_factory=list)
    items: list[SingleItemExtractLLM] = Field(default_factory=list)
    window_lines: list[str] = Field(default_factory=list)
    window_line_range: tuple[int, int] = (0, 0)
    window_last_line: int = 0  # 供 Stage1Review 再次触发 exercise near-tail 校验
    block_index: int = 0
    source_file: str = ""
    page_start: int | None = None
    page_end: int | None = None
    # Phase 4 条件化 review：Stage1Extract 填入 LLM 的 stop_reason
    # 默认 "all_complete" 以兼容老 checkpoint 回放（无 stop_reason）
    stop_reason: str = "all_complete"


class ReviewedItem(BaseModel):
    """Stage1Review → q_rev → Stage2Annotate 的 payload"""

    item_id: int = Field(ge=0)
    item: SingleItemExtractLLM
    block_index: int = 0
    source_file: str = ""
    page_start: int | None = None
    page_end: int | None = None


class AnnotatedItem(BaseModel):
    """Stage2Annotate → q_ann → Stage2Review 的 payload"""

    item_id: int = Field(ge=0)
    item: SingleItemExtractLLM
    annotation: SingleAnnotationLLM
    block_index: int = 0
    source_file: str = ""
    page_start: int | None = None
    page_end: int | None = None


class ReviewedAnnotatedItem(BaseModel):
    """Stage2Review → q_done → Stage3Persist 的 payload"""

    item_id: int = Field(ge=0)
    item: SingleItemExtractLLM
    annotation: SingleAnnotationLLM
    block_index: int = 0
    source_file: str = ""
    page_start: int | None = None
    page_end: int | None = None


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
