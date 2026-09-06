"""ContextRetriever 混合检索测试（标签重叠 + TF-IDF 语义相似）

验证：标签重叠为主排序、同标签档内 TF-IDF 内容相似度 tiebreak、query 文本构造、
mock storage 下 build_context 集成生效。不依赖真实 storage / embedding。
"""

from __future__ import annotations

from typing import Any

from sciforge.agents.qgen.retriever import ContextRetriever
from sciforge.core.models import (
    ContextBundle,
    Discipline,
    GenerationRequest,
    KnowledgePoint,
    Question,
    VariantStrategy,
)


def _kp(content: str, tags: list[str]) -> KnowledgePoint:
    return KnowledgePoint(discipline=Discipline.MATH, kp_type="theorem", content=content, tags=tags)


def test_hybrid_sort_tag_overlap_priority() -> None:
    """标签重叠多的排在前（主排序键）"""
    more_tags = _kp("无关的群论与环论代数结构内容。", ["math:数学", "analysis:分析"])
    fewer_tags = _kp("连续函数闭区间有界达到最值的内容。", ["math:数学"])
    result = ContextRetriever._hybrid_sort(
        [fewer_tags, more_tags], "连续 闭区间 有界", ["math:数学", "analysis:分析"]
    )
    assert result[0] is more_tags  # 标签重叠 2 > 1，排第一（即便内容不相似）


def test_hybrid_sort_semantic_tiebreak() -> None:
    """同标签重叠档内，TF-IDF 内容相似度高的排前"""
    relevant = _kp("一致连续与连续在闭区间上的等价及有界性。", ["math:数学"])
    irrelevant = _kp("矩阵特征值与行列式的计算方法。", ["math:数学"])
    result = ContextRetriever._hybrid_sort(
        [irrelevant, relevant], "连续函数 闭区间 有界 最值", ["math:数学"]
    )
    assert result[0] is relevant  # 标签都 1，内容更相似者排前


def test_query_text_variant_uses_source() -> None:
    req = GenerationRequest(
        topics=["x"],
        difficulty_target=5,
        strategy=VariantStrategy.PARAMETER_SHIFT,
        source_question=Question(discipline=Discipline.MATH, content="源题的具体内容文本"),
    )
    assert ContextRetriever._query_text(req) == "源题的具体内容文本"


def test_query_text_free_uses_topics_subject() -> None:
    req = GenerationRequest(
        topics=["continuity:连续性"],
        difficulty_target=5,
        strategy=VariantStrategy.TOPIC_GENERATION,
        subject="real_analysis:实分析",
    )
    qt = ContextRetriever._query_text(req)
    assert "连续性" in qt and "real_analysis" in qt


class _MockStorage:
    def __init__(self, kps: list[KnowledgePoint], questions: list[Question]) -> None:
        self._kps = {k.id: k for k in kps}
        self._questions = {q.id: q for q in questions}
        self.knowledge_filters: list[dict[str, Any]] = []

    def search_knowledge(self, filters: dict[str, Any]) -> list[str]:
        self.knowledge_filters.append(filters)
        if "include_ids" in filters:
            return [
                item_id
                for item_id in filters["include_ids"]
                if item_id in self._kps and self._kps[item_id].ingestion_status == "accepted"
            ]
        return [item_id for item_id, kp in self._kps.items() if kp.ingestion_status == "accepted"]

    def load_knowledge_by_ids(self, ids: list[str]) -> list[KnowledgePoint]:
        return [self._kps[i] for i in ids if i in self._kps]

    def search_questions(self, filters: dict[str, Any]) -> list[str]:
        return list(self._questions)

    def load_questions_by_ids(self, ids: list[str]) -> list[Question]:
        return [self._questions[i] for i in ids if i in self._questions]


def test_build_context_hybrid_ranks_relevant_kp_first() -> None:
    relevant = _kp("连续函数在闭区间上一致连续且有界，可取到最大最小值。", ["math:数学"])
    irrelevant = _kp("群的定义与子群的拉格朗日定理。", ["math:数学"])
    storage = _MockStorage([irrelevant, relevant], [])
    retriever = ContextRetriever(storage)  # type: ignore[arg-type]
    req = GenerationRequest(
        topics=["math:数学"],
        difficulty_target=5,
        strategy=VariantStrategy.TOPIC_GENERATION,
        subject="",
    )
    bundle = retriever.build_context(req, Discipline.MATH)
    assert isinstance(bundle, ContextBundle)
    # query("math:数学") 与"连续...有界"内容更相关 → 排第一
    assert bundle.knowledge_points[0] is relevant


def test_explicit_knowledge_pin_is_prioritized_but_still_quality_gated() -> None:
    ranked = _kp("连续函数紧致性相关内容，因此会被语义检索排到前面。", ["math:数学"])
    pinned = _kp("用户明确选择的测度论定义，应优先于自动排序结果。", ["math:数学"])
    pinned.subject = "measure_theory:测度论"
    blocked = _kp("未完成复核的原始占位内容不能因显式 ID 绕过门禁。", ["math:数学"])
    blocked.ingestion_status = "uncertain"
    storage = _MockStorage([ranked, pinned, blocked], [])
    retriever = ContextRetriever(storage)  # type: ignore[arg-type]
    request = GenerationRequest(
        topics=["math:数学"],
        knowledge_ids=[pinned.id, blocked.id],
        subject="real_analysis:实分析",
        difficulty_target=5,
        strategy=VariantStrategy.TOPIC_GENERATION,
    )

    bundle = retriever.build_context(request, Discipline.MATH)

    assert bundle.knowledge_points[0] is pinned
    assert blocked not in bundle.knowledge_points
    pin_query = next(call for call in storage.knowledge_filters if "include_ids" in call)
    assert pin_query["include_ids"] == [pinned.id, blocked.id]
    assert "subject" not in pin_query
    assert "quality_gate" in pin_query
