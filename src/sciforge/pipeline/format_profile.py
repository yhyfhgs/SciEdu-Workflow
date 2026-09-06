"""早期 Format/Genre Profile（v11 §E.0，M1）——轻量画像驱动窗口/体裁策略。

纯确定性、0 LLM、计算 < 50ms。对**已支持格式**给出"该怎么提取"的策略参数
（窗口大小、fast-skip、halo 宽度、carry 先验），不解析新格式（完整 adapter 在 M7）。

开关 ``format_profile_enabled = False`` 默认关：关闭时 Stage1 完全不调用本模块，
体裁探测走 v10 的 ``detect_genre``（四值）+ REC-14 白名单，行为零变化。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.pipeline.source_classifier import detect_genre

Density = Literal["sparse", "normal", "dense"]

# 窗口自适应的全局硬边界（无论体裁/密度如何都不越过）
MIN_BASE_WINDOW = 200
MAX_BASE_WINDOW = 1500


class GenreStrategy(BaseModel):
    """体裁策略表的一行（Docs/20 §E.0 12 维表的 M1 落地子集）。

    ``near_tail_margin`` 与 ``carry_prior`` 由后续里程碑消费（M2 carry 四态 /
    near-tail 调参），M1 先随 profile 计算出来供观测。
    """

    model_config = ConfigDict(frozen=True)

    fast_skip_recommended: bool
    window_range: tuple[int, int]  # (dense 端, sparse 端)
    halo_lines: int  # M2 segment halo 建议宽度
    near_tail_margin: int
    carry_prior: Literal["content_truncated", "answer_deferred", "truly_no_answer"]
    marker_style: Literal["textbook", "question_number", "mixed"]
    has_inline_answers: bool


GENRE_STRATEGIES: dict[str, GenreStrategy] = {
    "book": GenreStrategy(
        fast_skip_recommended=True,
        window_range=(800, 1200),
        halo_lines=100,
        near_tail_margin=15,
        carry_prior="content_truncated",
        marker_style="textbook",
        has_inline_answers=True,
    ),
    "exam_paper": GenreStrategy(
        fast_skip_recommended=False,  # REC-14：短题可能无关键字，关 fast-skip 提召回
        window_range=(200, 350),
        halo_lines=60,
        near_tail_margin=5,
        carry_prior="answer_deferred",  # 答案常集中卷末
        marker_style="question_number",
        has_inline_answers=False,
    ),
    "lecture": GenreStrategy(
        fast_skip_recommended=False,  # REC-14：讲义无编号要点易被 regex 预检误跳
        window_range=(500, 600),
        halo_lines=80,
        near_tail_margin=10,
        carry_prior="content_truncated",
        marker_style="mixed",
        has_inline_answers=True,
    ),
    "problem_set": GenreStrategy(
        fast_skip_recommended=False,  # 题型混杂
        window_range=(400, 600),
        halo_lines=80,
        near_tail_margin=8,
        carry_prior="truly_no_answer",  # 习题集常无解答
        marker_style="question_number",
        has_inline_answers=False,
    ),
    "unknown": GenreStrategy(
        fast_skip_recommended=True,  # 与 v10 默认一致
        window_range=(500, 800),
        halo_lines=80,
        near_tail_margin=10,
        carry_prior="content_truncated",
        marker_style="mixed",
        has_inline_answers=True,
    ),
}


class GenreProfile(BaseModel):
    """体裁画像：extended detect_genre + 策略表行。"""

    model_config = ConfigDict(extra="forbid")

    genre: str = "unknown"
    fast_skip_recommended: bool = True
    has_inline_answers: bool = True
    marker_style: str = "mixed"
    carry_prior: str = "content_truncated"
    near_tail_margin: int = 10


class FormatProfile(BaseModel):
    """格式画像：长短/密度自适应的窗口与 halo 建议。"""

    model_config = ConfigDict(extra="forbid")

    source_format: str = "markdown"
    avg_line_length: int = Field(ge=0, default=0)
    density: Density = "normal"
    ocr_noisy: bool = False
    suggested_base_window: int = Field(ge=MIN_BASE_WINDOW, le=MAX_BASE_WINDOW)
    suggested_halo_lines: int = Field(ge=0, default=0)


# ── 密度/噪声启发 ─────────────────────────────────────────

# 条目起始样式行（轻量版，正式真值清单是 text_tools.find_item_markers / T4）
_ITEM_START_RE = re.compile(
    r"^\s*(?:\d+\s*[.、．]\s+\S"  # 裸编号题 "12. 设…"
    r"|[(（]\s*\d+\s*[)）]"  # (1) 小问
    r"|第\s*[\d一二三四五六七八九十]+\s*题"
    r"|(?:\*{0,2})(?:定义|定理|引理|推论|命题|例题?|习题|练习|问题)\s*[\d.．]*"
    r"|\\begin\{(?:definition|theorem|lemma|corollary|example|exercise|problem)\})",
)


def estimate_density(lines: list[str]) -> Density:
    """条目起始行密度：每 100 行条目数 > 4 → dense；< 1 → sparse；否则 normal。"""
    if not lines:
        return "normal"
    starts = sum(1 for ln in lines if _ITEM_START_RE.match(ln))
    per_100 = starts * 100 / len(lines)
    if per_100 > 4:
        return "dense"
    if per_100 < 1:
        return "sparse"
    return "normal"


def _looks_ocr_noisy(text: str) -> bool:
    """保守的 OCR 噪声探测：替换符出现或全角/半角数字混排比例异常。"""
    if "�" in text:
        return True
    fullwidth_digits = len(re.findall(r"[０-９]", text))
    halfwidth_digits = len(re.findall(r"[0-9]", text))
    total = fullwidth_digits + halfwidth_digits
    # 全角数字显著混入（>15%）通常来自 OCR/排版转换
    return total > 50 and fullwidth_digits / total > 0.15


# ── 画像计算 ──────────────────────────────────────────────


def compute_genre_profile(md_text: str) -> GenreProfile:
    """extended 体裁探测（含 problem_set）+ 策略表查行。"""
    genre = detect_genre(md_text, extended=True)
    strategy = GENRE_STRATEGIES.get(genre, GENRE_STRATEGIES["unknown"])
    return GenreProfile(
        genre=genre,
        fast_skip_recommended=strategy.fast_skip_recommended,
        has_inline_answers=strategy.has_inline_answers,
        marker_style=strategy.marker_style,
        carry_prior=strategy.carry_prior,
        near_tail_margin=strategy.near_tail_margin,
    )


def compute_format_profile(
    md_text: str,
    *,
    genre: str,
    source_format: str = "markdown",
) -> FormatProfile:
    """长短/密度自适应：在体裁窗口区间内按密度取点，夹到全局硬边界。

    dense → 区间低端（密集短题防漏），sparse → 高端（长文省调用），normal → 中点。
    """
    lines = md_text.splitlines()
    strategy = GENRE_STRATEGIES.get(genre, GENRE_STRATEGIES["unknown"])
    density = estimate_density(lines)
    low, high = strategy.window_range
    if density == "dense":
        window = low
    elif density == "sparse":
        window = high
    else:
        window = (low + high) // 2
    avg_len = sum(len(ln) for ln in lines) // len(lines) if lines else 0
    return FormatProfile(
        source_format=source_format,
        avg_line_length=avg_len,
        density=density,
        ocr_noisy=_looks_ocr_noisy(md_text),
        suggested_base_window=max(MIN_BASE_WINDOW, min(window, MAX_BASE_WINDOW)),
        suggested_halo_lines=strategy.halo_lines,
    )
