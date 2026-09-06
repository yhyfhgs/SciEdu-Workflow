"""T7 ``check_numbering_continuity``——编号连续性确定性检查（复用 core/numbering）。

输入按文档顺序排列的编号原文列表，输出跳号/重号/乱序问题清单。
供覆盖率审计（缺口 = 候选遗漏）与 Evaluator 工具调用（M4）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from sciforge.core.numbering import canonicalize_number, parse_number_components


class NumberingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["gap", "duplicate", "out_of_order"]
    before: str  # 规范化编号（问题对的前一个）
    after: str
    detail: str = ""


class NumberingContinuityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[NumberingIssue] = []
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues


def _prefix_and_index(canonical: str) -> tuple[str, int] | None:
    """ "3.2.5" → ("3.2", 5)；无末级数字索引返回 None。"""
    _, _, index = parse_number_components(canonical)
    if index is None:
        return None
    head, _, tail = canonical.rpartition(".")
    if not tail.isdigit():
        return None
    return head, index


def check_numbering_continuity(numbers: list[str]) -> NumberingContinuityReport:
    """检查同前缀编号序列的连续性（相邻比较，纯确定性）。

    只对**相邻且同前缀**（如 3.2.4 → 3.2.5）的编号对判定：
    - 索引相等 → duplicate；回退 → out_of_order；跳过 ≥1 个 → gap。
    前缀切换（3.2.9 → 3.3.1）视为新序列开始，不报问题。
    无法解析出末级索引的编号跳过不计。
    """
    parsed: list[tuple[str, str, int]] = []  # (canonical, prefix, index)
    for raw in numbers:
        canonical = canonicalize_number(raw)
        if not canonical:
            continue
        pi = _prefix_and_index(canonical)
        if pi is None:
            continue
        parsed.append((canonical, pi[0], pi[1]))

    issues: list[NumberingIssue] = []
    for (prev_c, prev_p, prev_i), (cur_c, cur_p, cur_i) in zip(parsed, parsed[1:], strict=False):
        if prev_p != cur_p:
            continue  # 前缀切换 = 新序列
        if cur_i == prev_i:
            issues.append(
                NumberingIssue(kind="duplicate", before=prev_c, after=cur_c, detail="编号重复")
            )
        elif cur_i < prev_i:
            issues.append(
                NumberingIssue(kind="out_of_order", before=prev_c, after=cur_c, detail="编号回退")
            )
        elif cur_i > prev_i + 1:
            missing = ", ".join(
                f"{cur_p + '.' if cur_p else ''}{i}" for i in range(prev_i + 1, cur_i)
            )
            issues.append(
                NumberingIssue(
                    kind="gap", before=prev_c, after=cur_c, detail=f"疑似缺号: {missing}"
                )
            )
    return NumberingContinuityReport(issues=issues, checked=len(parsed))
