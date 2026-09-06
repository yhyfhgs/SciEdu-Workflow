"""T3 ``detect_structure`` + T9 ``extract_subinterval``（v11 II.2，M3）。

T3：文本 → 结构骨架（标题层级/列表/表格区/公式块/定理环境），带 span，供
LOCATE 步骤与 FormatProfile 参考。T9：行区间 → 该区间纯正文（剥离行号
前缀），供 PatchInstruction 局部补抽把小段文本喂给 LLM（不重跑整窗）。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StructureKind = Literal["heading", "list_item", "table_row", "math_block", "env_block"]


def _strip_line_prefix(line: str) -> str:
    """剥离 ``NNNNNNN | 正文`` 行号前缀；无前缀原样返回（与 Stage1Extract 同语义）。"""
    head, sep, rest = line.partition(" | ")
    return rest if sep and head.strip().isdigit() else line


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")
_MATH_BLOCK_START_RE = re.compile(r"^\s*\$\$|^\s*\\\[")
_ENV_START_RE = re.compile(r"\\begin\{([A-Za-z*]+)\}")
_ENV_END_RE = re.compile(r"\\end\{([A-Za-z*]+)\}")


class StructureNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: StructureKind
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    level: int = 0  # heading 层级（#=1..######=6）；其余为 0
    text: str = ""


def detect_structure(lines: list[str], *, line_base: int = 1) -> list[StructureNode]:
    """扫描（已剥离行号前缀的）行，返回结构骨架节点列表（按行号升序）。

    纯确定性、0 LLM。多行结构（表格连续行/公式块/LaTeX 环境）合并为单节点。
    """
    nodes: list[StructureNode] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        no = line_base + i

        heading_m = _HEADING_RE.match(line)
        if heading_m:
            nodes.append(
                StructureNode(
                    kind="heading",
                    start_line=no,
                    end_line=no,
                    level=len(heading_m.group(1)),
                    text=heading_m.group(2).strip(),
                )
            )
            i += 1
            continue

        if _TABLE_ROW_RE.match(line):
            start = i
            while i < n and _TABLE_ROW_RE.match(lines[i]):
                i += 1
            nodes.append(
                StructureNode(
                    kind="table_row",
                    start_line=line_base + start,
                    end_line=line_base + i - 1,
                )
            )
            continue

        if _MATH_BLOCK_START_RE.match(line):
            start = i
            i += 1
            while i < n and "$$" not in lines[i] and "\\]" not in lines[i]:
                i += 1
            end = min(i, n - 1)
            nodes.append(
                StructureNode(
                    kind="math_block", start_line=line_base + start, end_line=line_base + end
                )
            )
            i = end + 1
            continue

        env_m = _ENV_START_RE.search(line)
        if env_m:
            env_name = env_m.group(1)
            start = i
            i += 1
            while i < n and not (
                (end_m := _ENV_END_RE.search(lines[i])) and end_m.group(1) == env_name
            ):
                i += 1
            end = min(i, n - 1)
            nodes.append(
                StructureNode(
                    kind="env_block",
                    start_line=line_base + start,
                    end_line=line_base + end,
                    text=env_name,
                )
            )
            i = end + 1
            continue

        if _LIST_ITEM_RE.match(line):
            nodes.append(
                StructureNode(kind="list_item", start_line=no, end_line=no, text=line.strip())
            )
            i += 1
            continue

        i += 1
    return nodes


def extract_subinterval(
    window_lines: list[str],
    start_line: int,
    end_line: int,
    *,
    line_base: int,
    context_lines: int = 0,
) -> str:
    """取 ``[start_line, end_line]`` 全局行号区间的纯正文（已剥离行号前缀）。

    ``context_lines > 0`` 时向两侧各扩展该行数（供 PatchInstruction 附带上下文，
    行范围裁剪到窗口边界内）。行号越界返回空串。
    """
    n = len(window_lines)
    if n == 0:
        return ""
    s_idx = max(0, start_line - line_base - context_lines)
    e_idx = min(n - 1, end_line - line_base + context_lines)
    if s_idx > e_idx:
        return ""
    return "\n".join(_strip_line_prefix(ln) for ln in window_lines[s_idx : e_idx + 1])
