"""T13 ``parse_structured_q``——选择/填空/表格题结构化解析（v11 II.2，M3）。

对齐现有 ``Question.options`` / ``correct_option`` / ``blanks``（REC-19）。
识别多种真实排版噪声（同行/带括号/逐行选项，各类填空占位符），**未识别
返回空而非伪造**——宁可留给下游 LLM 判断，不臆造结构。
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

# 选项标签：A/B/C/D/E/F，允许后接 . 、) ．
_OPTION_LABEL = r"[A-F][.、)．]"
# 逐行选项：整行以选项标签开头
_OPTION_LINE_RE = re.compile(rf"^\s*({_OPTION_LABEL})\s*(.*)$")
# 同行内联选项：A. xxx B. xxx C. xxx（贪婪匹配到下一个标签或行尾）
_INLINE_OPTION_RE = re.compile(
    rf"({_OPTION_LABEL})\s*(.+?)(?=\s*{_OPTION_LABEL}|$)",
)
# 带括号选项头：(A) xxx （B）xxx
_PAREN_OPTION_RE = re.compile(
    r"[(（]([A-F])[)）]\s*(.+?)(?=\s*[(（][A-F][)）]|$)",
)
# 填空占位符：下划线（半角3+/全角2+）、空括号（可含内部全角/半角空格）
_BLANK_RE = re.compile(r"_{3,}|＿{2,}|[（(]\s*[）)]")
# 正确答案标注："答案：C" / "正确答案是(B)" / "答案为 A"
_ANSWER_RE = re.compile(r"(?:答案|正确答案)\s*(?:是|为|：|:)\s*[(（]?([A-F])[)）]?")


class StructuredQuestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options: list[str] = Field(default_factory=list)
    correct_option: str | None = None
    blanks: int | None = None
    # 表格题：markdown table 每行拆出的子题文本（未检测到表格则为空）
    table_subquestions: list[list[str]] = Field(default_factory=list)


def _parse_options(text: str) -> list[str]:
    lines = text.splitlines()
    # ① 逐行排版：优先识别（更明确，避免同行/括号误判逐行文本的一部分）
    line_options = [
        f"{m.group(1)[0]}. {m.group(2).strip()}" for ln in lines if (m := _OPTION_LINE_RE.match(ln))
    ]
    if len(line_options) >= 2:
        return line_options

    # ② 带括号：(A) xxx (B) xxx
    paren_matches = _PAREN_OPTION_RE.findall(text)
    if len(paren_matches) >= 2:
        return [f"{label}. {content.strip()}" for label, content in paren_matches]

    # ③ 同行内联：A. xxx B. xxx
    inline_matches = _INLINE_OPTION_RE.findall(text)
    if len(inline_matches) >= 2:
        return [f"{label[0]}. {content.strip()}" for label, content in inline_matches]

    return []


def parse_structured_q(text: str) -> StructuredQuestionResult:
    """解析题面结构化字段；无法识别的维度保持默认空值（不臆造）。"""
    options = _parse_options(text)

    blank_matches = _BLANK_RE.findall(text)
    blanks = len(blank_matches) if blank_matches else None

    answer_m = _ANSWER_RE.search(text)
    correct_option = answer_m.group(1) if answer_m else None

    table_subquestions: list[list[str]] = []
    table_rows = [ln.strip() for ln in text.splitlines() if re.match(r"^\s*\|.*\|\s*$", ln)]
    # 跳过 markdown 表格的表头分隔行（|---|---|）
    data_rows = [r for r in table_rows if not re.fullmatch(r"\|[\s:|-]+\|", r)]
    for row in data_rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if any(cells):
            table_subquestions.append(cells)

    return StructuredQuestionResult(
        options=options,
        correct_option=correct_option,
        blanks=blanks,
        table_subquestions=table_subquestions,
    )
