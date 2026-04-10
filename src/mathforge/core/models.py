"""MathForge 核心数据模型 (Pydantic v2)"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

# ── 枚举 ──────────────────────────────────────────────


class Subject(StrEnum):
    ANALYSIS = "数学分析"
    LINEAR_ALGEBRA = "高等代数"
    GROUP_THEORY = "群论"
    COMBINATORICS = "组合数学"
    NUMBER_THEORY = "数论"
    TOPOLOGY = "拓扑学"
    COMPLEX_ANALYSIS = "复分析"
    PROBABILITY = "概率论"
    REAL_ANALYSIS = "实分析"
    FUNCTIONAL_ANALYSIS = "泛函分析"
    DIFFERENTIAL_EQUATIONS = "常微分方程"
    OTHER = "其他"


class QuestionType(StrEnum):
    CALCULATION = "calculation"
    PROOF = "proof"
    CONSTRUCTION = "construction"
    TRUE_FALSE = "true_false"
    CHOICE = "choice"
    FILL_BLANK = "fill_blank"


class KPType(StrEnum):
    DEFINITION = "definition"
    THEOREM = "theorem"
    LEMMA = "lemma"
    COROLLARY = "corollary"
    PROPOSITION = "proposition"
    EXAMPLE = "example"
    REMARK = "remark"
    PROOF_TECHNIQUE = "proof_technique"
    FORMULA = "formula"
    METHOD = "method"


class VariantStrategy(StrEnum):
    PARAMETER_SHIFT = "A1_parameter_shift"
    METHOD_SHIFT = "A2_method_shift"
    TYPE_SHIFT = "A3_type_shift"
    ESCALATE = "A4_escalate"
    SIMPLIFY = "A5_simplify"
    REVERSE = "A6_reverse"
    TOPIC_GENERATION = "B1_topic_generation"
    CROSS_TOPIC = "B2_cross_topic"


class ExamType(StrEnum):
    LINGSHI = "零试"
    YISHI = "一试"
    NVZI = "女子"


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


class SubjectDomain(StrEnum):
    MATH = "math"
    PHYSICS = "physics"
    CS = "cs"


class EducationLevel(StrEnum):
    HIGH_SCHOOL = "high_school"
    UNDERGRADUATE = "undergraduate"
    GRADUATE = "graduate"


class PedagogicalLevel(StrEnum):
    BASIC = "basic"
    STANDARD = "standard"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    COMPETITION = "competition"


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
    """知识点存储模型 — 与 DB schema 一一对应"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    kp_type: KPType = KPType.THEOREM
    number: str = ""
    content: str = ""  # LaTeX+文本混排，唯一内容字段
    proof_content: str | None = None  # 证明文本（定理/引理/命题）
    proof_methods: list[str] = Field(default_factory=list)
    difficulty: int = Field(ge=1, le=10, default=5)
    subject: str = ""
    taxonomy_path: str = ""
    tags: list[str] = Field(default_factory=list)  # ["group_theory:群论", ...]
    relations: list[ItemRelation] = Field(default_factory=list)
    source_file: str = ""
    source_chapter: str = ""
    source_section: str = ""
    quality_grade: str = "B"
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(validate_assignment=True)


