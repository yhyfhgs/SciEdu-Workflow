"""ItemUnifier — RawItem + ItemProcessingResult → UnifiedItemBase

确定性 Python 逻辑，不调用 LLM。
根据 item_subtype 派发到正确的 UnifiedItem 子类。
Proof/Solution 合并到 parent 的嵌套字段。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import (
    CalculationQuestion,
    ChoiceQuestion,
    ConstructionQuestion,
    CorollaryItem,
    DefinitionItem,
    ExampleItem,
    FillBlankQuestion,
    FormulaItem,
    ItemSource,
    LemmaItem,
    MdBlockV2,
    MethodItem,
    ProofContent,
    ProofQuestion,
    PropositionItem,
    QualityAssessment,
    RawItem,
    RemarkItem,
    SolutionContent,
    TheoremItem,
    TrueFalseQuestion,
    UnifiedItemBase,
)

if TYPE_CHECKING:
    from mathforge.core.models import ItemProcessingResult

logger = structlog.get_logger()

# item_subtype → (类, 可选的 kp_type/question_type 覆写)
_SUBTYPE_MAP: dict[str, type[UnifiedItemBase]] = {
    "definition": DefinitionItem,
    "theorem": TheoremItem,
    "lemma": LemmaItem,
    "corollary": CorollaryItem,
    "proposition": PropositionItem,
    "formula": FormulaItem,
    "method": MethodItem,
    "remark": RemarkItem,
    "example": ExampleItem,
    "proof_question": ProofQuestion,
    "calculation_question": CalculationQuestion,
    "choice_question": ChoiceQuestion,
    "fill_blank_question": FillBlankQuestion,
    "construction_question": ConstructionQuestion,
    "true_false_question": TrueFalseQuestion,
    # 以下为 block_type 直接映射的后备
    "problem": ProofQuestion,
    "exercise": CalculationQuestion,
    "proof": TheoremItem,  # proof 会被合并到 parent，此为后备
    "solution": ProofQuestion,  # solution 会被合并到 parent
    "property": TheoremItem,
    "text": DefinitionItem,
}


class ItemUnifier:
    """将 RawItem + 处理结果转化为 UnifiedItem"""

    def __init__(self) -> None:
        self._items: dict[str, UnifiedItemBase] = {}
        self._raw_map: dict[str, RawItem] = {}

    def unify_batch(
        self,
        raw_items: list[tuple[RawItem, ItemProcessingResult, MdBlockV2]],
    ) -> list[UnifiedItemBase]:
        """批量转化，处理配对关系"""
        results: list[UnifiedItemBase] = []

        for raw, result, block in raw_items:
            self._raw_map[raw.item_id] = raw
            item = self._unify_single(raw, result, block)
            if item:
                self._items[raw.item_id] = item
                results.append(item)

        # 第二遍：合并 proof/solution 到 parent
        self._merge_paired(results)

        logger.info("items_unified", total=len(results))
        return results

    def _unify_single(
        self,
        raw: RawItem,
        result: ItemProcessingResult,
        block: MdBlockV2,
    ) -> UnifiedItemBase | None:
        """转化单个 RawItem"""
        subtype = result.item_subtype or raw.block_type
        cls = _SUBTYPE_MAP.get(subtype)

        if cls is None:
            logger.warning(
                "unify_unknown_subtype",
                subtype=subtype,
                item_id=raw.item_id,
            )
            cls = DefinitionItem

        # 构建来源
        source = ItemSource(
            file_path=raw.source_file,
            block_id=raw.source_block_id,
            line_range=(raw.line_start, raw.line_end),
            source_type=block.format_spec.source_type if block.format_spec else "",
            chapter=block.chapter_title or None,
            section=block.section_title or None,
            extraction_method="meta_extractor",
        )
        if raw.raw_metadata:
            source.exam_type = raw.raw_metadata.get("exam_type")
            source.year = raw.raw_metadata.get("year")

        # 质量评估
        quality = QualityAssessment(
            grade=result.correction.overall_quality,
            completeness=result.correction.completeness_score,
        )

        # 内容
        content_latex = result.correction.corrected_latex or result.correction.corrected_content
        content_plain = result.correction.corrected_content

        # 构建基础字段
        base_kwargs = {
            "item_id": raw.item_id,
            "content_latex": content_latex,
            "content_plain": content_plain,
            "content_markdown": raw.content_raw,
            "title": self._build_title(raw, subtype),
            "number": raw.number,
            "source": source,
            "tags": result.classification,
            "quality": quality,
        }

        # 根据类型构建特定字段
        try:
            if issubclass(cls, (ProofQuestion, CalculationQuestion, ChoiceQuestion,
                               FillBlankQuestion, ConstructionQuestion, TrueFalseQuestion)):
                base_kwargs["difficulty"] = result.difficulty
                if result.classification:
                    base_kwargs["subject"] = result.classification.subject
                item = cls(**base_kwargs)
            elif cls == ExampleItem:
                base_kwargs["difficulty"] = result.difficulty
                if result.classification:
                    base_kwargs["subject"] = result.classification.subject
                item = cls(**base_kwargs)
            else:
                # KnowledgeItem 及子类
                base_kwargs["difficulty"] = result.difficulty
                if result.classification:
                    base_kwargs["subject"] = result.classification.subject
                item = cls(**base_kwargs)

            return item
        except Exception:
            logger.error(
                "unify_failed",
                item_id=raw.item_id,
                subtype=subtype,
                exc_info=True,
            )
            return None

    def _merge_paired(self, items: list[UnifiedItemBase]) -> None:
        """将 proof/solution 合并到 parent 的嵌套字段"""
        id_to_item: dict[str, UnifiedItemBase] = {i.item_id: i for i in items}
        to_remove: set[str] = set()

        for item in items:
            raw = self._raw_map.get(item.item_id)
            if not raw or not raw.paired_item_id:
                continue

            parent = id_to_item.get(raw.paired_item_id)
            if not parent:
                continue

            if raw.block_type == "proof" and hasattr(parent, "proof"):
                parent.proof = ProofContent(  # type: ignore[attr-defined]
                    proof_latex=item.content_latex,
                    proof_plain=item.content_plain,
                )
                to_remove.add(item.item_id)

            elif raw.block_type == "solution" and hasattr(parent, "solution"):
                parent.solution = SolutionContent(  # type: ignore[attr-defined]
                    solution_latex=item.content_latex,
                    solution_plain=item.content_plain,
                )
                to_remove.add(item.item_id)

        # 移除已合并的条目
        items[:] = [i for i in items if i.item_id not in to_remove]

    @staticmethod
    def _build_title(raw: RawItem, subtype: str) -> str:
        """生成条目标题"""
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
