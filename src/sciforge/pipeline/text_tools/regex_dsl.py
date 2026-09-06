"""T10 通用正则 DSL——命名片段组合编译 + 掐头去尾（v11 II.3，M3）。

痛点（需求 10）：不同书/讲义/试卷里一个条目的"头"可能是
``**定理 3.1（中值定理）**``、``定理3.1.``、``Theorem 3.1.``、``【例 2】``、
``5. ``、``(1)``……"尾"可能误包含了下一条标记、页眉、``\\hfill$\\square$``、
答案分隔符。硬编码单条正则无法穷举——本模块用**命名片段拼装**，LLM/Evaluator
用语义级 ``compose(...)`` 调用，后端编译为精确正则，不直接暴露正则字符串。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.pipeline.text_tools.math_balance import check_math_balance

MarkerStyle = Literal["textbook", "question_number", "mixed"]

# 命名片段（可复用、跨学科、跨体裁）。片段名即 compose() 的合法参数。
FRAGMENTS: dict[str, str] = {
    "marker_head": (
        r"(?:\*{0,2})(?P<kind>定理|定义|引理|推论|命题|例题?|习题|练习|问题|证明|解答?"
        r"|Theorem|Definition|Lemma|Corollary|Proposition|Example|Exercise|Problem"
        r"|Proof|Solution)"
    ),
    "number": r"(?P<num>\d+(?:[.．、]\d+)*)",  # 1 / 3.1 / 3.1.2，半角
    "number_fw": r"(?P<num_fw>[０-９]+(?:[.．、][０-９]+)*)",  # 全角变体
    "enum_paren": r"[(（]\s*(?P<en>\d+|[a-zA-Z]|[ⅰ-ⅹ])\s*[)）]",  # (1) （a） (ⅱ)
    "title_paren": r"[(（](?P<title>[^)）]{0,40})[)）]",  # （中值定理）
    "qed": r"(?:\\hfill)?\s*(?:\$?\\square\$?|∎|□|证毕|Q\.?E\.?D\.?)[。.]?",
    "answer_sep": r"(?:^|\n)\s*(?:答案?|解答?|证明?|Answer|Solution|Proof)\s*[:：.]",
    "option_marker": r"[(（]?\s*[A-Da-d]\s*[)）.、]",  # 选择项 A. (B) C、
}


# 片段间分隔符：允许 0-2 个 markdown 加粗星号（**定理 3.1** 的收尾）+ 可选
# 一个收尾标点（"3.1．" 的句号）+ 空白。比单纯 \s* 更贴合真实排版噪声，
# 通用作用于所有 compose() 拼接点（含末尾——头部标记后总有分隔）。
_SEP = r"[.．、]?\*{0,2}\s*"


def compose(*fragment_names: str, optional_tail: tuple[str, ...] = ()) -> str:
    """按语义名拼装正则表达式。

    Args:
        fragment_names: 必须命中的片段序列（顺序拼接）。
        optional_tail: 追加在末尾的可选片段（各自加 ``?``），供
            ``compose("marker_head", "number", optional_tail=("title_paren",))``
            这类"头+编号+可选副标题"场景使用。

    Raises:
        KeyError: 片段名不在 ``FRAGMENTS`` 中（防拼写错误静默失效）。
    """
    parts = [FRAGMENTS[name] for name in fragment_names]
    parts.extend(f"(?:{FRAGMENTS[name]})?" for name in optional_tail)
    return _SEP.join(parts) + _SEP


# 体裁 → 加载的片段组合（II.3：marker_style 决定用哪组）
_STYLE_HEAD_PATTERNS: dict[MarkerStyle, str] = {
    "textbook": compose("marker_head", "number", optional_tail=("title_paren",)),
    "question_number": compose("enum_paren"),
    "mixed": compose("marker_head", "number", optional_tail=("title_paren",)),
}


class DslMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    text: str
    groups: dict[str, str] = Field(default_factory=dict)


def regex_dsl(expr: str, text: str) -> list[DslMatch]:
    """对 ``compose()`` 产出的表达式在 ``text`` 上找全部匹配。"""
    return [
        DslMatch(
            start=m.start(),
            end=m.end(),
            text=m.group(0),
            groups={k: v for k, v in m.groupdict().items() if v is not None},
        )
        for m in re.finditer(expr, text)
    ]


# ── 掐头去尾算法（确定性，无 LLM）────────────────────────


def trim_head(text: str, *, marker_style: MarkerStyle = "mixed", search_chars: int = 60) -> int:
    """在文本开头 ``search_chars`` 字符内定位"真正正文开头"的偏移。

    找到该体裁的 marker_head 整体 span（含编号/副标题）→ 切到其后。
    找不到 → 返回 0（不裁剪，不劣于现状）。
    """
    window = text[:search_chars]
    pattern = _STYLE_HEAD_PATTERNS[marker_style]
    m = re.match(r"\s*" + pattern, window)
    return m.end() if m else 0


#  "下一条目"检测要求"关键字+编号"组合（而非裸 marker_head）——单独一个
#  "证明"/"解答"在正文中极常见（如"...的证明如下"），裸关键字会大量误判；
#  要求紧跟编号才是可靠的新条目标题信号。
_NEXT_ITEM_RE = re.compile(FRAGMENTS["marker_head"] + r"\s*" + FRAGMENTS["number"])


def trim_tail(text: str) -> int:
    """在文本结尾定位"真正正文结尾"的偏移（exclusive）。

    去尾规则（按优先级）：
    1. 遇 ``qed``（证毕/□/∎/\\hfill$\\square$）→ 切到其后
    2. 遇下一条"关键字+编号"组合（如"定理 3.2"）→ 回退到其前（不吞入下一条）
    3. 结合 T12 数学配平：若裁剪点落在未闭合数学环境内部 → 不裁剪该点，
       退回到环境开启前（避免在公式中途截断）
    找不到明确边界 → 返回 ``len(text)``（不裁剪，不劣于现状）。
    """
    end = len(text)
    qed_matches = list(re.finditer(FRAGMENTS["qed"], text))
    if qed_matches:
        end = qed_matches[-1].end()
    else:
        later = [m for m in _NEXT_ITEM_RE.finditer(text) if m.start() > 0]
        if later:
            end = later[0].start()

    # 始终检查配平——即便 qed 恰好在文本末尾（end==len(text)最常见情形），
    # qed 词法命中不等于其前数学环境真的已闭合。
    prefix_balance = check_math_balance(text[:end].splitlines())
    if prefix_balance.open_env_at_tail:
        # 裁剪点在未闭合数学环境内部——回退到该环境开启前更安全，
        # 找不到明确开启点则退回不裁剪（不劣于现状）
        opener = re.search(r"\$\$|\\\[|\\\(|\\begin\{[A-Za-z*]+\}", text[:end])
        end = opener.start() if opener else len(text)
    return end


def trim_item(text: str, *, marker_style: MarkerStyle = "mixed") -> tuple[int, int]:
    """对条目全文做掐头去尾，返回 ``(start_offset, end_offset)``（相对 text）。"""
    start = trim_head(text, marker_style=marker_style)
    end = trim_tail(text)
    if end <= start:  # 掐头去尾冲突（极端情形）→ 不裁剪，返回整段
        return 0, len(text)
    return start, end
