"""Stage 1: 上下文检索器"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    ContextBundle,
    GenerationRequest,
    KnowledgePoint,
    Question,
)

if TYPE_CHECKING:
    from mathforge.storage.file_backend import FileStorageBackend

logger = structlog.get_logger()


class ContextRetriever:
    """从知识库和题库中检索生成上下文

    当前版本: 基于 JSON 索引的检索，支持标签为空时的 fallback。
    """

    def __init__(self, storage: FileStorageBackend):
        self.storage = storage

    def build_context(self, request: GenerationRequest) -> ContextBundle:
        """构建生成上下文"""
        kps = self._match_knowledge(request)
        refs = self._match_questions(request)
        negatives = self._find_negatives(request)

        logger.info(
            "context_built",
            knowledge_points=len(kps),
            reference_questions=len(refs),
            negative_examples=len(negatives),
        )

        return ContextBundle(
            knowledge_points=kps[:8],
            reference_questions=refs[:8],
            negative_examples=negatives[:5],
        )

    def _match_knowledge(self, req: GenerationRequest) -> list[KnowledgePoint]:
        """按标签匹配 + 难度窗口筛选知识点，支持 fallback"""
        d_min = max(1, req.difficulty_target - 3)
        d_max = min(10, req.difficulty_target + 1)

        # 优先: 标签匹配
        ids = self.storage.search_knowledge({
            "tags": req.topics,
            "difficulty_range": (d_min, d_max),
        })

        # Fallback: 标签为空时仅用难度范围
        if not ids:
            ids = self.storage.search_knowledge({
                "difficulty_range": (d_min, d_max),
            })
            if ids:
                logger.debug("knowledge_fallback", reason="empty_tags", count=len(ids))

        # 再 fallback: 连难度都没匹配到，取全部
        if not ids:
            ids = self.storage.search_knowledge({})
            if ids:
                logger.debug("knowledge_fallback", reason="no_difficulty_match", count=len(ids))

        kps = self.storage.load_knowledge_by_ids(ids[:30])

        # 按标签重叠度排序（标签非空时有效），否则按标题关键词匹配
        topic_set = set(req.topics)

        def score(kp: KnowledgePoint) -> tuple[int, float]:
            tag_overlap = len(set(kp.tags) & topic_set)
            # 标题关键词匹配作为 fallback 排序
            title_match = sum(1 for t in req.topics if t in kp.title)
            # 难度接近度
            d_proximity = -abs(kp.difficulty - req.difficulty_target)
            return (tag_overlap + title_match, d_proximity)

        kps.sort(key=score, reverse=True)
        return kps[:8]

    def _match_questions(self, req: GenerationRequest) -> list[Question]:
        """按专题 + 难度筛选参考题"""
        d_min = max(1, req.difficulty_target - 2)
        d_max = min(10, req.difficulty_target + 2)

        # 优先: 标签匹配
        ids = self.storage.search_questions({
            "tags": req.topics,
            "difficulty_range": (d_min, d_max),
        })

        # Fallback: 按科目 + 难度
        if not ids:
            ids = self.storage.search_questions({
                "subject": req.subject.value,
                "difficulty_range": (d_min, d_max),
            })
            if ids:
                logger.debug("questions_fallback", reason="empty_tags", count=len(ids))

        # 再 fallback: 仅按科目
        if not ids:
            ids = self.storage.search_questions({
                "subject": req.subject.value,
            })

        # 排除已有
        ids = [i for i in ids if i not in req.exclusion_ids]
        questions = self.storage.load_questions_by_ids(ids[:20])

        # 按难度接近度排序
        questions.sort(key=lambda q: abs(q.difficulty - req.difficulty_target))
        main = questions[:6]

        # 添加难度锚点
        anchors = []
        all_ids = {q.id for q in main}
        for q in questions:
            if q.id not in all_ids and q.difficulty <= req.difficulty_target - 3:
                anchors.append(q)
                break
        for q in reversed(questions):
            if q.id not in all_ids and q.difficulty >= req.difficulty_target + 3:
                anchors.append(q)
                break

        return main + anchors

    def _find_negatives(self, req: GenerationRequest) -> list[Question]:
        """变式模式下，找与源题最相似的已有题作为负样本"""
        if not req.source_question:
            return []

        ids = self.storage.search_questions({
            "subject": req.subject.value,
            "type": req.question_type.value,
        })
        ids = [i for i in ids if i != req.source_question.id]
        questions = self.storage.load_questions_by_ids(ids[:10])

        # 优先选与源题标签重叠的
        src_tags = set(req.source_question.tags)
        if src_tags:
            questions.sort(
                key=lambda q: len(set(q.tags) & src_tags),
                reverse=True,
            )

        return questions[:5]
