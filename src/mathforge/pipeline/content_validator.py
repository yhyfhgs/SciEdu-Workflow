"""内容完整性验证引擎"""

from __future__ import annotations

import re

from mathforge.core.models import MdBlock, MdBlockType, ValidationIssue, ValidationReport


def validate_latex_brackets(latex: str, source: str = "", block_id: str = "") -> list[ValidationIssue]:
    """Level 1: LaTeX 括号匹配验证"""
    issues: list[ValidationIssue] = []

    # $ 配对检查
    dollar_count = 0
    in_display = False
    i = 0
    while i < len(latex):
        if latex[i:i + 2] == "$$":
            in_display = not in_display
            i += 2
            continue
        elif latex[i] == "$" and not in_display:
            dollar_count += 1
        i += 1

    if dollar_count % 2 != 0:
        issues.append(ValidationIssue(
            level="error",
            category="bracket",
            message=f"行内公式 $ 未配对 (奇数个: {dollar_count})",
            source_file=source,
            block_id=block_id,
        ))
    if in_display:
        issues.append(ValidationIssue(
            level="error",
            category="bracket",
            message="显示公式 $$ 未关闭",
            source_file=source,
            block_id=block_id,
        ))

    # { } 配对
    depth = 0
    for ch in latex:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth < 0:
            issues.append(ValidationIssue(
                level="warning",
                category="bracket",
                message="多余的 '}'",
                source_file=source,
                block_id=block_id,
            ))
            depth = 0
    if depth > 0:
        issues.append(ValidationIssue(
            level="warning",
            category="bracket",
            message=f"缺少 {depth} 个 '}}'",
            source_file=source,
            block_id=block_id,
        ))

    # \begin / \end 配对
    begins = re.findall(r"\\begin\{(\w+)\}", latex)
    ends = re.findall(r"\\end\{(\w+)\}", latex)
    begin_counts: dict[str, int] = {}
    end_counts: dict[str, int] = {}
    for env in begins:
        begin_counts[env] = begin_counts.get(env, 0) + 1
    for env in ends:
        end_counts[env] = end_counts.get(env, 0) + 1
    for env in set(begin_counts) | set(end_counts):
        b = begin_counts.get(env, 0)
        e = end_counts.get(env, 0)
        if b != e:
            issues.append(ValidationIssue(
                level="warning",
                category="bracket",
                message=f"\\begin/{env}/ ({b}) vs \\end/{env}/ ({e}) 不匹配",
                source_file=source,
                block_id=block_id,
            ))

    return issues


def validate_semantic_completeness(blocks: list[MdBlock], source: str = "") -> list[ValidationIssue]:
    """语义完整性检查"""
    issues: list[ValidationIssue] = []

    theorem_types = {MdBlockType.THEOREM, MdBlockType.LEMMA, MdBlockType.COROLLARY, MdBlockType.PROPOSITION}

    for i, block in enumerate(blocks):
        block_id = f"{block.block_type.value}-{block.number or i}"

        # 定理-证明配对
        if block.block_type in theorem_types:
            has_proof = block.proof_content is not None
            # 检查后续块是否有证明
            if not has_proof and i + 1 < len(blocks):
                next_block = blocks[i + 1]
                if next_block.block_type == MdBlockType.PROOF:
                    has_proof = True
            # 检查是否注明"证明略"
            if not has_proof:
                content_lower = block.content.lower()
                if "证明略" not in content_lower and "证明留作" not in content_lower:
                    issues.append(ValidationIssue(
                        level="warning",
                        category="semantic",
                        message=f"定理/引理 {block.number or ''} 缺少证明",
                        source_file=source,
                        block_id=block_id,
                    ))

        # 定义完整性: 不应以逗号/连接词结尾
        if block.block_type == MdBlockType.DEFINITION:
            content = block.content.rstrip()
            if content and content[-1] in "，,、从而于是所以即":
                issues.append(ValidationIssue(
                    level="warning",
                    category="semantic",
                    message=f"定义 {block.number or ''} 可能被截断（以连接词结尾）",
                    source_file=source,
                    block_id=block_id,
                ))

        # 证明完整性
        if block.block_type == MdBlockType.PROOF:
            content = block.content.rstrip()
            qed_markers = ["□", "■", "▪", "得证", "证毕", "Q.E.D", "QED"]
            has_qed = any(marker in content[-20:] for marker in qed_markers) if len(content) >= 5 else False
            if not has_qed and len(content) > 50:
                issues.append(ValidationIssue(
                    level="warning",
                    category="semantic",
                    message=f"证明可能被截断（无 QED 标记）",
                    source_file=source,
                    block_id=block_id,
                ))

    return issues


