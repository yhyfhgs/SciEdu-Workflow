"""Markdown 文件解析器 — 从讲义和教材 MD 中提取结构化知识块"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from mathforge.core.models import MdBlock, MdBlockType, MdSourceType

logger = structlog.get_logger()

# ── 块识别模式（全角/半角标点兼容）────────────────────

P = r"[.．]"  # period (full-width or ASCII)
NUM = r"(\d[\d.．]*)"  # numbering like 1.2.3 or 1．2．3

BLOCK_PATTERNS: dict[MdBlockType, re.Pattern[str]] = {
    MdBlockType.DEFINITION: re.compile(
        rf"^定义\s*{NUM}\s*{P}\s*(?:[\[｜\|（(](.+?)[\]｜\|）)])?\.?\s*(.*)", re.MULTILINE
    ),
    MdBlockType.THEOREM: re.compile(
        rf"^定理\s*{NUM}\s*{P}?\s*(?:[\[｜\|（(](.+?)[\]｜\|）)])?\.?\s*(.*)", re.MULTILINE
    ),
    MdBlockType.LEMMA: re.compile(
        rf"^引理\s*{NUM}\s*{P}?\s*(?:[\[（(](.+?)[\]）)])?\.?\s*(.*)", re.MULTILINE
    ),
    MdBlockType.COROLLARY: re.compile(
        rf"^推论\s*{NUM}\s*{P}?\s*(?:[\[（(](.+?)[\]）)])?\.?\s*(.*)", re.MULTILINE
    ),
    MdBlockType.PROPOSITION: re.compile(rf"^(?:性质|命题)\s*{NUM}\s*{P}?\s*(.*)", re.MULTILINE),
    MdBlockType.EXAMPLE: re.compile(rf"^例\s*{NUM}\s*{P}?\s*(.*)", re.MULTILINE),
    MdBlockType.PROOF: re.compile(r"^(?:证明|Proof)\s*[：:.．]?\s*(.*)", re.MULTILINE),
    MdBlockType.REMARK: re.compile(r"^(?:Remark|注)\s*[：:.]\s*(.*)", re.MULTILINE),
}

# 节标题模式
LECTURE_SECTION = re.compile(r"^##\s+(\d[\d.．]*)\s*{P}?\s*(.*)", re.MULTILINE)
TEXTBOOK_SECTION = re.compile(r"^##\s*§?\s*(\d[\d.．]*)\s+(.*)", re.MULTILINE)
CHAPTER_PATTERN = re.compile(r"^##?\s*第.{1,5}章\s*(.*)", re.MULTILINE)
EXERCISE_SECTION = re.compile(r"^习题", re.MULTILINE)

# 图片
IMAGE_PATTERN = re.compile(r"!\[.*?\]\((https?://[^)]+)\)")

# 乱码检测启发式
GARBLE_INDICATORS = re.compile(r"[老且∴∵故]\s*[A-Z]\s*[qQ]|[≥≤]\s*[，,]")


def detect_source_type(filepath: Path, explicit: str = "auto") -> MdSourceType:
    """检测 MD 文件来源类型"""
    if explicit == "lecture":
        return MdSourceType.LECTURE
    if explicit == "textbook":
        return MdSourceType.TEXTBOOK

    path_str = str(filepath)
    if "讲义" in path_str or "lecture" in path_str.lower():
        return MdSourceType.LECTURE
    if "课本" in path_str or "教材" in path_str or "textbook" in path_str.lower():
        return MdSourceType.TEXTBOOK

    return MdSourceType.LECTURE  # default


def _detect_block_start(line: str) -> tuple[MdBlockType, re.Match[str]] | None:
    """检测行是否是块起始"""
    for block_type, pattern in BLOCK_PATTERNS.items():
        m = pattern.match(line)
        if m:
            return block_type, m
    return None


def _is_section_header(line: str) -> bool:
    """检测行是否是节标题"""
    return bool(line.startswith("## ") or line.startswith("# "))


def _is_exercise_section(line: str) -> bool:
    return bool(EXERCISE_SECTION.match(line))


def _is_garbled(text: str) -> bool:
    """启发式判断文本是否含严重乱码"""
    if len(text) < 10:
        return False
    garble_matches = len(GARBLE_INDICATORS.findall(text))
    return garble_matches >= 2


def parse_file(
    filepath: Path,
    content: str | None = None,
    source_type: str = "auto",
) -> list[MdBlock]:
    """解析单个 MD 文件为块列表"""
    if content is None:
        content = filepath.read_text(encoding="utf-8")

    src_type = detect_source_type(filepath, source_type)
    lines = content.split("\n")
    blocks: list[MdBlock] = []

    current_type: MdBlockType | None = None
    current_number: str | None = None
    current_name: str | None = None
    current_lines: list[str] = []
    current_start = 0

    def _finalize_block() -> None:
        nonlocal current_type, current_number, current_name, current_lines, current_start
        if current_type is not None and current_lines:
            text = "\n".join(current_lines).strip()
            if text:
                images = IMAGE_PATTERN.findall(text)
                blocks.append(
                    MdBlock(
                        block_type=current_type,
                        number=current_number,
                        name=current_name,
                        content=text,
                        source_file=str(filepath),
                        line_start=current_start,
                        line_end=current_start + len(current_lines),
                        images=images,
                        has_garbled_content=_is_garbled(text),
                    )
                )
        current_type = None
        current_number = None
        current_name = None
        current_lines = []

    for i, line in enumerate(lines):
        # 跳过空行（但如果在块内则保留）
        if not line.strip() and current_type is None:
            continue

        # 节标题
        if _is_section_header(line):
            _finalize_block()
            continue

        # 习题节
        if _is_exercise_section(line):
            _finalize_block()
            current_type = MdBlockType.EXERCISE
            current_number = None
            current_name = None
            current_lines = []
            current_start = i
            continue

        # 块起始
        detected = _detect_block_start(line)
        if detected:
            _finalize_block()
            block_type, m = detected
            current_type = block_type
            current_start = i

            groups = m.groups()
            if block_type in (MdBlockType.PROOF, MdBlockType.REMARK):
                current_number = None
                current_name = None
                first_line = groups[0] if groups else ""
            elif block_type == MdBlockType.PROPOSITION:
                current_number = groups[0] if len(groups) > 0 else None
                current_name = None
                first_line = groups[1] if len(groups) > 1 else ""
            elif block_type == MdBlockType.EXAMPLE:
                current_number = groups[0] if len(groups) > 0 else None
                current_name = None
                first_line = groups[1] if len(groups) > 1 else ""
            else:
                current_number = groups[0] if len(groups) > 0 else None
                current_name = groups[1] if len(groups) > 1 else None
                first_line = groups[2] if len(groups) > 2 else ""

            # 标准化编号
            if current_number:
                current_number = current_number.replace("．", ".")

            current_lines = [first_line] if first_line else []
            continue

        # 在块内：追加行
        if current_type is not None:
            current_lines.append(line)
        else:
            # 块外文本 → TEXT 类型
            if line.strip():
                if not current_type:
                    current_type = MdBlockType.TEXT
                    current_start = i
                    current_lines = []
                current_lines.append(line)

    _finalize_block()

    # 后处理: 附加证明到定理/引理
    _attach_proofs(blocks)

    logger.info(
        "md_parse_complete",
        source_file=filepath.name,
        source_type=src_type.value,
        blocks_extracted=len(blocks),
        garbled_blocks=sum(1 for b in blocks if b.has_garbled_content),
    )

    return blocks


def _attach_proofs(blocks: list[MdBlock]) -> None:
    """将证明块附加到前一个定理/引理/推论"""
    theorem_types = {
        MdBlockType.THEOREM,
        MdBlockType.LEMMA,
        MdBlockType.COROLLARY,
        MdBlockType.PROPOSITION,
    }
    i = 0
    while i < len(blocks):
        if blocks[i].block_type == MdBlockType.PROOF and i > 0:
            # 找前一个定理类型块
            for j in range(i - 1, -1, -1):
                if blocks[j].block_type in theorem_types:
                    blocks[j].proof_content = blocks[i].content
                    blocks.pop(i)
                    break
            else:
                i += 1
        else:
            i += 1


def parse_directory(dirpath: Path, source_type: str = "auto") -> list[MdBlock]:
    """解析目录下所有 MD 文件"""
    all_blocks: list[MdBlock] = []
    for filepath in sorted(dirpath.rglob("*.md")):
        blocks = parse_file(filepath, source_type=source_type)
        all_blocks.extend(blocks)
    return all_blocks
