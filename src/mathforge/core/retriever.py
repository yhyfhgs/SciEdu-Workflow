"""Stage 1: 上下文检索器（增强版，支持层级标签 + 模糊搜索）"""

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
    from mathforge.storage.enhanced_index import EnhancedIndexManager
    from mathforge.storage.file_backend import FileStorageBackend

logger = structlog.get_logger()


class ContextRetriever:
    """从知识库和题库中检索生成上下文

    增强版: 支持 EnhancedIndexManager 的层级标签搜索和模糊匹配。
    向后兼容: 当 storage 使用普通 IndexManager 时自动 fallback 到旧逻辑。
    """

    def __init__(self, storage: FileStorageBackend):
        self.storage = storage
        self._enhanced = self._detect_enhanced()

    def _detect_enhanced(self) -> bool:
        """检测 storage 是否使用 EnhancedIndexManager"""
        from mathforge.storage.enhanced_index import EnhancedIndexManager
        return (
            isinstance(getattr(self.storage, '_kp_index', None), EnhancedIndexManager)
            or isinstance(getattr(self.storage, '_q_index', None), EnhancedIndexManager)
        )

    def build_context(
        self,
        request: GenerationRequest,
        depth: int | None = None,
    ) -> ContextBundle:
        """构建生成上下文

        Args:
            request: 生成请求
            depth: 标签搜索深度限制（None 不限制）
        """
        kps = self._match_knowledge(request, depth=depth)
        refs = self._match_questions(request)
        negatives = self._find_negatives(request)

        logger.info(
            "context_built",
            knowledge_points=len(kps),
            reference_questions=len(refs),
            negative_examples=len(negatives),
            enhanced=self._enhanced,
        )

        return ContextBundle(
            knowledge_points=kps[:8],
            reference_questions=refs[:8],
            negative_examples=negatives[:5],
        )

    def _match_knowledge(
        self,
        req: GenerationRequest,
        depth: int | None = None,
    ) -> list[KnowledgePoint]:
        """按标签匹配 + 难度窗口筛选知识点

        增强模式下的搜索优先级:
        1. Trie 精确/前缀匹配 + 难度窗口
        2. Trie 子串匹配 + 难度窗口
        3. 标题关键词 + 难度窗口
        4. 仅难度范围
        5. 全量 fallback
        """
        d_min = max(1, req.difficulty_target - 3)
        d_max = min(10, req.difficulty_target + 1)

        if self._enhanced:
            ids = self._match_knowledge_enhanced(req, d_min, d_max, depth)
        else:
            ids = self._match_knowledge_basic(req, d_min, d_max)

        kps = self.storage.load_knowledge_by_ids(ids[:30])

        # 排序: 标签重叠 + 标题关键词 + 难度接近
        topic_set = set(req.topics)

        def score(kp: KnowledgePoint) -> tuple[int, float]:
            tag_overlap = len(set(kp.tags) & topic_set)
            title_match = sum(1 for t in req.topics if t in kp.title)
            d_proximity = -abs(kp.difficulty - req.difficulty_target)
            return (tag_overlap + title_match, d_proximity)

        kps.sort(key=score, reverse=True)
        return kps[:8]

    def _match_knowledge_enhanced(
        self,
        req: GenerationRequest,
        d_min: int,
        d_max: int,
        depth: int | None,
    ) -> list[str]:
        """增强模式: 使用 EnhancedIndexManager 的 trie 搜索"""
        kp_index: EnhancedIndexManager = self.storage._kp_index  # type: ignore[assignment]

        # Level 1: 层级标签查询 + 难度
        ids = kp_index.query_hierarchical(
            tag_queries=req.topics,
            difficulty_range=(d_min, d_max),
            depth=depth,
        )
        if ids:
            logger.debug("knowledge_match", level="hierarchical", count=len(ids))
            return ids

        # Level 2: 子串搜索 + 难度
        all_substring: set[str] = set()
        for topic in req.topics:
            all_substring |= kp_index.tag_trie.search_substring(topic)
        if all_substring:
            diff_ids = kp_index._query_difficulty_range(d_min, d_max)
            ids = list(all_substring & diff_ids)
            if ids:
                logger.debug("knowledge_match", level="substring", count=len(ids))
                return ids

        # Level 3: 模糊搜索 + 难度
        all_fuzzy: set[str] = set()
        for topic in req.topics:
            all_fuzzy |= kp_index.tag_trie.search_fuzzy(topic, max_distance=2)
        if all_fuzzy:
            diff_ids = kp_index._query_difficulty_range(d_min, d_max)
            ids = list(all_fuzzy & diff_ids)
            if ids:
                logger.debug("knowledge_match", level="fuzzy", count=len(ids))
                return ids

        # Level 4: 仅难度
        ids = kp_index.query({"difficulty_range": (d_min, d_max)})
        if ids:
            logger.debug("knowledge_fallback", reason="difficulty_only", count=len(ids))
            return ids

        # Level 5: 全量
        ids = kp_index.query({})
        logger.debug("knowledge_fallback", reason="all", count=len(ids))
        return ids

    def _match_knowledge_basic(
        self, req: GenerationRequest, d_min: int, d_max: int,
    ) -> list[str]:
        """基础模式: 原有 IndexManager 逻辑"""
        ids = self.storage.search_knowledge({
            "tags": req.topics,
            "difficulty_range": (d_min, d_max),
        })
        if not ids:
            ids = self.storage.search_knowledge({"difficulty_range": (d_min, d_max)})
            if ids:
                logger.debug("knowledge_fallback", reason="empty_tags", count=len(ids))
        if not ids:
            ids = self.storage.search_knowledge({})
            if ids:
                logger.debug("knowledge_fallback", reason="no_difficulty_match", count=len(ids))
        return ids

    def _match_questions(self, req: GenerationRequest) -> list[Question]:
        """按专题 + 难度筛选参考题"""
        d_min = max(1, req.difficulty_target - 2)
        d_max = min(10, req.difficulty_target + 2)

        ids = self.storage.search_questions({
            "tags": req.topics,
            "difficulty_range": (d_min, d_max),
        })

        if not ids:
            ids = self.storage.search_questions({
                "subject": req.subject.value,
                "difficulty_range": (d_min, d_max),
            })
            if ids:
                logger.debug("questions_fallback", reason="empty_tags", count=len(ids))

        if not ids:
            ids = self.storage.search_questions({
                "subject": req.subject.value,
            })

        ids = [i for i in ids if i not in req.exclusion_ids]
        questions = self.storage.load_questions_by_ids(ids[:20])

        questions.sort(key=lambda q: abs(q.difficulty - req.difficulty_target))
        main = questions[:6]

        # 难度锚点
        anchors: list[Question] = []
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

        src_tags = set(req.source_question.tags)
        if src_tags:
            questions.sort(
                key=lambda q: len(set(q.tags) & src_tags),
                reverse=True,
            )

        return questions[:5]
