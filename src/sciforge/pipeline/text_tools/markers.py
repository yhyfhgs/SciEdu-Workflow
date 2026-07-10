"""T4 ``find_item_markers``——条目起始标记的确定性真值扫描（marker ledger 底座）。

与 fast-skip 预检（``stage1_extract._ITEM_MARKER_RE``，召回导向、行内任意位置命中）
不同，本工具是**精度导向**：只认行首标记（允许前导空白 / ``**`` 粗体 / LaTeX 环境），
避免把正文引用（"由定理 3.1 可知"）计入真值清单。清单用于覆盖率对账查漏（III.1），
误报会直接抬高 missed_marker 噪声。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.core.numbering import canonicalize_number

MarkerKind = Literal[
    "definition",
    "theorem",
    "lemma",
    "corollary",
    "proposition",
    "property",
    "formula",
    "law",
    "principle",
    "reaction",
    "mechanism",
    "model",
    "experiment",
    "method",
    "process",
    "structure",
    "function",
    "classification",
    "fact",
    "remark",
    "example",
    "exercise",
    "problem",
    "solution",
    "proof",
    "section",
]

# 可作为独立条目起始的 marker 类型（solution/proof 属条目本体、section 是结构）
ITEM_KINDS: frozenset[str] = frozenset(
    {
        "definition",
        "theorem",
        "lemma",
        "corollary",
        "proposition",
        "property",
        "formula",
        "law",
        "principle",
        "reaction",
        "mechanism",
        "model",
        "experiment",
        "method",
        "process",
        "structure",
        "function",
        "classification",
        "fact",
        "remark",
        "example",
        "exercise",
        "problem",
    }
)


class MarkerEntry(BaseModel):
    """一处条目起始标记（真值清单的一行，Docs/20 III.1）。"""

    model_config = ConfigDict(extra="forbid")

    global_line: int = Field(ge=1)
    char_offset: int = Field(ge=0, default=0)  # 行内 0-based（标记起始列）
    marker_text: str
    kind: MarkerKind
    number_hint: str = ""
    # strong = 关键字/环境明确；weak = 裸编号行（可能是解答步骤等，审计降级处理）
    strength: Literal["strong", "weak"] = "strong"
    status: Literal["unseen", "covered", "patched", "ambiguous"] = "unseen"
    covered_by: str = ""  # 覆盖它的 item 引用（item_id / provisional id）


# 中文/英文关键字 → kind（顺序无关，取自 stage1 _ITEM_MARKER_RE 的语义超集）
_KEYWORD_KINDS: dict[str, MarkerKind] = {
    "定义": "definition",
    "definition": "definition",
    "def": "definition",
    "定理": "theorem",
    "theorem": "theorem",
    "thm": "theorem",
    "引理": "lemma",
    "lemma": "lemma",
    "lem": "lemma",
    "推论": "corollary",
    "corollary": "corollary",
    "cor": "corollary",
    "命题": "proposition",
    "proposition": "proposition",
    "prop": "proposition",
    "性质": "property",
    "property": "property",
    "公式": "formula",
    "formula": "formula",
    "定律": "law",
    "法则": "law",
    "law": "law",
    "原理": "principle",
    "principle": "principle",
    "反应": "reaction",
    "reaction": "reaction",
    "机理": "mechanism",
    "机制": "mechanism",
    "mechanism": "mechanism",
    "模型": "model",
    "model": "model",
    "实验": "experiment",
    "experiment": "experiment",
    "方法": "method",
    "method": "method",
    "过程": "process",
    "process": "process",
    "结构": "structure",
    "structure": "structure",
    "功能": "function",
    "function": "function",
    "分类": "classification",
    "classification": "classification",
    "事实": "fact",
    "fact": "fact",
    "注记": "remark",
    "备注": "remark",
    "remark": "remark",
    "例题": "example",
    "例": "example",
    "example": "example",
    "eg": "example",
    "习题": "exercise",
    "练习": "exercise",
    "exercise": "exercise",
    "问题": "problem",
    "problem": "problem",
    "题": "problem",
    "解答": "solution",
    "解": "solution",
    "solution": "solution",
    "sol": "solution",
    "证明": "proof",
    "证": "proof",
    "proof": "proof",
    "pf": "proof",
}

# LaTeX 环境名 → kind
_ENV_KINDS: dict[str, MarkerKind] = {
    "definition": "definition",
    "theorem": "theorem",
    "lemma": "lemma",
    "corollary": "corollary",
    "proposition": "proposition",
    "property": "property",
    "formula": "formula",
    "law": "law",
    "principle": "principle",
    "reaction": "reaction",
    "mechanism": "mechanism",
    "model": "model",
    "experiment": "experiment",
    "method": "method",
    "process": "process",
    "structure": "structure",
    "function": "function",
    "classification": "classification",
    "fact": "fact",
    "remark": "remark",
    "example": "example",
    "exercise": "exercise",
    "problem": "problem",
    "solution": "solution",
    "proof": "proof",
}

_ENV_NAMES = "|".join(_ENV_KINDS)
_KEYWORD_NAMES = "|".join(
    re.escape(keyword) for keyword in sorted(_KEYWORD_KINDS, key=len, reverse=True)
)

# 行首标记（允许前导空白与 ** 粗体）。分组命名用于 kind 判别与编号提取。
_LINE_MARKER_RE = re.compile(
    r"^\s*(?:"
    # ① LaTeX 环境：\begin{theorem} / \begin{problem}[第1题]
    rf"\\begin\{{(?P<env>{_ENV_NAMES})\}}(?:\[(?P<env_num>[^\]]{{0,40}})\])?"
    # ② 关键字 + 编号：**定理 3.1** / 定义2．1．5 / Theorem 3.1 / Thm. 2
    r"|(?:\*{1,2}\s*)?"
    rf"(?P<kw>{_KEYWORD_NAMES})"
    r"\.?[\s 　]{0,3}"
    r"(?P<kw_num>[0-9０-９][0-9０-９.．、]*)?"
    # ③ 中文序数题：第 3 题 / 第３题
    r"|第\s*(?P<ord_num>[0-9０-９]+)\s*题"
    # ③b 中文解答/证明起始行："解：" / "证明。" / "答案："（无编号、带标点）
    r"|(?P<sol_kw>解答?|证明?|答案?)\s*[:：.．]"
    # ④ 裸编号行（弱标记）：12. 设 … / 3、证明 …（要求编号后有正文）
    r"|(?P<bare_num>\d{1,3})\s*[.、．)）]\s+\S"
    # ⑤ 章节：markdown 标题 / \section / 第X章节
    r"|(?P<md_section>#{1,6}\s+\S)"
    r"|\\(?:sub)*section\{"
    r"|第\s*[0-9０-９一二三四五六七八九十百]+\s*[章节]"
    r")",
    re.IGNORECASE,
)


def _classify(match: re.Match[str]) -> tuple[MarkerKind, str, str]:
    """match → (kind, number_hint, strength)。"""
    if match.group("env"):
        env = match.group("env").lower()
        num = match.group("env_num") or ""
        return _ENV_KINDS[env], canonicalize_number(num) if num else "", "strong"
    if match.group("kw"):
        kw = match.group("kw").lower()
        kind = _KEYWORD_KINDS.get(kw)
        if kind is None:  # pragma: no cover - 关键字表与 regex 同步维护
            kind = "problem"
        # 关键字后无编号的"解/证明"等仍是强标记；条目类关键字无编号降为弱
        # （"例 如下"这类误命中概率高；带编号才是可靠条目头）
        num_raw = match.group("kw_num") or ""
        number = canonicalize_number(num_raw) if num_raw else ""
        if kind in ITEM_KINDS and not number:
            return kind, "", "weak"
        return kind, number, "strong"
    if match.group("ord_num"):
        return "problem", canonicalize_number(match.group("ord_num")), "strong"
    if match.group("sol_kw"):
        kw = match.group("sol_kw")
        return ("proof" if kw.startswith("证") else "solution"), "", "strong"
    if match.group("bare_num"):
        return "problem", canonicalize_number(match.group("bare_num")), "weak"
    return "section", "", "strong"


def find_item_markers(lines: list[str], *, line_base: int = 1) -> list[MarkerEntry]:
    """扫描（已剥离行号前缀的）窗口行，返回全部条目起始标记（按行号升序）。

    ``line_base`` 为首行的全局行号（与 Stage1 ``_annotate`` 同语义）。
    纯确定性，0 LLM。
    """
    markers: list[MarkerEntry] = []
    for offset, line in enumerate(lines):
        match = _LINE_MARKER_RE.match(line)
        if match is None:
            continue
        kind, number, strength = _classify(match)
        marker_start = match.start()
        markers.append(
            MarkerEntry(
                global_line=line_base + offset,
                char_offset=marker_start,
                marker_text=line.strip()[:80],
                kind=kind,
                number_hint=number,
                strength=strength,  # type: ignore[arg-type]
            )
        )
    return markers
