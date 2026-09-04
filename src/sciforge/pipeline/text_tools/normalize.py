"""T1 ``normalize_text``——文本归一化，保留 char 映射表（v11 II.2，M3）。

归一化操作：全角→半角数字/字母、Unicode NFKC、连字符/引号统一、零宽字符剥离。
返回归一化文本 + 每个输出字符对应的原文偏移映射（供下游需要把归一化后的
匹配位置换算回原文 offset 的场景，如 T4/T10 在归一化文本上跑规则后回填）。
"""

from __future__ import annotations

import unicodedata

from pydantic import BaseModel, ConfigDict, Field

# 零宽字符（ZWSP/ZWNJ/ZWJ/BOM）—— NFKC 不处理这些，需显式剥离
_ZERO_WIDTH = "​‌‍﻿"
# 引号/连字符统一为 ASCII 直引号与连字符
_QUOTE_HYPHEN_MAP = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",
    "—": "-",
    "‐": "-",
}


class NormalizedText(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    # offset_map[i] = 原文中对应 text[i] 的偏移（char_map，供回填字符级溯源）
    offset_map: list[int] = Field(default_factory=list)


def normalize_text(raw: str) -> NormalizedText:
    """归一化并保留逐字符偏移映射。

    NFKC 可能改变字符数（如全角 Ａ→A 是 1:1；但某些组合字符 1:N 或 N:1），
    因此映射基于 NFKC **逐字符**规范化（每个输入字符独立转换），牺牲极少数
    多字符组合的规范化能力换取偏移映射的严格正确性。
    """
    out_chars: list[str] = []
    offset_map: list[int] = []
    for i, ch in enumerate(raw):
        if ch in _ZERO_WIDTH:
            continue
        mapped = _QUOTE_HYPHEN_MAP.get(ch, ch)
        normalized = unicodedata.normalize("NFKC", mapped)
        for out_ch in normalized:
            out_chars.append(out_ch)
            offset_map.append(i)
    return NormalizedText(text="".join(out_chars), offset_map=offset_map)
