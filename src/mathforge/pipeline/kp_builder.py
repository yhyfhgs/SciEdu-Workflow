"""MdBlock → KnowledgePoint 转换"""

from __future__ import annotations

from pathlib import Path

from mathforge.core.models import (
    KnowledgePoint,
    KPSource,
    KPType,
    MdBlock,
    MdBlockType,
)

# 块类型 → 知识点类型映射
BLOCK_TO_KP_TYPE: dict[MdBlockType, KPType] = {
    MdBlockType.DEFINITION: KPType.DEFINITION,
    MdBlockType.THEOREM: KPType.THEOREM,
    MdBlockType.LEMMA: KPType.LEMMA,
    MdBlockType.COROLLARY: KPType.COROLLARY,
    MdBlockType.PROPOSITION: KPType.PROPOSITION,
    MdBlockType.EXAMPLE: KPType.EXAMPLE,
    MdBlockType.REMARK: KPType.REMARK,
    MdBlockType.PROPERTY: KPType.PROPOSITION,
    MdBlockType.EXERCISE: KPType.EXAMPLE,
}

# 可转换为 KP 的块类型
KP_ELIGIBLE_TYPES = set(BLOCK_TO_KP_TYPE.keys())


def _detect_subject(filepath: Path) -> str:
    """从文件路径推断科目"""
    path_str = str(filepath)
    if "Analysis" in path_str or "分析" in path_str:
        return "analysis"
    if "Algebra" in path_str or "代数" in path_str:
        return "linear_algebra"
    if "Group" in path_str or "群" in path_str:
        return "group_theory"
    return "other"


def _build_kp_id(subject: str, number: str | None, index: int) -> str:
    """生成知识点 ID"""
    if number:
        num_clean = number.replace("．", ".").replace(" ", "").strip(".")
        return f"kp-{subject}-{num_clean}"
    return f"kp-{subject}-auto-{index:04d}"


def _build_title(block: MdBlock) -> str:
    """从块信息生成标题"""
    type_names = {
        MdBlockType.DEFINITION: "定义",
        MdBlockType.THEOREM: "定理",
        MdBlockType.LEMMA: "引理",
        MdBlockType.COROLLARY: "推论",
        MdBlockType.PROPOSITION: "命题",
        MdBlockType.EXAMPLE: "例",
        MdBlockType.REMARK: "注",
        MdBlockType.PROPERTY: "性质",
        MdBlockType.EXERCISE: "习题",
    }
    type_name = type_names.get(block.block_type, block.block_type.value)
    parts = [type_name]
    if block.number:
        parts.append(block.number)
    if block.name:
        parts.append(f"({block.name})")
    return " ".join(parts)


def build_knowledge_points(blocks: list[MdBlock], filepath: Path) -> list[KnowledgePoint]:
    """将 MdBlock 列表转换为 KnowledgePoint 列表"""
    subject = _detect_subject(filepath)
    results: list[KnowledgePoint] = []
    auto_index = 0

    for block in blocks:
        if block.block_type not in KP_ELIGIBLE_TYPES:
            continue

        # 跳过乱码内容
        if block.has_garbled_content:
            continue

        # 跳过太短的块（可能是残留）
        if len(block.content.strip()) < 10:
            continue

        auto_index += 1
        kp_id = _build_kp_id(subject, block.number, auto_index)

        # 构建内容（含证明）
        content = block.content
        if block.proof_content:
            content += f"\n\n证明：{block.proof_content}"

        kp = KnowledgePoint(
            id=kp_id,
            title=_build_title(block),
            content_latex=content,
            kp_type=BLOCK_TO_KP_TYPE[block.block_type],
            source=KPSource(
                source_file=str(filepath),
                line_range=(block.line_start, block.line_end),
            ),
        )
        results.append(kp)

    return results