def validate_numbering_continuity(blocks: list[MdBlock], source: str = "") -> list[ValidationIssue]:
    """编号连续性检查"""
    issues: list[ValidationIssue] = []

    # 按类型分组检查编号
    numbered_blocks: list[tuple[str, str]] = []
    for block in blocks:
        if block.number:
            numbered_blocks.append((block.block_type.value, block.number))

    # 简单检查: 同一前缀下的编号应连续
    # 例如 1.2.1, 1.2.2, 1.2.3 中缺少 1.2.2 应报告
    # 这里做一个基础版本
    prev_numbers: dict[str, str] = {}
    for block_type, number in numbered_blocks:
        parts = re.split(r"[.．]", number)
        if len(parts) >= 2:
            prefix = ".".join(parts[:-1])
            try:
                last_num = int(parts[-1])
                key = f"{block_type}:{prefix}"
                if key in prev_numbers:
                    prev_last = int(prev_numbers[key].split(".")[-1].split("．")[-1])
                    if last_num > prev_last + 1:
                        issues.append(ValidationIssue(
                            level="warning",
                            category="numbering",
                            message=f"编号跳跃: {prev_numbers[key]} → {number}",
                            source_file=source,
                        ))
                prev_numbers[key] = number
            except ValueError:
                pass

    return issues


def validate_cross_references(blocks: list[MdBlock], source: str = "") -> list[ValidationIssue]:
    """交叉引用验证"""
    issues: list[ValidationIssue] = []

    # 收集所有已定义的编号
    defined_numbers: set[str] = set()
    for block in blocks:
        if block.number:
            # 标准化编号
            norm = block.number.replace("．", ".").strip()
            defined_numbers.add(norm)

    # 在内容中查找引用
    ref_pattern = re.compile(r"(?:由|根据|见|参见|按照)\s*(?:定[义理]|引理|推论|命题|例)\s*(\d[\d.．]*)")
    for block in blocks:
        for m in ref_pattern.finditer(block.content):
            ref_num = m.group(1).replace("．", ".").strip()
            if ref_num not in defined_numbers:
                issues.append(ValidationIssue(
                    level="warning",
                    category="reference",
                    message=f"引用 {m.group(0)} 未在当前文档中找到定义",
                    source_file=source,
                    block_id=f"{block.block_type.value}-{block.number or ''}",
                ))

    return issues


def validate_blocks(blocks: list[MdBlock], source: str = "") -> ValidationReport:
    """运行所有验证检查"""
    all_issues: list[ValidationIssue] = []

    # LaTeX 括号验证
    for block in blocks:
        block_id = f"{block.block_type.value}-{block.number or ''}"
        issues = validate_latex_brackets(block.content, source, block_id)
        all_issues.extend(issues)

    # 语义完整性
    all_issues.extend(validate_semantic_completeness(blocks, source))

    # 编号连续性
    all_issues.extend(validate_numbering_continuity(blocks, source))

    # 交叉引用
    all_issues.extend(validate_cross_references(blocks, source))

    return ValidationReport(
        total_blocks=len(blocks),
        issues=all_issues,
        bracket_errors=sum(1 for i in all_issues if i.category == "bracket"),
        semantic_warnings=sum(1 for i in all_issues if i.category == "semantic"),
        dangling_references=sum(1 for i in all_issues if i.category == "reference"),
        numbering_gaps=sum(1 for i in all_issues if i.category == "numbering"),
    )
