"""T11 ``repair_ocr_noise``——常见 OCR 错误修复 + 低置信度标记（v11 II.2，M3）。

设计原则：**只做高置信度、可逆推理的确定性修复**（断字合词/中文语境标点/
明确的上下标模式）；字符级混淆（l/1/O/0/rn/m 等）风险高、误判代价大
（可能把正确的字母/数字改错），本工具**只标记 confidence_spans 供人工/
下游复核，不自动替换**——宁可不修，不可修错。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ConfusionKind = Literal["char_confusion", "punctuation", "word_break", "superscript"]

_WORD_BREAK_RE = re.compile(r"([A-Za-z]{2,})-\n([a-z]{2,})")
# 中文字符 + 英文句点(+可选空格) + 中文字符 → 疑似被 OCR 误认成英文点的中文句号
_CN_PUNCT_RE = re.compile(r"([一-鿿])\.\s?([一-鿿])")
# 字母+单个非零数字，后面紧跟运算符/逗号/右括号/行尾（常见平方项 x2+y2=1）
_SUPERSCRIPT_RE = re.compile(r"(?<![\w^])([a-zA-Z])([1-9])(?=[-+=,)\s]|$)")
# "rn" 紧邻中文字符：常见 "m" 被 OCR 拆成 "rn"（只标记，不替换）
_RN_CONFUSION_RE = re.compile(r"[一-鿿]rn|rn[一-鿿]")


class OcrConfidenceSpan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)  # 修复后文本中的偏移
    end: int = Field(ge=0)
    kind: ConfusionKind
    original: str
    suggestion: str = ""  # 空 = 仅标记，不建议具体替换（如 char_confusion）


class OcrRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repaired_text: str
    confidence_spans: list[OcrConfidenceSpan] = Field(default_factory=list)


def repair_ocr_noise(text: str) -> OcrRepairResult:
    """按优先级应用高置信度修复，附带低置信度位置标记。"""
    spans: list[OcrConfidenceSpan] = []

    def _apply(pattern: re.Pattern[str], repl_fn, kind: ConfusionKind, s: str) -> str:  # type: ignore[no-untyped-def]
        out_parts: list[str] = []
        last = 0
        for m in pattern.finditer(s):
            out_parts.append(s[last : m.start()])
            replacement = repl_fn(m)
            start_in_output = sum(len(p) for p in out_parts)
            out_parts.append(replacement)
            spans.append(
                OcrConfidenceSpan(
                    start=start_in_output,
                    end=start_in_output + len(replacement),
                    kind=kind,
                    original=m.group(0),
                    suggestion=replacement,
                )
            )
            last = m.end()
        out_parts.append(s[last:])
        return "".join(out_parts)

    repaired = _apply(_WORD_BREAK_RE, lambda m: m.group(1) + m.group(2), "word_break", text)
    repaired = _apply(
        _CN_PUNCT_RE, lambda m: f"{m.group(1)}。{m.group(2)}", "punctuation", repaired
    )
    repaired = _apply(
        _SUPERSCRIPT_RE, lambda m: f"{m.group(1)}^{m.group(2)}", "superscript", repaired
    )

    # char_confusion：只标记，不替换（在最终 repaired_text 上定位）
    for m in _RN_CONFUSION_RE.finditer(repaired):
        spans.append(
            OcrConfidenceSpan(
                start=m.start(),
                end=m.end(),
                kind="char_confusion",
                original=m.group(0),
                suggestion="",
            )
        )

    spans.sort(key=lambda s: s.start)
    return OcrRepairResult(repaired_text=repaired, confidence_spans=spans)
