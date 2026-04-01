"""EntityBuilder — UnifiedItem → 最终持久化实体

将 UnifiedItem 继承体系转化为核心实体：
  KnowledgeItem → KnowledgePoint
  QuestionItem → Question + Solution
  ExampleItem → KnowledgePoint(kp_type=EXAMPLE)
"""

from __future__ import annotations

import structlog

from mathforge.core.models import (
    ExampleItem,
    KnowledgeItem,
    KnowledgePoint,
    KPSource,
    KPType,
    Question,
    QuestionItem,
    QuestionSource,
    Solution,
    Subject,
    UnifiedItemBase,
)

logger = structlog.get_logger()


def build_entities(
    items: list[UnifiedItemBase],
) -> tuple[list[KnowledgePoint], list[tuple[Question, Solution | None]]]:
    """将 UnifiedItem 列表转化为最终持久化实体"""
    knowledge_points: list[KnowledgePoint] = []
    question_pairs: list[tuple[Question, Solution | None]] = []

    for item in items:
        try:
            if isinstance(item, QuestionItem):
                q, sol = _build_question(item)
                question_pairs.append((q, sol))
            elif isinstance(item, ExampleItem):
                kp = _build_kp_from_example(item)
                knowledge_points.append(kp)
            elif isinstance(item, KnowledgeItem):
                kp = _build_kp(item)
                knowledge_points.append(kp)
            else:
                # 未知类型，按知识点处理
                kp = _build_kp_generic(item)
                knowledge_points.append(kp)
        except Exception:
            logger.error(
                "entity_build_failed",
                item_id=item.item_id,
                item_type=type(item).__name__,
                exc_info=True,
            )

    logger.info(
        "entities_built",
        knowledge_points=len(knowledge_points),
        questions=len(question_pairs),
    )

    return knowledge_points, question_pairs


def _build_kp(item: KnowledgeItem) -> KnowledgePoint:
    """KnowledgeItem → KnowledgePoint"""
    tags = []
    if item.tags:
        tags = list(item.tags.methods) + list(item.tags.custom_tags)

    prerequisites = list(item.prerequisites)
    # 从 relations 中提取 prerequisite
    for rel in item.relations:
        if rel.relation_type.value == "prerequisite" and rel.target_id not in prerequisites:
            prerequisites.append(rel.target_id)

    source = KPSource(
        source_file=item.source.file_path if item.source else None,
        chapter=item.source.chapter if item.source else None,
        section=item.source.section if item.source else None,
        line_range=item.source.line_range if item.source else None,
    )

    return KnowledgePoint(
        id=item.item_id,
        title=item.title,
        content_latex=item.content_latex,
        content_plain=item.content_plain,
        kp_type=item.kp_type,
        difficulty=item.difficulty,
        tags=tags,
        prerequisites=prerequisites,
        source=source,
    )


def _build_kp_from_example(item: ExampleItem) -> KnowledgePoint:
    """ExampleItem → KnowledgePoint(kp_type=EXAMPLE)"""
    tags = []
    if item.tags:
        tags = list(item.tags.methods) + list(item.tags.custom_tags)

    source = KPSource(
        source_file=item.source.file_path if item.source else None,
        chapter=item.source.chapter if item.source else None,
        section=item.source.section if item.source else None,
        line_range=item.source.line_range if item.source else None,
    )

    content = item.content_latex
    if item.solution:
        content += f"\n\n**解答**:\n{item.solution.solution_latex}"

    return KnowledgePoint(
        id=item.item_id,
        title=item.title,
        content_latex=content,
        content_plain=item.content_plain,
        kp_type=KPType.EXAMPLE,
        difficulty=item.difficulty,
        tags=tags,
        prerequisites=list(item.illustrates_ids),
        source=source,
    )


def _build_question(item: QuestionItem) -> tuple[Question, Solution | None]:
    """QuestionItem → Question + Solution"""
    tags = []
    if item.tags:
        tags = list(item.tags.methods) + list(item.tags.custom_tags)

    # 构建来源
    q_source = QuestionSource(
        exam_type=item.source.exam_type if item.source else None,
        year=item.source.year if item.source else None,
    )
    if item.source:
        q_source.book_title = item.source.book_title
        q_source.chapter = item.source.chapter

    try:
        subject = item.subject
    except Exception:
        subject = Subject.ANALYSIS

    question = Question(
        id=item.item_id,
        content_latex=item.content_latex,
        content_plain=item.content_plain,
        difficulty=item.difficulty,
        question_type=item.question_type,
        subject=subject,
        tags=tags,
        source=q_source,
        is_generated=False,
    )

    # 构建 Solution
    solution: Solution | None = None
    if item.solution:
        solution = Solution(
            question_id=item.item_id,
            solution_latex=item.solution.solution_latex,
            methods_used=list(item.solution.methods_used),
            step_count=item.solution.step_count,
            confidence=item.solution.confidence,
            is_verified=item.solution.is_verified,
        )

    return question, solution


def _build_kp_generic(item: UnifiedItemBase) -> KnowledgePoint:
    """通用 fallback：未知类型 → KnowledgePoint"""
    tags = []
    if item.tags:
        tags = list(item.tags.methods) + list(item.tags.custom_tags)

    source = KPSource(
        source_file=item.source.file_path if item.source else None,
        line_range=item.source.line_range if item.source else None,
    )

    return KnowledgePoint(
        id=item.item_id,
        title=item.title,
        content_latex=item.content_latex,
        content_plain=item.content_plain,
        kp_type=KPType.REMARK,
        difficulty=5,
        tags=tags,
        source=source,
    )
