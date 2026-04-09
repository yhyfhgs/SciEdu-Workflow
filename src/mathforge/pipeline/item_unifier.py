"""ItemUnifier — RawItem + ItemProcessingResult → UnifiedItemBase

确定性 Python 逻辑，不调用 LLM。
通过 kp_type/question_type 枚举判别条目类型，无需 22 个子类。
Proof/Solution 合并到 parent 的嵌套字段。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    ChunkExtractionResult,
    ExampleItem,
    ItemSource,
    KnowledgeItem,
    KPType,
    ProofContent,
    QualityAssessment,
    QuestionItem,
    QuestionType,
    RawItem,
    SolutionContent,
    UnifiedItemBase,
)

if TYPE_CHECKING:
    from mathforge.core.models import ItemProcessingResult

logger = structlog.get_logger()

# subtype → (类, 判别字段名, 枚举值)
_SUBTYPE_MAP: dict[str, tuple[type[UnifiedItemBase], str, KPType | QuestionType]] = {
    # 知识点类型
    "definition": (KnowledgeItem, "kp_type", KPType.DEFINITION),
    "theorem": (KnowledgeItem, "kp_type", KPType.THEOREM),
    "lemma": (KnowledgeItem, "kp_type", KPType.LEMMA),
    "corollary": (KnowledgeItem, "kp_type", KPType.COROLLARY),
    "proposition": (KnowledgeItem, "kp_type", KPType.PROPOSITION),
    "formula": (KnowledgeItem, "kp_type", KPType.FORMULA),
    "method": (KnowledgeItem, "kp_type", KPType.METHOD),
    "remark": (KnowledgeItem, "kp_type", KPType.REMARK),
    "example": (ExampleItem, "kp_type", KPType.EXAMPLE),
    # 题目类型
    "proof_question": (QuestionItem, "question_type", QuestionType.PROOF),
    "calculation_question": (QuestionItem, "question_type", QuestionType.CALCULATION),
    "choice_question": (QuestionItem, "question_type", QuestionType.CHOICE),
    "fill_blank_question": (QuestionItem, "question_type", QuestionType.FILL_BLANK),
    "construction_question": (QuestionItem, "question_type", QuestionType.CONSTRUCTION),
    "true_false_question": (QuestionItem, "question_type", QuestionType.TRUE_FALSE),
    # block_type 后备映射
    "problem": (QuestionItem, "question_type", QuestionType.CALCULATION),
    "exercise": (QuestionItem, "question_type", QuestionType.CALCULATION),
    # proof/solution 作为独立条目时用 REMARK 标记，等待配对合并
    "proof": (KnowledgeItem, "kp_type", KPType.REMARK),
    "solution": (KnowledgeItem, "kp_type", KPType.REMARK),
    "property": (KnowledgeItem, "kp_type", KPType.PROPOSITION),
    "text": (KnowledgeItem, "kp_type", KPType.REMARK),
}

# 配对兼容类型
_PAIR_COMPATIBLE: dict[str, list[str]] = {
    "proof": ["theorem", "lemma", "corollary", "proposition", "property"],
    "solution": [
        "problem",
        "exercise",
        "example",
        "proof_question",
        "calculation_question",
        "choice_question",
        "fill_blank_question",
        "construction_question",
        "true_false_question",
    ],
}


class ItemUnifier:
    """将 RawItem + 处理结果转化为 UnifiedItem"""

    def __init__(self) -> None:
        self._raw_map: dict[str, RawItem] = {}

    def unify_batch(
        self,
        raw_items: list[tuple[RawItem, ItemProcessingResult, ChunkExtractionResult]],
    ) -> list[UnifiedItemBase]:
        """批量转化，处理配对关系"""
        results: list[UnifiedItemBase] = []

        for raw, result, block in raw_items:
            self._raw_map[raw.item_id] = raw
            item = self._unify_single(raw, result, block)
            if item:
                results.append(item)

        self._merge_paired(results)

        logger.info("items_unified", total=len(results))
        return results

    def _unify_single(
        self,
        raw: RawItem,
        result: ItemProcessingResult,
        block: ChunkExtractionResult,
    ) -> UnifiedItemBase | None:
        """转化单个 RawItem"""
        subtype = result.item_subtype or raw.block_type
        entry = _SUBTYPE_MAP.get(subtype)

        if entry is None:
            logger.warning("unify_unknown_subtype", subtype=subtype, item_id=raw.item_id)
            cls, disc_field, disc_value = KnowledgeItem, "kp_type", KPType.DEFINITION
        else:
            cls, disc_field, disc_value = entry

        source = ItemSource(
            file_path=raw.source_file,
            block_id=raw.source_block_id,
            line_range=(raw.line_start, raw.line_end),
            source_type=getattr(block, "structural_context", None)
            and block.structural_context.document_type
            or "",
            chapter=getattr(block, "structural_context", None)
            and block.structural_context.chapter_title
            or None,
            section=getattr(block, "structural_context", None)
            and block.structural_context.section_title
            or None,
            extraction_method="chunk_extractor",
        )
        if raw.raw_metadata:
            source.exam_type = raw.raw_metadata.get("exam_type")
            source.year = raw.raw_metadata.get("year")

        quality = QualityAssessment(
            grade=result.correction.overall_quality,
            completeness=result.correction.completeness_score,
        )

        content_latex = result.correction.corrected_latex or result.correction.corrected_content
        content_plain = result.correction.corrected_content

        kwargs: dict = {
            "item_id": raw.item_id,
            "content_latex": content_latex,
            "content_plain": content_plain,
            "content_markdown": raw.content_raw,
            "title": self._build_title(raw, subtype),
            "number": raw.number,
            "source": source,
            "tags": result.classification,
            "quality": quality,
            "difficulty": result.difficulty,
            disc_field: disc_value,
        }

        # 添加 subject（如果分类结果中有）
        if result.classification and result.classification.subject:
            kwargs["subject"] = result.classification.subject

        try:
            return cls(**kwargs)
        except Exception:
            logger.error("unify_failed", item_id=raw.item_id, subtype=subtype, exc_info=True)
            return None

    def _merge_paired(self, items: list[UnifiedItemBase]) -> None:
        """将 proof/solution 合并到 parent 的嵌套字段

        两轮配对 → 未配对的标记 intended_parent_type
        """
        id_to_item: dict[str, UnifiedItemBase] = {i.item_id: i for i in items}
        to_remove: set[str] = set()

        orphans: list[tuple[int, UnifiedItemBase]] = []
        for idx, item in enumerate(items):
            raw = self._raw_map.get(item.item_id)
            if not raw or raw.block_type not in ("proof", "solution"):
                continue

            parent = id_to_item.get(raw.paired_item_id or "")
            if parent and self._try_merge(raw.block_type, item, parent):
                to_remove.add(item.item_id)
            else:
                orphans.append((idx, item))

        # 二次配对：扫描相邻条目
        for idx, item in orphans:
            if item.item_id in to_remove:
                continue
            raw = self._raw_map.get(item.item_id)
            if not raw:
                continue

            compatible = _PAIR_COMPATIBLE.get(raw.block_type, [])
            for offset in range(1, 4):
                check_idx = idx - offset
                if check_idx < 0:
                    break
                candidate = items[check_idx]
                if candidate.item_id in to_remove:
                    continue
                cand_raw = self._raw_map.get(candidate.item_id)
                if cand_raw and cand_raw.block_type in compatible:
                    if self._try_merge(raw.block_type, item, candidate):
                        to_remove.add(item.item_id)
                        break

        # 标记未配对的孤儿
        for _, item in orphans:
            if item.item_id not in to_remove:
                raw = self._raw_map.get(item.item_id)
                if raw and hasattr(item, "intended_parent_type"):
                    item.intended_parent_type = raw.raw_metadata.get("parent_type", "")  # type: ignore[union-attr]
                    logger.warning("orphan_item_remains", item_id=item.item_id)

        items[:] = [i for i in items if i.item_id not in to_remove]

    @staticmethod
    def _try_merge(child_type: str, child: UnifiedItemBase, parent: UnifiedItemBase) -> bool:
        if child_type == "proof" and hasattr(parent, "proof"):
            parent.proof = ProofContent(  # type: ignore[attr-defined]
                proof_latex=child.content_latex,
                proof_plain=child.content_plain,
            )
            return True
        if child_type == "solution" and hasattr(parent, "solution"):
            parent.solution = SolutionContent(  # type: ignore[attr-defined]
                solution_latex=child.content_latex,
                solution_plain=child.content_plain,
            )
            return True
        return False

    @staticmethod
    def _build_title(raw: RawItem, subtype: str) -> str:
        type_names = {
            "definition": "定义",
            "theorem": "定理",
            "lemma": "引理",
            "corollary": "推论",
            "proposition": "命题",
            "formula": "公式",
            "method": "方法",
            "remark": "注记",
            "example": "例题",
            "proof_question": "证明题",
            "calculation_question": "计算题",
            "choice_question": "选择题",
            "fill_blank_question": "填空题",
            "construction_question": "构造题",
            "true_false_question": "判断题",
            "problem": "题目",
            "exercise": "练习",
        }
        type_name = type_names.get(subtype, subtype)
        if raw.number:
            return f"{type_name} {raw.number}"
        if raw.title:
            return raw.title
        return type_name
