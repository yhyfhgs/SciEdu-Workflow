"""跨章节题答回填 — 把章末/附录答案并回题干（#3 #8 不分离）

教材常把习题放正文、解答放章末"习题答案/解答"专章。Stage1 会把题干和答案
提取成两条独立 Question（同 ``number_canonical``、内容不同、不会被去重）。
本模块在入库后按 ``(subject, number_canonical)`` 匹配，把"答案条目"的解答正文
回填到对应"题目条目"的 ``solution_content``，并删除冗余答案条目。

保守策略（避免误并）：仅当一个分组内**恰好**能区分出
- 一条"题目型"：无 solution_content，且内容不是答案样式
- 一条或多条"答案型"：内容以 解/证/答/Solution/Answer 起头
时才回填；否则整组跳过。纯逻辑，便于单测；存储读写由调用方完成。
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sciforge.core.models import Question

# 剥离内容开头的编号/题号噪声（"3.2"、"例3.2"、"第1题"、空白、括号）
_LEADING_NOISE_RE = re.compile(r"^[\s\d.．、)）(（第题例习\-—]*")
# 答案/解答样式起头标记：标记须是完整 token（lookahead 防止 "解释"/"证券"/"答辩" 误判），
# 允许后跟标点/空白/右括号/结尾（兼容标签无冒号、解答正文在 solution_content 的情形）。
# 长 alternation 在前（解答>解、证明>证、答案>答），优先匹配更具体的标记。
_ANSWER_MARKER_RE = re.compile(
    r"^\*{0,2}\s*(?:解答|解|证明|证|答案|答|Solution|Sol|Answer|Proof)"
    r"(?=[:：.、)）\s]|$)",
    re.IGNORECASE,
)


def looks_like_answer(content: str) -> bool:
    """内容是否为"答案/解答"样式（剥离开头编号后以 解/证/答/Solution 起）。"""
    stripped = _LEADING_NOISE_RE.sub("", (content or "").strip())
    return _ANSWER_MARKER_RE.match(stripped) is not None


@dataclass
class LinkResult:
    """题答回填结果（供调用方落库）"""

    backfills: list[tuple[str, str]] = field(default_factory=list)  # (question_id, solution_text)
    delete_ids: list[str] = field(default_factory=list)  # 冗余答案条目 id（回填后删除）


def link_answers(questions: list[Question]) -> LinkResult:
    """按 (subject, number_canonical) 匹配题干与答案条目，产出回填/删除计划。"""
    groups: dict[tuple[str, str], list[Question]] = defaultdict(list)
    for q in questions:
        if q.number_canonical:
            groups[(q.subject, q.number_canonical)].append(q)

    result = LinkResult()
    for members in groups.values():
        if len(members) < 2:
            continue
        answers = [q for q in members if looks_like_answer(q.content)]
        problems = [
            q
            for q in members
            if q not in answers and not (q.solution_content or "").strip()
        ]
        # 仅当恰好一条题目型 + 至少一条答案型时回填（保守）
        if len(problems) != 1 or not answers:
            continue
        prob = problems[0]
        ans = answers[0]
        sol_text = (ans.solution_content or "").strip() or ans.content
        result.backfills.append((prob.id, sol_text))
        result.delete_ids.extend(a.id for a in answers)
    return result
