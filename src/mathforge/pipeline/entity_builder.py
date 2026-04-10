"""EntityBuilder — ExtractedItem + ItemProcessingResult → KnowledgePoint / Question

直接从提取结果构建存储实体，不经过 UnifiedItemBase 中间层。
"""

from __future__ import annotations

import structlog

from mathforge.core.models import (
    ExtractedItem,
    ItemProcessingResult,
    KnowledgePoint,
    KPType,
    Question,
    QuestionType,
)

logger = structlog.get_logger()

# 知识类 item_type → KPType 映射
_KP_TYPE_MAP: dict[str, KPType] = {
    "definition": KPType.DEFINITION,
    "theorem": KPType.THEOREM,
    "lemma": KPType.LEMMA,
    "corollary": KPType.COROLLARY,
    "proposition": KPType.PROPOSITION,
    "property": KPType.PROPOSITION,
    "formula": KPType.FORMULA,
    "example": KPType.EXAMPLE,
}

# 题目类 question_type 映射
_Q_TYPE_MAP: dict[str, QuestionType] = {
    "proof": QuestionType.PROOF,
    "calculation": QuestionType.CALCULATION,
    "choice": QuestionType.CHOICE,
    "fill_blank": QuestionType.FILL_BLANK,
    "true_false": QuestionType.TRUE_FALSE,
    "construction": QuestionType.PROOF,
}

# 哪些 item_type 是题目
_QUESTION_TYPES = {"example", "exercise", "problem"}


def build_entities(
    items: list[tuple[ExtractedItem, ItemProcessingResult]],
    source_file: str = "",
    source_chapter: str = "",
    source_section: str = "",
) -> tuple[list[KnowledgePoint], list[Question]]:
    """从提取+处理结果直接构建存储实体"""
    kps: list[KnowledgePoint] = []
    questions: list[Question] = []

    for ei, pr in items:
        if pr.correction.overall_quality == "F":
            continue

        content = pr.correction.corrected_content or ei.content
        tags = pr.tags if pr.tags else []
        difficulty = pr.difficulty

        if ei.item_type in _QUESTION_TYPES:
            q_type_str = pr.question_type or ei.item_type
            q_type = _Q_TYPE_MAP.get(q_type_str, QuestionType.PROOF)

            q = Question(
                id=ei.item_id,
                question_type=q_type,
                number=ei.number or "",
                content=content,
                solution_content=pr.corrected_solution or ei.solution,
                solution_methods=pr.methods,
                difficulty=difficulty,
                subject=pr.subject,
                taxonomy_path=pr.taxonomy_path,
                tags=tags,
                source_file=source_file or ei.source_file,
                source_chapter=source_chapter,
                quality_grade=pr.correction.overall_quality,
            )
            if ei.raw_metadata:
                q.exam_type = ei.raw_metadata.get("exam_type")
                q.exam_year = ei.raw_metadata.get("year")
            questions.append(q)
        else:
            kp_type_str = pr.item_subtype or ei.item_type
            kp_type = _KP_TYPE_MAP.get(kp_type_str, KPType.THEOREM)

            kp = KnowledgePoint(
                id=ei.item_id,
                kp_type=kp_type,
                number=ei.number or "",
                content=content,
                proof_content=pr.corrected_proof or ei.proof,
                proof_methods=pr.methods,
                difficulty=difficulty,
                subject=pr.subject,
                taxonomy_path=pr.taxonomy_path,
                tags=tags,
                source_file=source_file or ei.source_file,
                source_chapter=source_chapter,
                source_section=source_section,
                quality_grade=pr.correction.overall_quality,
            )
            kps.append(kp)

    logger.info("entities_built", kps=len(kps), questions=len(questions))
    return kps, questions
