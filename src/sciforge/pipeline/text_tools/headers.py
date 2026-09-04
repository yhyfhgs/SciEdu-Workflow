"""T2 ``strip_running_headers``——跨页重复页眉/页脚/水印剥离（v11 II.2，M3）。

基于**跨页重复行检测**：多页文本中，若某一短行在多页的相同相对位置
（页首/页尾附近）重复出现 ≥ 阈值次，视为页眉/页脚/水印，予以剥离。
纯启发式、无 LLM；找不到重复模式则原样返回（不误伤正文）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# 页眉/页脚候选行的最大长度（超过则不太可能是页眉页脚，是正文）
_MAX_HEADER_LEN = 60
# 页首/页尾各取多少行作候选窗口
_EDGE_WINDOW = 3
# 重复次数达此比例（占总页数）才判定为页眉/页脚
_MIN_REPEAT_RATIO = 0.5


class StripHeadersResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[str] = Field(default_factory=list)  # 剥离后的行（保持原行数，剥离行置空）
    removed_indices: list[int] = Field(default_factory=list)  # 被剥离的行下标（0-based）
    detected_patterns: list[str] = Field(default_factory=list)  # 识别出的页眉/页脚文本


def strip_running_headers(pages: list[list[str]]) -> StripHeadersResult:
    """``pages``：按页切分的行列表（每页一个 list[str]）。

    页数 < 2 时无法判定"重复"，原样返回全部行、不剥离。
    """
    all_lines: list[str] = []
    page_bounds: list[tuple[int, int]] = []  # 每页在 all_lines 中的 [start, end)
    for page in pages:
        start = len(all_lines)
        all_lines.extend(page)
        page_bounds.append((start, len(all_lines)))

    if len(pages) < 2:
        return StripHeadersResult(lines=list(all_lines))

    # 收集候选：每页页首/页尾窗口内的非空短行 → 计数
    candidate_counts: dict[str, int] = {}
    for page in pages:
        edge_lines = {
            ln.strip()
            for ln in (page[:_EDGE_WINDOW] + page[-_EDGE_WINDOW:])
            if ln.strip() and len(ln.strip()) <= _MAX_HEADER_LEN
        }
        for ln in edge_lines:
            candidate_counts[ln] = candidate_counts.get(ln, 0) + 1

    threshold = max(2, int(len(pages) * _MIN_REPEAT_RATIO))
    patterns = {text for text, count in candidate_counts.items() if count >= threshold}

    removed: list[int] = []
    result_lines = list(all_lines)
    if patterns:
        for i, ln in enumerate(all_lines):
            if ln.strip() in patterns:
                result_lines[i] = ""
                removed.append(i)

    return StripHeadersResult(
        lines=result_lines,
        removed_indices=removed,
        detected_patterns=sorted(patterns),
    )
