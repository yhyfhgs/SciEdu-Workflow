"""ContextRetriever — QGen 第 1 阶段：从 SQLite 检索上下文

SQLiteStorageBackend 驱动的混合检索：
1. 按 discipline + subject + 难度窗口过滤知识点 / 参考题
2. 混合排序：标签重叠度为主 + TF-IDF 内容语义相似度为辅（复用 prefilter，无外部依赖）
3. 难度锚点（一个偏简单 + 一个偏难）

返回 ContextBundle 给 QuestionGenerator 用。

注：语义排序当前用 TF-IDF cosine；真 embedding 向量检索（sqlite-vec / pgvector）
作为后续升级，待 storage schema 稳定 + embedding 来源确定后接入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from sciforge.agents.qgen.prefilter import _tf_vector, cosine_similarity
from sciforge.core.models import (
    ContextBundle,
    Discipline,
    GenerationRequest,
    KnowledgePoint,
    Question,
)
from sciforge.storage.query import QualityGateFilter

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
        quality_gate_enabled: bool = True,
        quality_rubric_version: str = "v1",
        quality_extraction_min: float = 0.7,
        quality_solution_min: float = 0.6,
        quality_allow_legacy_unscored: bool = True,
    ) -> None:
        self.storage = storage
        self.MAX_KPS = int(max_kps) if max_kps is not None else self.DEFAULT_MAX_KPS
        self.MAX_REFS = int(max_refs) if max_refs is not None else self.DEFAULT_MAX_REFS
        self.MAX_NEGATIVES = (
            int(max_negatives) if max_negatives is not None else self.DEFAULT_MAX_NEGATIVES
        )
        self.quality_gate_enabled = quality_gate_enabled
        self.quality_rubric_version = quality_rubric_version
        self.quality_extraction_min = quality_extraction_min
        self.quality_solution_min = quality_solution_min
        self.quality_allow_legacy_unscored = quality_allow_legacy_unscored

    def _apply_quality_gate(self, filters: dict[str, object], item_kind: str) -> None:
        if not self.quality_gate_enabled:
            return
        if item_kind not in {"knowledge", "question"}:
            raise ValueError(f"unsupported quality-gate item kind: {item_kind}")
        filters["quality_gate"] = QualityGateFilter(
            item_kind=item_kind,  # type: ignore[arg-type]
            rubric_version=self.quality_rubric_version,
            extraction_min=self.quality_extraction_min,
            solution_min=self.quality_solution_min,
            allow_legacy_when_unscored=self.quality_allow_legacy_unscored,
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

    @staticmethod
    def _query_text(req: GenerationRequest) -> str:
        """语义检索 query：变式用源题内容，自由生成用 topics + subject。"""
        if req.source_question is not None:
            return req.source_question.content
        return " ".join(req.topics) + " " + (req.subject or "")

    @staticmethod
    def _hybrid_sort(
        items: list[KnowledgePoint], query_text: str, topics: list[str]
    ) -> list[KnowledgePoint]:
        """混合排序键 (标签重叠度, TF-IDF 内容相似度) 降序：标签匹配优先，同档按语义相似。"""
        query_vec = _tf_vector(query_text)
        topic_set = set(topics)
        return sorted(
            items,
            key=lambda kp: (
                len(topic_set & set(kp.tags)),
                cosine_similarity(query_vec, _tf_vector(kp.content)),
            ),
            reverse=True,
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
        self._apply_quality_gate(filters, "knowledge")

        ids = self.storage.search_knowledge(filters)
        kps = self.storage.load_knowledge_by_ids(ids)

        # 混合排序：标签重叠度为主 + TF-IDF 内容语义相似度为辅
        return self._hybrid_sort(kps, self._query_text(req), req.topics)

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
        self._apply_quality_gate(filters, "question")

        ids = self.storage.search_questions(filters)
        questions = self.storage.load_questions_by_ids(ids)

        # 难度接近为主 + 内容语义相似为辅（同难度档按 TF-IDF 相似度优先）
        query_vec = _tf_vector(self._query_text(req))
        questions.sort(
            key=lambda q: (
                abs(q.difficulty - req.difficulty_target),
                -cosine_similarity(query_vec, _tf_vector(q.content)),
            )
        )

        # 难度锚点：一道偏简单 + 一道偏难
        anchors: list[Question] = []
        easy_filter: dict[str, object] = {
            "discipline": discipline.value,
            "difficulty_range": (1, max(1, req.difficulty_target - 3)),
        }
        if req.subject:
            easy_filter["subject"] = req.subject
        self._apply_quality_gate(easy_filter, "question")
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
        self._apply_quality_gate(hard_filter, "question")
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
        self._apply_quality_gate(filters, "question")
        ids = self.storage.search_questions(filters)
        if not ids:
            return []
        return self.storage.load_questions_by_ids(ids[: self.MAX_NEGATIVES])
