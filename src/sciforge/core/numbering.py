"""题号规范化工具 — 把自由文本题号统一为规范形态 + 结构化序号

解决的问题（#4）：原始文档里同一编号有多种写法并存——
    "例 3.2" / "定理3.2" / "第1题" / "1．2．3"（全角句号）/ "Exercise 5" / "(3.2)"
这些进库前必须归一，否则无法做编号连续性核对、无法稳定排序/去重。

核心 API：
- ``canonicalize_number(raw)`` → 规范化字符串（如 "3.2"、"1"、"3.2.1"）
- ``parse_number_components(raw)`` → (chapter, section, index) 三元组，供连续性 audit

设计原则：
- 只做**确定性**的格式归一（全角→半角、剥离条目关键字/包装词、统一分隔符）；
  不猜测、不补全。无法解析为数字序列时返回空串 + 全 None（保留 raw 供人工核对）。
- 幂等：``canonicalize_number(canonicalize_number(x)) == canonicalize_number(x)``。
"""

from __future__ import annotations

import re

# 全角数字 → 半角
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# 各种"分隔/句号"统一为半角 '.'：全角句号．、表意句号。、间隔号·•、全角点
_SEPARATOR_CHARS = "．。·•‧・　"  # 末尾是全角空格 U+3000
_SEPARATOR_TRANS = {ord(c): "." for c in _SEPARATOR_CHARS}

# 条目关键字前缀（中英文），出现在编号前需剥离
# 注意：用 | 连接，按长度优先匹配（"例题" 在 "例" 前）
_PREFIX_KEYWORDS = (
    "定理|定义|引理|推论|命题|性质|公式|例题|例|习题|练习|问题|题|注记|注|备注|"
    "方法|结论|法则|算法|定律|原理|实验|模型|公理|"
    r"Theorem|Definition|Lemma|Corollary|Proposition|Property|Formula|"
    r"Example|Exercise|Problem|Remark|Method|Algorithm|Law|Principle|Axiom"
)

# "第 X 题/章/节/部分" 包装：提取中间数字
_ORDINAL_WRAP_RE = re.compile(r"第\s*([0-9]+)\s*[题章节讲部分课]")

# 行首前缀关键字 + 可选空白（剥离）
_PREFIX_RE = re.compile(rf"^\s*(?:{_PREFIX_KEYWORDS})\s*", re.IGNORECASE)

# 规范化后提取核心数字序列：连续的 数字(.数字)* 模式
_NUMERIC_SEQ_RE = re.compile(r"[0-9]+(?:\.[0-9]+)*")


def canonicalize_number(raw: str) -> str:
    """把原始题号规范化为 ``"3.2"`` / ``"1"`` / ``"3.2.1"`` 形态。

    步骤：全角→半角 → 剥离条目关键字前缀 → 处理"第N题"包装 →
    统一分隔符为 '.' → 提取第一个数字序列。无数字则返回 ""。

    Examples:
        "例 3.2"      -> "3.2"
        "定理3.1.2"   -> "3.1.2"
        "第1题"        -> "1"
        "1．2．3"      -> "1.2.3"   (全角句号)
        "Exercise 5"  -> "5"
        "(3.2)"       -> "3.2"
        "引言"         -> ""        (无数字)
    """
    if not raw:
        return ""

    s = raw.strip()
    # 1. 全角数字 → 半角
    s = s.translate(_FULLWIDTH_DIGITS)
    # 2. "第 N 题/章/节" 包装优先（在剥前缀前处理，避免 "第" 被误伤）
    wrap = _ORDINAL_WRAP_RE.search(s)
    if wrap:
        return wrap.group(1)
    # 3. 分隔符统一为 '.'
    s = s.translate(_SEPARATOR_TRANS)
    # 4. 剥离条目关键字前缀（可能多重，如 "例题 例 3.2" → 循环剥）
    prev = None
    while prev != s:
        prev = s
        s = _PREFIX_RE.sub("", s)
    # 5. 提取第一个数字序列
    m = _NUMERIC_SEQ_RE.search(s)
    if not m:
        return ""
    # 去掉末尾可能的多余 '.'（如 "3." → "3"）
    return m.group(0).rstrip(".")


def parse_number_components(raw: str) -> tuple[int | None, int | None, int | None]:
    """解析题号为 ``(chapter, section, index)`` 三元组，供编号连续性 audit。

    先 ``canonicalize_number`` 再按 '.' 拆分，位置语义：
    - 1 段 ``"5"``        -> (None, None, 5)        # 章内裸序号
    - 2 段 ``"3.2"``      -> (3, None, 2)           # 章.序号
    - 3 段 ``"3.2.1"``    -> (3, 2, 1)              # 章.节.序号
    - ≥4 段：取首、次、末三个为 chapter/section/index

    ``index`` 始终是最后一段（章/节内的运行序号，audit 检查它的连续性）。
    无法解析时返回 ``(None, None, None)``。
    """
    canonical = canonicalize_number(raw)
    if not canonical:
        return (None, None, None)
    parts = [int(p) for p in canonical.split(".") if p.isdigit()]
    if not parts:
        return (None, None, None)
    if len(parts) == 1:
        return (None, None, parts[0])
    if len(parts) == 2:
        return (parts[0], None, parts[1])
    # len >= 3
    return (parts[0], parts[1], parts[-1])
