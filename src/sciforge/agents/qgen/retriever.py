"""ContextRetriever — QGen 第 1 阶段：从 SQLite 检索上下文

相比参考实现的"内存列表"版本，这里改为 SQLiteStorageBackend 驱动：
1. 按 discipline + tag 过滤知识点
2. 按难度窗口进一步筛选
3. FTS5 全文检索补充相关条目
4. 难度锚点（一个偏简单 + 一个偏难）

返回 ContextBundle 给 QuestionGenerator 用。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sciforge.core.models import (
    ContextBundle,
    Discipline,
    GenerationRequest,
    KnowledgePoint,
    Question,
)

if TYPE_CHECKING:
    from sciforge.storage.sqlite_backend import SQLiteStorageBackend

logger = structlog.get_logger()


class ContextRetriever:
    """从 SQLite 存储检索生成上下文"""

    # 默认值，可通过 config 覆盖（见 __init__）
    DEFAULT_MAX_KPS = 8
    DEFAULT_MAX_REFS = 8
    DEFAULT_MAX_NEGATIVES = 5

    def __init__(
        self,
        storage: SQLiteStorageBackend,
        *,
        max_kps: int | None = None,
        max_refs: int | None = None,
        max_negatives: int | None = None,
    ) -> None:
        self.storage = storage
        self.MAX_KPS = int(max_kps) if max_kps is not None else self.DEFAULT_MAX_KPS
        self.MAX_REFS = int(max_refs) if max_refs is not None else self.DEFAULT_MAX_REFS
        self.MAX_NEGATIVES = (
            int(max_negatives) if max_negatives is not None else self.DEFAULT_MAX_NEGATIVES
        )

    def build_context(
        self,
        request: GenerationRequest,
        discipline: Discipline,
    ) -> ContextBundle:
        """构建生成上下文"""
        kps = self._match_knowledge(request, discipline)
        refs = self._match_questions(request, discipline)
        negatives = self._find_negatives(request, discipline)

        logger.info(
            "context_retriever_built",
            discipline=discipline.value,
            kps=len(kps),
            refs=len(refs),
            negatives=len(negatives),
        )

        return ContextBundle(
            knowledge_points=kps[: self.MAX_KPS],
            reference_questions=refs[: self.MAX_REFS],
            negative_examples=negatives[: self.MAX_NEGATIVES],
        )

    def _match_knowledge(
        self,
        req: GenerationRequest,
        discipline: Discipline,
    ) -> list[KnowledgePoint]:
        """按 discipline + subject + 难度窗口筛选知识点"""
        filters: dict[str, object] = {
            "discipline": discipline.value,
            "difficulty_range": (
                max(1, req.difficulty_target - 3),
                min(10, req.difficulty_target + 1),
            ),
        }
        if req.subject:
            filters["subject"] = req.subject

        ids = self.storage.search_knowledge(filters)
        kps = self.storage.load_knowledge_by_ids(ids)

        # 按 tag 重叠度排序
        if req.topics:
            topic_set = set(req.topics)
            kps.sort(
                key=lambda kp: len(topic_set & set(kp.tags)),
                reverse=True,
            )

        return kps

    def _match_questions(
        self,
        req: GenerationRequest,
        discipline: Discipline,
    ) -> list[Question]:
        """按 discipline + subject + 难度窗口 + 题型筛选参考题"""
        filters: dict[str, object] = {
            "discipline": discipline.value,
            "difficulty_range": (
                max(1, req.difficulty_target - 2),
                min(10, req.difficulty_target + 2),
            ),
        }
        if req.subject:
            filters["subject"] = req.subject
        if req.exclusion_ids:
            filters["exclude_ids"] = list(req.exclusion_ids)

        ids = self.storage.search_questions(filters)
        questions = self.storage.load_questions_by_ids(ids)

        # 按难度接近目标排序
        questions.sort(key=lambda q: abs(q.difficulty - req.difficulty_target))

        # 难度锚点：一道偏简单 + 一道偏难
        anchors: list[Question] = []
        easy_filter: dict[str, object] = {
            "discipline": discipline.value,
            "difficulty_range": (1, max(1, req.difficulty_target - 3)),
        }
        if req.subject:
            easy_filter["subject"] = req.subject
        easy_ids = self.storage.search_questions(easy_filter)
        if easy_ids:
            easy_qs = self.storage.load_questions_by_ids(easy_ids[:1])
            anchors.extend(easy_qs)

        hard_filter: dict[str, object] = {
            "discipline": discipline.value,
            "difficulty_range": (min(10, req.difficulty_target + 3), 10),
        }
        if req.subject:
            hard_filter["subject"] = req.subject
        hard_ids = self.storage.search_questions(hard_filter)
        if hard_ids:
            hard_qs = self.storage.load_questions_by_ids(hard_ids[:1])
            anchors.extend(hard_qs)

        return questions[:6] + anchors

    def _find_negatives(
        self,
        req: GenerationRequest,
        discipline: Discipline,
    ) -> list[Question]:
        """变式模式：找与源题同类型同话题的已有题做负样本"""
        if not req.source_question:
            return []

        filters: dict[str, object] = {
            "discipline": discipline.value,
            "question_type": req.source_question.question_type,
            "exclude_ids": {req.source_question.id},
        }
        ids = self.storage.search_questions(filters)
        if not ids:
            return []
        return self.storage.load_questions_by_ids(ids[: self.MAX_NEGATIVES])