class Question(BaseModel):
    """题目存储模型 — solution 直接嵌入，不再有独立 solutions 表"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question_type: QuestionType = QuestionType.PROOF
    number: str = ""
    content: str = ""  # 题目内容
    solution_content: str | None = None  # 解答文本
    solution_methods: list[str] = Field(default_factory=list)
    answer: str = ""  # 简答（选择题答案、填空答案等）
    difficulty: int = Field(ge=1, le=10, default=5)
    subject: str = ""
    taxonomy_path: str = ""
    tags: list[str] = Field(default_factory=list)
    relations: list[ItemRelation] = Field(default_factory=list)
    source_file: str = ""
    source_chapter: str = ""
    exam_type: str | None = None
    exam_year: int | None = None
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
    parameters: dict = Field(default_factory=dict)


class ToolCall(BaseModel):
    """LLM 发出的工具调用"""

    tool_name: str
    arguments: dict = Field(default_factory=dict)
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

    test_cases: list[dict] = Field(default_factory=list)
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


# ── 层级标签 ────────────────────────────────────────────


class HierarchicalTag(BaseModel):
    """层级标签，支持多级标签路径"""

    full_path: str
    segments: list[str] = Field(default_factory=list)
    depth: int = 0

    def __init__(self, **data: object) -> None:
        super().__init__(**data)
        if not self.segments and self.full_path:
            self.segments = self.full_path.split("/")
        if not self.depth:
            self.depth = len(self.segments)

    model_config = ConfigDict(validate_assignment=True)


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
                    f"### 知识点 {i}: {kp.title} [{kp.kp_type.value}] "
                    f"(难度 {kp.difficulty}/10)\n{kp.content_latex}\n"
                )
        if self.reference_questions:
            sections.append("\n## 参考题目（供参考，不要照搬）\n")
            for i, q in enumerate(self.reference_questions, 1):
                sections.append(f"### 参考题 {i} (难度 {q.difficulty}/10)\n{q.content_latex}\n")
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
    numerical_verification: NumericalVerification | None = None

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


# ── Pipeline Phase 1: 文档结构与条目提取 ──────────────────


class StructuralContext(BaseModel):
    """Task1 输出：当前 chunk 的文档层级上下文"""

    chapter_title: str = ""
    chapter_number: str = ""
    section_title: str = ""
    section_number: str = ""
    subsection_title: str = ""
    subsection_number: str = ""
    document_type: str = ""  # textbook / lecture_notes / exam_paper


class ExtractedItem(BaseModel):
    """Task1 LLM 直接提取的条目

    proof/solution 直接嵌入父条目，不再拆分为独立 item。
    """

    item_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    item_type: str = ""  # definition/theorem/lemma/corollary/proposition/example/exercise/problem
    number: str | None = None
    content: str = ""  # 主内容（定理陈述/题目描述）
    proof: str | None = None  # 证明文本（定理/引理/命题的配套）
    solution: str | None = None  # 解答文本（例题/习题的配套）
    line_start: int = 0
    line_end: int = 0
    source_chunk_id: str = ""
    source_file: str = ""
    structural_context: StructuralContext = Field(default_factory=StructuralContext)
    raw_metadata: dict = Field(default_factory=dict)


class ChunkExtractionResult(BaseModel):
    """QueueA 消息类型：一个 chunk 的提取结果"""

    chunk_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    structural_context: StructuralContext = Field(default_factory=StructuralContext)
    items: list[ExtractedItem] = Field(default_factory=list)
    source_file: str = ""
    source_format: DocumentFormat = DocumentFormat.MARKDOWN
    line_start: int = 0
    line_end: int = 0
    chunk_index: int = 0
    raw_metadata: dict = Field(default_factory=dict)


# ── Pipeline Phase 1.5: 文档结构树 ──────────────────────


class TaxonomyNode(BaseModel):
    """文档结构树的节点"""

    node_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    level: Literal["book", "chapter", "section", "subsection"] = "book"
    title: str = ""
    number: str = ""
    parent_id: str | None = None
    children_ids: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    source_file: str = ""
    position: int = 0  # 在父节点下的排序位置


class DocumentTree(BaseModel):
    """完整的文档结构树"""

    root_id: str = ""
    nodes: dict[str, TaxonomyNode] = Field(default_factory=dict)
    item_to_node: dict[str, str] = Field(default_factory=dict)  # item_id → node_id


class MdBlockV2(MdBlock):
    """扩展 MdBlock，新增 Pipeline 所需字段（兼容旧 parser）"""

    block_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_format: DocumentFormat = DocumentFormat.MARKDOWN
    block_index: int = 0
    total_blocks: int | None = None
    chapter_title: str = ""
    section_title: str = ""
    preceding_context: str = ""
    image_assets: list[ImageAsset] = Field(default_factory=list)
    raw_metadata: dict = Field(default_factory=dict)


# ── Pipeline Phase 2: 条目提取 ───────────────────────────


class ContentMarkers(BaseModel):
    """内容类型标记 — 公式/表格/图片的检测计数"""

    formula_count: int = 0
    table_count: int = 0
    image_count: int = 0
    has_display_math: bool = False
    has_cjk: bool = False


class RawItem(BaseModel):
    """环节 2 输出：从 MdBlock 中提取的原始条目"""

    item_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    block_type: str = ""
    number: str | None = None
    title: str | None = None
    content_raw: str = ""
    content_latex: str = ""
    line_start: int = 0
    line_end: int = 0
    paired_item_id: str | None = None
    source_block_id: str = ""
    source_file: str = ""
    extraction_mode: Literal["explicit", "implicit"] = "implicit"
    linked_image_ids: list[str] = Field(default_factory=list)
    raw_metadata: dict = Field(default_factory=dict)
    content_markers: ContentMarkers = Field(default_factory=ContentMarkers)


class ExtractionReport(BaseModel):
    """单 block 提取质量报告"""

    block_id: str = ""
    strategy_used: Literal["spec", "heuristic", "fallback"] = "spec"
    items_found: int = 0
    coverage_ratio: float = 0.0
    quality_score: float = 0.0
    issues: list[str] = Field(default_factory=list)


# ── Pipeline Phase 3: 校准 + 统一 + 标注 + 关系 ──────────


class CorrectionEntry(BaseModel):
    """单处修正记录"""

    original: str = ""
    corrected: str = ""
    reason: str = ""
    category: Literal["ocr", "typo", "math_symbol", "semantic", "logic", "formatting"] = "ocr"


class CorrectionResult(BaseModel):
    """单条目校准结果"""

    corrected_content: str = ""
    corrected_latex: str = ""
    corrections: list[CorrectionEntry] = Field(default_factory=list)
    semantic_issues: list[str] = Field(default_factory=list)
    logic_issues: list[str] = Field(default_factory=list)
    is_answer_verified: bool | None = None
    completeness_score: float = 1.0
    overall_quality: Literal["A", "B", "C", "D", "F"] = "B"


class TaxonomyPath(BaseModel):
    """知识点层级分类路径 (dot 分隔)"""

    domain: str = "math"
    branch: str = ""
    topic: str = ""
    subtopic: str | None = None
    specific: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_path(self) -> str:
        parts = [self.domain, self.branch, self.topic]
        if self.subtopic:
            parts.append(self.subtopic)
        if self.specific:
            parts.append(self.specific)
        return ".".join(p for p in parts if p)


class TagSet(BaseModel):
    """条目的完整标注集 — 5 层从粗到细"""

    domain: SubjectDomain = SubjectDomain.MATH
    subject: Subject = Subject.ANALYSIS
    taxonomy: TaxonomyPath = Field(default_factory=TaxonomyPath)
    education_level: EducationLevel = EducationLevel.UNDERGRADUATE
    pedagogical_level: PedagogicalLevel = PedagogicalLevel.STANDARD
    methods: list[str] = Field(default_factory=list)
    tested_topics: list[str] = Field(default_factory=list)
    custom_tags: list[str] = Field(default_factory=list)


class ItemRelation(BaseModel):
    """条目间的一条关系"""

    relation_type: RelationType = RelationType.RELATED
    target_id: str = ""
    target_title: str = ""
    confidence: float = 1.0
    source: Literal["llm", "structural", "manual"] = "llm"
    note: str = ""


class QualityAssessment(BaseModel):
    """内容质量评估"""

    grade: Literal["A", "B", "C", "D", "F"] = "B"
    completeness: float = 1.0
    correctness: float = 1.0
    clarity: float = 1.0
    issues: list[str] = Field(default_factory=list)


class ItemSource(BaseModel):
    """统一来源追溯"""

    file_path: str = ""
    block_id: str = ""
    line_range: tuple[int, int] | None = None
    page_range: tuple[int, int] | None = None
    source_type: str = ""
    book_title: str | None = None
    chapter: str | None = None
    section: str | None = None
    exam_type: ExamType | None = None
    year: int | None = None
    extraction_method: str = "meta_extractor"
    # 溯源扩展
    extraction_strategy: str = ""  # "spec", "heuristic", "fallback"
    extraction_score: float = 0.0
    correction_count: int = 0
    original_quality: str = ""  # 校正前质量评级


class ProofContent(BaseModel):
    """证明内容（嵌套在定理类中）"""

    proof_latex: str = ""
    proof_plain: str = ""
    methods_used: list[str] = Field(default_factory=list)
    step_count: int = 0
    is_complete: bool = True


class SolutionContent(BaseModel):
    """解答内容（嵌套在题目类中）"""

    solution_latex: str = ""
    solution_plain: str = ""
    methods_used: list[str] = Field(default_factory=list)
    step_count: int = 0
    key_steps: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"
    is_verified: bool = False


# ── UnifiedItem 继承体系 ─────────────────────────────────


class UnifiedItemBase(BaseModel):
    """所有统一条目的抽象基类"""

    item_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content_latex: str = ""
    content_plain: str = ""
    content_markdown: str = ""
    title: str = ""
    number: str | None = None
    source: ItemSource = Field(default_factory=ItemSource)
    tags: TagSet | None = None
    relations: list[ItemRelation] = Field(default_factory=list)
    quality: QualityAssessment | None = None
    images: list[ImageAsset] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(validate_assignment=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def item_category(self) -> str:
        return "base"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_images(self) -> bool:
        return len(self.images) > 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_length(self) -> int:
        return len(self.content_plain or self.content_latex)


# ── 知识点类 ──


class KnowledgeItem(UnifiedItemBase):
    """所有知识点条目 — 通过 kp_type 枚举区分具体类型"""

    kp_type: KPType = KPType.THEOREM
    subject_domain: SubjectDomain = SubjectDomain.MATH
    subject: Subject = Subject.ANALYSIS
    education_level: EducationLevel = EducationLevel.UNDERGRADUATE
    difficulty: int = Field(ge=1, le=10, default=5)
    prerequisites: list[str] = Field(default_factory=list)
    # 定理族字段（THEOREM/LEMMA/COROLLARY/PROPOSITION 可用）
    proof: ProofContent | None = None
    parent_theorem_id: str | None = None  # 推论的父定理
    # 孤儿配对追踪
    intended_parent_type: str = ""
    intended_parent_number: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def item_category(self) -> str:
        return "knowledge"


# ── 题目类 ──


class QuestionItem(UnifiedItemBase):
    """所有题目条目 — 通过 question_type 枚举区分具体类型"""

    question_type: QuestionType = QuestionType.PROOF
    subject_domain: SubjectDomain = SubjectDomain.MATH
    subject: Subject = Subject.ANALYSIS
    education_level: EducationLevel = EducationLevel.UNDERGRADUATE
    difficulty: int = Field(ge=1, le=10, default=5)
    difficulty_score: DifficultyScore | None = None
    solution: SolutionContent | None = None
    answer: str = ""
    tested_knowledge_ids: list[str] = Field(default_factory=list)
    methods_required: list[str] = Field(default_factory=list)
    # 选择题字段
    options: dict[str, str] = Field(default_factory=dict)
    correct_option: str = ""
    is_multiple: bool = False
    # 填空题字段
    blanks: list[str] = Field(default_factory=list)
    blank_count: int = 0
    # 判断题字段
    correct_answer: bool | None = None
    counterexample: str = ""
    # 孤儿配对追踪
    intended_parent_type: str = ""
    intended_parent_number: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def item_category(self) -> str:
        return "question"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def has_solution(self) -> bool:
        return self.solution is not None


# ── 例题类 ──


class ExampleItem(UnifiedItemBase):
    """例题/示例 — 同时具有知识点和题目属性"""

    kp_type: KPType = KPType.EXAMPLE
    subject_domain: SubjectDomain = SubjectDomain.MATH
    subject: Subject = Subject.ANALYSIS
    education_level: EducationLevel = EducationLevel.UNDERGRADUATE
    difficulty: int = Field(ge=1, le=10, default=5)
    solution: SolutionContent | None = None
    illustrates_ids: list[str] = Field(default_factory=list)
    methods_demonstrated: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def item_category(self) -> str:
        return "example"


# ── Pipeline 处理结果 + 进度 ──────────────────────────────


class ItemProcessingResult(BaseModel):
    """单次 LLM 调用的合并结果：校准 + 分类"""

    correction: CorrectionResult = Field(default_factory=CorrectionResult)
    classification: TagSet = Field(default_factory=TagSet)
    item_subtype: str = ""
    difficulty: int = Field(ge=1, le=10, default=5)


class PipelineRunProgress(BaseModel):
    """Pipeline 运行进度（checkpoint/resume 用）"""

    file_path: str = ""
    file_hash: str = ""
    stage: str = "not_started"  # not_started|vlm_convert|chunk_extract|process_tree|graph|completed
    blocks_completed: int = 0
    completed_block_ids: list[str] = Field(default_factory=list)  # chunk IDs
    items_extracted: int = 0
    items_processed: int = 0
    items_stored: int = 0
    errors: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=datetime.now)
    last_checkpoint_at: datetime | None = None
    tree_state: str | None = None  # JSON-serialized DocumentTree


class DirectoryProgress(BaseModel):
    """目录级 pipeline 进度追踪"""

    dir_path: str = ""
    completed_files: dict[str, str] = Field(default_factory=dict)  # file_path → file_hash
    failed_files: dict[str, str] = Field(default_factory=dict)  # file_path → error
    started_at: datetime = Field(default_factory=datetime.now)


# ── 前向引用解析 ──────────────────────────────────────
# KnowledgePoint/Question 引用了后续定义的 ItemRelation/SolutionContent
KnowledgePoint.model_rebuild()
Question.model_rebuild()
