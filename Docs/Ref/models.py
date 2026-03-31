"""
MathForge 核心数据模型
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ── 枚举 ──────────────────────────────────────────────

class Subject(str, Enum):
    ANALYSIS = "数学分析"
    LINEAR_ALGEBRA = "高等代数"
    GROUP_THEORY = "群论"


class QuestionType(str, Enum):
    CALCULATION = "calculation"    # 计算题
    PROOF = "proof"                # 证明题
    CONSTRUCTION = "construction"  # 构造题
    TRUE_FALSE = "true_false"      # 判断/辨析题
    CHOICE = "choice"              # 选择题


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


class VerifyStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


# ── 核心实体 ──────────────────────────────────────────

@dataclass
class Tag:
    name: str
    category: str           # subject / topic / method / type
    parent: Optional[str] = None


@dataclass
class KnowledgePoint:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    content_latex: str = ""
    content_plain: str = ""
    kp_type: KPType = KPType.THEOREM
    difficulty: int = 5
    tags: list[str] = field(default_factory=list)
    prerequisites: list[str] = field(default_factory=list)
    source_book: str = ""
    source_chapter: str = ""
    embedding: Optional[list[float]] = None


@dataclass
class Question:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    content_latex: str = ""
    content_plain: str = ""
    difficulty: int = 5
    difficulty_ai: float = 5.0
    question_type: QuestionType = QuestionType.PROOF
    subject: Subject = Subject.ANALYSIS
    tags: list[str] = field(default_factory=list)
    source_book: str = ""
    source_number: str = ""
    is_generated: bool = False
    parent_id: Optional[str] = None
    embedding: Optional[list[float]] = None
    generation_meta: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class Solution:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    question_id: str = ""
    solution_latex: str = ""
    methods_used: list[str] = field(default_factory=list)
    step_count: int = 0
    confidence: str = "medium"       # high / medium / low
    is_verified: bool = False


@dataclass
class DifficultyScore:
    """多维难度评分"""
    concept_count: int = 5
    step_count: int = 5
    abstraction_level: int = 5
    technique_novelty: int = 5
    condition_parsing: int = 5
    computation_load: int = 5
    proof_structure: int = 5
    prerequisite_depth: int = 5
    multi_representation: int = 5
    metacognition: int = 5
    overall: float = 5.0
    reasoning: str = ""

    WEIGHTS = {
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

@dataclass
class GenerationRequest:
    subject: Subject
    topics: list[str]
    difficulty_target: int                        # 1-10
    question_type: QuestionType
    strategy: VariantStrategy
    source_question: Optional[Question] = None    # 变式模式下非空
    exclusion_ids: set[str] = field(default_factory=set)
    count: int = 1


@dataclass
class ContextBundle:
    """传递给 LLM 的完整上下文"""
    knowledge_points: list[KnowledgePoint] = field(default_factory=list)
    reference_questions: list[Question] = field(default_factory=list)
    negative_examples: list[Question] = field(default_factory=list)
    feedback_from_previous_round: str = ""

    def to_prompt_block(self) -> str:
        sections = []

        if self.knowledge_points:
            sections.append("## 相关知识点\n")
            for i, kp in enumerate(self.knowledge_points, 1):
                sections.append(
                    f"### 知识点 {i}: {kp.title} [{kp.kp_type.value}] "
                    f"(难度 {kp.difficulty}/10)\n{kp.content_latex}\n"
                )

        if self.reference_questions:
            sections.append("\n## 同领域参考题目（供参考风格和难度，不要照搬）\n")
            for i, q in enumerate(self.reference_questions, 1):
                sections.append(
                    f"### 参考题 {i} (难度 {q.difficulty}/10, "
                    f"类型: {q.question_type.value})\n{q.content_latex}\n"
                )

        if self.negative_examples:
            sections.append(
                "\n## ⚠ 以下题目已存在于题库中，你的新题必须与它们有实质性区别\n"
            )
            for i, q in enumerate(self.negative_examples, 1):
                sections.append(f"### 已有题 {i}\n{q.content_latex}\n")

        if self.feedback_from_previous_round:
            sections.append(
                f"\n## 上一轮生成的反馈（请据此改进）\n{self.feedback_from_previous_round}\n"
            )

        return "\n".join(sections)


@dataclass
class VerificationResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    suggestions: str = ""

    @property
    def has_suggestions(self) -> bool:
        return bool(self.suggestions.strip())


@dataclass
class GeneratedQuestion:
    question: Question
    solution: Solution
    difficulty_score: DifficultyScore
    verification: VerificationResult
    generation_meta: dict = field(default_factory=dict)
