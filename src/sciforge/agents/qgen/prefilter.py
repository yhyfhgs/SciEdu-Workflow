"""候选题目轻量预筛 — 在进入 solve-verify 链路前做 zero-cost 过滤。

策略：
1. 内容非空且长度 > 30
2. LaTeX 括号平衡（`{` / `}`、`\\(` / `\\)`、`$` / `$`）
3. 与 negative examples 的 TF-IDF 相似度 < 阈值

失败时记录理由到日志，返回 False 让主链路直接跳过。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sciforge.core.models import Question

logger = structlog.get_logger()

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z][a-zA-Z0-9_]*|\\[a-zA-Z]+|\d+")

MIN_CONTENT_LEN = 30
MAX_NEGATIVE_SIMILARITY = 0.9


def check_structural_validity(content: str) -> tuple[bool, str]:
    """检查内容长度 + 括号平衡。

    Returns:
        (is_valid, reason_if_invalid)
    """
    if not content or len(content.strip()) < MIN_CONTENT_LEN:
        return False, f"content_too_short(len={len(content.strip())})"

    # 花括号平衡
    if content.count("{") != content.count("}"):
        return False, (f"unbalanced_braces(open={content.count('{')}, close={content.count('}')})")

    # $ 成对（LaTeX inline math）
    # 排除 $$ 的情况下，$ 必须是偶数
    stripped = content.replace("$$", "")
    dollar_count = stripped.count("$")
    if dollar_count % 2 != 0:
        return False, f"unbalanced_dollar(count={dollar_count})"

    return True, ""


def _tokenize(text: str) -> list[str]:
    """简单中英文混合 tokenizer：中文按字切、英文按单词切、LaTeX 命令按整体切。"""
    return _TOKEN_RE.findall(text.lower())


def _tf_vector(text: str) -> Counter[str]:
    return Counter(_tokenize(text))


def cosine_similarity(a: Counter[str], b: Counter[str]) -> float:
    """TF 向量的余弦相似度（简化版，不做 IDF）。"""
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def check_novelty(content: str, negative_examples: list[Question]) -> tuple[bool, str]:
    """检查与已有负样本的文本相似度。

    Returns:
        (is_novel, reason_if_too_similar)
    """
    if not negative_examples:
        return True, ""
    cand_vec = _tf_vector(content)
    for neg in negative_examples:
        neg_content = getattr(neg, "content", "")
        if not neg_content:
            continue
        neg_vec = _tf_vector(neg_content)
        sim = cosine_similarity(cand_vec, neg_vec)
        if sim >= MAX_NEGATIVE_SIMILARITY:
            return False, f"too_similar_to_neg(id={getattr(neg, 'id', '?')}, sim={sim:.2f})"
    return True, ""


def check_question_form(candidate: Question) -> tuple[bool, str]:
    """Validate structured form fields without an LLM call."""

    form = candidate.question_form
    if form in {"choice", "multiple_choice"}:
        if len(candidate.options) < 2:
            return False, f"{form}_requires_at_least_two_options"
        if not (candidate.correct_option or "").strip():
            return False, f"{form}_requires_correct_option"
    elif form == "fill_blank" and (candidate.blanks is None or candidate.blanks < 1):
        return False, "fill_blank_requires_positive_blanks"
    elif form == "true_false" and not (candidate.correct_option or "").strip():
        return False, "true_false_requires_correct_option"
    return True, ""


def prefilter_candidate(candidate: Question, negative_examples: list[Question]) -> tuple[bool, str]:
    """对候选做全部 zero-cost 检查。

    Returns:
        (should_process, reject_reason)
        should_process=True 表示通过预筛，继续 solve-verify
        False 表示被预筛拒绝，跳过，reject_reason 用于 logging
    """
    content = getattr(candidate, "content", "") or ""

    ok, reason = check_structural_validity(content)
    if not ok:
        logger.info("qgen_prefilter_reject", reason=reason, stage="structural")
        return False, reason

    ok, reason = check_question_form(candidate)
    if not ok:
        logger.info("qgen_prefilter_reject", reason=reason, stage="question_form")
        return False, reason

    ok, reason = check_novelty(content, negative_examples)
    if not ok:
        logger.info("qgen_prefilter_reject", reason=reason, stage="novelty")
        return False, reason

    return True, ""
