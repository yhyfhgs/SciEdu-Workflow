"""T5 ``resolve_span``——锚点短串 → 精确字符区间（v11 II.4，M1）。

核心决策：**不让 LLM 输出整数 offset**（中文+LaTeX 混排时数字符极不可靠），
LLM 只抄"条目开头/结尾的逐字短串"+ 粗行号，由本模块确定性定位精确偏移。
按可靠性降级：exact（唯一/近提示命中）→ fuzzy（difflib ≥ 阈值）→
line_fallback（行级，等价现状，最坏不劣化）。

M1 偏离 Docs/20 II.4 一处：ResolvedSpan.text **不写回** corrected_content——
锚点覆盖整条目（含解答/证明），covering span 覆写会把解答并进题干、
破坏 content/solution 分离与 content_hash 去重语义；写回留待内容/解答
分段锚点（4 锚点方案）落地时再做。本版只填 resolved_start/end_char 溯源列。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.pipeline.text_tools.markers import ITEM_KINDS, find_item_markers

DEFAULT_FUZZY_THRESHOLD = 0.86
_HINT_LINE_TOLERANCE = 5  # 多候选时允许偏离提示行的最大行数
_FUZZY_LINE_RANGE = 2  # 模糊匹配在提示行 ±N 行内滑动


class ResolvedSpan(BaseModel):
    """精确字符区间（行号 1-based 全局，char 为行内 0-based codepoint，end exclusive）。"""

    model_config = ConfigDict(extra="forbid")

    start_line: int = Field(ge=1)
    start_char: int = Field(ge=0)
    end_line: int = Field(ge=1)
    end_char: int = Field(ge=0)
    text: str = ""
    confidence: Literal["exact", "fuzzy", "line_fallback"] = "exact"
    resolver_note: str = ""


def _find_all(lines: list[str], needle: str) -> list[tuple[int, int]]:
    """needle 在各行中的全部命中 → [(line_idx, char)]。"""
    hits: list[tuple[int, int]] = []
    for idx, line in enumerate(lines):
        pos = line.find(needle)
        while pos != -1:
            hits.append((idx, pos))
            pos = line.find(needle, pos + 1)
    return hits


def _fuzzy_locate(
    lines: list[str], needle: str, hint_idx: int, threshold: float
) -> tuple[int, int, float] | None:
    """提示行 ±N 行内滑动窗口取最高相似度 → (line_idx, char, ratio)。"""
    best: tuple[int, int, float] | None = None
    lo = max(0, hint_idx - _FUZZY_LINE_RANGE)
    hi = min(len(lines), hint_idx + _FUZZY_LINE_RANGE + 1)
    width = len(needle)
    for idx in range(lo, hi):
        line = lines[idx]
        if not line:
            continue
        for pos in range(0, max(1, len(line) - width + 1)):
            ratio = SequenceMatcher(None, needle, line[pos : pos + width]).ratio()
            if ratio >= threshold and (best is None or ratio > best[2]):
                best = (idx, pos, ratio)
    return best


def _locate(
    lines: list[str],
    anchor: str,
    hint_idx: int,
    threshold: float,
) -> tuple[int, int, str] | None:
    """定位锚点 → (line_idx, char, confidence)；找不到返回 None。"""
    hits = _find_all(lines, anchor)
    if len(hits) == 1:
        return (*hits[0], "exact")
    if len(hits) > 1:
        near = [h for h in hits if abs(h[0] - hint_idx) <= _HINT_LINE_TOLERANCE]
        pool = near or hits
        best = min(pool, key=lambda h: abs(h[0] - hint_idx))
        return (*best, "exact")
    fuzzy = _fuzzy_locate(lines, anchor, hint_idx, threshold)
    if fuzzy is not None:
        return fuzzy[0], fuzzy[1], "fuzzy"
    return None


def _slice_text(lines: list[str], s_idx: int, s_char: int, e_idx: int, e_char: int) -> str:
    if s_idx == e_idx:
        return lines[s_idx][s_char:e_char]
    parts = [lines[s_idx][s_char:]]
    parts.extend(lines[s_idx + 1 : e_idx])
    parts.append(lines[e_idx][:e_char])
    return "\n".join(parts)


def resolve_span(
    start_anchor: str,
    end_anchor: str,
    window_lines: list[str],
    *,
    line_base: int,
    start_line: int,
    end_line: int,
    fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD,
) -> ResolvedSpan:
    """锚点短串 + 粗行号 → 精确字符区间（纯确定性，最坏退回行级）。

    ``window_lines`` 为已剥离行号前缀的窗口行；``start_line``/``end_line``
    为 LLM 给出的粗定位（全局行号）。
    """
    n = len(window_lines)
    if n == 0:
        return ResolvedSpan(
            start_line=start_line,
            start_char=0,
            end_line=max(end_line, start_line),
            end_char=0,
            confidence="line_fallback",
            resolver_note="empty_window",
        )
    s_hint = min(max(start_line - line_base, 0), n - 1)
    e_hint = min(max(end_line - line_base, 0), n - 1)

    notes: list[str] = []
    start_hit = (
        _locate(window_lines, start_anchor, s_hint, fuzzy_threshold) if start_anchor else None
    )
    end_hit = _locate(window_lines, end_anchor, e_hint, fuzzy_threshold) if end_anchor else None

    if start_hit is not None:
        s_idx, s_char, s_conf = start_hit
    else:
        s_idx, s_char, s_conf = s_hint, 0, "line_fallback"
        notes.append("start_anchor_unresolved")
    if end_hit is not None:
        e_idx, e_pos, e_conf = end_hit
        e_char = e_pos + len(end_anchor)  # 锚点结尾（exclusive）
        e_char = min(e_char, len(window_lines[e_idx]))
    else:
        e_idx, e_char, e_conf = e_hint, len(window_lines[e_hint]), "line_fallback"
        notes.append("end_anchor_unresolved")

    # end >= start 约束：违反时退回行级粗定位
    if (e_idx, e_char) < (s_idx, s_char):
        s_idx, s_char = s_hint, 0
        e_idx, e_char = e_hint, len(window_lines[e_hint])
        s_conf = e_conf = "line_fallback"
        notes.append("end_before_start")

    # 防多余尾巴（确定性回退）：跨行 span 的末行若以另一强条目标记开头 → 回退到上一行行尾
    if e_idx > s_idx:
        tail_markers = find_item_markers([window_lines[e_idx]], line_base=1)
        if any(m.kind in ITEM_KINDS and m.strength == "strong" for m in tail_markers):
            e_idx -= 1
            e_char = len(window_lines[e_idx])
            notes.append("trailing_marker_backoff")

    if "line_fallback" in (s_conf, e_conf):
        confidence: Literal["exact", "fuzzy", "line_fallback"] = "line_fallback"
    elif "fuzzy" in (s_conf, e_conf):
        confidence = "fuzzy"
    else:
        confidence = "exact"

    return ResolvedSpan(
        start_line=line_base + s_idx,
        start_char=s_char,
        end_line=line_base + e_idx,
        end_char=e_char,
        text=_slice_text(window_lines, s_idx, s_char, e_idx, e_char),
        confidence=confidence,
        resolver_note=";".join(notes),
    )
