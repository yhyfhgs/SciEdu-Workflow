"""DeduplicationEngine — 跨文档内容去重

支持精确去重（content hash）和近似去重（MinHash Jaccard 估计）。
标记重复而非静默丢弃，允许用户审查。
"""

from __future__ import annotations

import hashlib
import re
import struct

import structlog

from mathforge.core.models import UnifiedItemBase

logger = structlog.get_logger()

# LaTeX 归一化用的正则
_WHITESPACE_RE = re.compile(r"\s+")
_LATEX_CMD_RE = re.compile(
    r"\\(?:left|right|big|Big|bigg|Bigg|text|mathrm|operatorname|displaystyle)"
)
_LATEX_FRAC_RE = re.compile(r"\\d?frac")


def normalize_latex(text: str) -> str:
    """归一化 LaTeX 内容用于比较"""
    text = _WHITESPACE_RE.sub(" ", text)
    text = _LATEX_CMD_RE.sub("", text)
    text = _LATEX_FRAC_RE.sub("\\frac", text)  # dfrac → frac 统一
    return text.strip().lower()


def content_hash(text: str) -> str:
    """归一化后取 SHA-256 前 32 位"""
    return hashlib.sha256(normalize_latex(text).encode()).hexdigest()[:32]


def _tokenize(text: str) -> set[str]:
    """将归一化文本拆分为 token 集合

    使用 2-gram 增强区分度，减少单字符变量名带来的假阳性。
    """
    normalized = normalize_latex(text)
    # 基础 token：单词、LaTeX 命令、数字
    tokens = re.findall(r"[a-zA-Z\u4e00-\u9fff]{2,}|\\[a-zA-Z]+|\d+", normalized)
    # 添加 bigram 提高区分度
    result = set(tokens)
    for i in range(len(tokens) - 1):
        result.add(f"{tokens[i]}_{tokens[i + 1]}")
    return result


# ── MinHash 近似去重 ──

_NUM_HASHES = 128  # MinHash 签名长度
_MAX_HASH = (1 << 32) - 1

# 预生成随机哈希系数 (a, b)
_HASH_COEFFS: list[tuple[int, int]] = []


def _ensure_coeffs() -> None:
    """延迟初始化哈希系数"""
    if _HASH_COEFFS:
        return
    import random

    rng = random.Random(42)  # 固定种子保证可复现
    for _ in range(_NUM_HASHES):
        a = rng.randint(1, _MAX_HASH)
        b = rng.randint(0, _MAX_HASH)
        _HASH_COEFFS.append((a, b))


def _minhash_signature(tokens: set[str]) -> bytes:
    """计算 token 集合的 MinHash 签名"""
    _ensure_coeffs()
    # 先把 token 映射为整数
    token_hashes = [
        struct.unpack("<I", hashlib.md5(t.encode()).digest()[:4])[0]  # noqa: S324
        for t in tokens
    ]
    if not token_hashes:
        return b"\xff" * (_NUM_HASHES * 4)

    sig = []
    for a, b in _HASH_COEFFS:
        min_val = min(((a * h + b) & _MAX_HASH) for h in token_hashes)
        sig.append(min_val)
    return struct.pack(f"<{_NUM_HASHES}I", *sig)


def _minhash_jaccard(sig1: bytes, sig2: bytes) -> float:
    """从两个 MinHash 签名估计 Jaccard 相似度"""
    vals1 = struct.unpack(f"<{_NUM_HASHES}I", sig1)
    vals2 = struct.unpack(f"<{_NUM_HASHES}I", sig2)
    matches = sum(1 for a, b in zip(vals1, vals2) if a == b)
    return matches / _NUM_HASHES


class DeduplicationEngine:
    """跨文档内容去重引擎

    精确去重: SHA-256 content hash — O(1)
    近似去重: MinHash 签名估计 Jaccard 相似度 — O(n) 但常数极小
    """

    def __init__(self) -> None:
        self._hashes: dict[str, str] = {}  # hash → first item_id
        self._signatures: dict[str, bytes] = {}  # item_id → MinHash 签名

    def check(self, item: UnifiedItemBase, threshold: float = 0.85) -> str | None:
        """检查条目是否重复

        返回重复的 item_id，若无重复返回 None。
        """
        # 使用 content_latex 或 content_plain 作为去重内容
        content = item.content_latex or item.content_plain
        if not content or not content.strip():
            return None  # 空内容不参与去重，避免所有空条目互相匹配

        # 精确去重
        ch = content_hash(content)
        if ch in self._hashes:
            dup_id = self._hashes[ch]
            logger.info(
                "dedup_exact_match",
                item_id=item.item_id,
                duplicate_of=dup_id,
            )
            return dup_id

        # 近似去重 (MinHash)
        tokens = _tokenize(content)
        if len(tokens) >= 5:  # 太短的内容不做近似比较
            sig = _minhash_signature(tokens)
            for existing_id, existing_sig in self._signatures.items():
                jaccard = _minhash_jaccard(sig, existing_sig)
                if jaccard >= threshold:
                    logger.info(
                        "dedup_near_match",
                        item_id=item.item_id,
                        duplicate_of=existing_id,
                        jaccard=round(jaccard, 2),
                    )
                    return existing_id

            # 注册签名
            self._signatures[item.item_id] = sig

        # 注册为新条目
        self._hashes[ch] = item.item_id
        return None

    def register(self, item: UnifiedItemBase) -> None:
        """手动注册条目（跳过去重检查）"""
        content = item.content_latex or item.content_plain
        if not content or not content.strip():
            return
        ch = content_hash(content)
        self._hashes[ch] = item.item_id
        tokens = _tokenize(content)
        if len(tokens) >= 5:
            self._signatures[item.item_id] = _minhash_signature(tokens)
