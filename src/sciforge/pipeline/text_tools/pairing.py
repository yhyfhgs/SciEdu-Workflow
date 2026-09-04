"""T8 ``check_pairing``——题答/定理证明配对自洽检查（v11 II.2，M2）。

对一批条目做确定性配对体检（0 LLM）：

- ``complete_without_payload``：声称 complete 但 proof/solution 皆空
- ``payload_without_complete``：有解答正文但状态不是 complete
- ``near_tail_no_solution``：习题类条目贴近窗口末尾却声称 no_solution_in_source
  （near-tail guard 的批量后验版——carry 合并/补抽产物可能绕过 Pydantic validator）
- ``solution_marker_inside``：声称无解答但条目区间内有解答/证明标记（需传 ledger）

供 III.2 carry 四态判别（``truly_no_answer`` vs ``answer_deferred`` 判据之一）
与 M4 Evaluator 工具调用。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.pipeline.text_tools.markers import MarkerEntry

_EXERCISE_LIKE = frozenset({"exercise", "example", "problem"})
_SOLUTION_KINDS = frozenset({"solution", "proof"})


class PairingItem(BaseModel):
    """配对体检的最小条目视图（与 SingleItemExtractLLM 解耦）。"""

    model_config = ConfigDict(extra="forbid")

    item_ref: str
    category: str = ""
    item_type: str = ""
    solution_status: str = ""
    has_payload: bool = False  # corrected_proof 或 corrected_solution 非空
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class PairingIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_ref: str
    kind: Literal[
        "complete_without_payload",
        "payload_without_complete",
        "near_tail_no_solution",
        "solution_marker_inside",
    ]
    detail: str = ""


class PairingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issues: list[PairingIssue] = Field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.issues


def check_pairing(
    items: list[PairingItem],
    *,
    window_last_line: int,
    near_tail_margin: int = 10,
    markers: list[MarkerEntry] | None = None,
) -> PairingReport:
    """批量配对自洽检查。``markers`` 传窗口台账时启用 solution_marker_inside 检查。"""
    issues: list[PairingIssue] = []
    for item in items:
        if item.solution_status == "complete" and not item.has_payload:
            issues.append(
                PairingIssue(
                    item_ref=item.item_ref,
                    kind="complete_without_payload",
                    detail="声称 complete 但 proof/solution 皆空",
                )
            )
        if item.solution_status != "complete" and item.has_payload:
            issues.append(
                PairingIssue(
                    item_ref=item.item_ref,
                    kind="payload_without_complete",
                    detail=f"有解答正文但 solution_status={item.solution_status}",
                )
            )
        if (
            item.solution_status == "no_solution_in_source"
            and item.item_type in _EXERCISE_LIKE
            and window_last_line - item.end_line < near_tail_margin
        ):
            issues.append(
                PairingIssue(
                    item_ref=item.item_ref,
                    kind="near_tail_no_solution",
                    detail=(
                        f"习题类条目距窗口末行 {window_last_line - item.end_line} 行"
                        f"（< {near_tail_margin}）却声称无解答——疑似截断"
                    ),
                )
            )
        if markers and item.solution_status == "no_solution_in_source":
            inner = [
                m
                for m in markers
                if m.kind in _SOLUTION_KINDS and item.start_line < m.global_line <= item.end_line
            ]
            if inner:
                issues.append(
                    PairingIssue(
                        item_ref=item.item_ref,
                        kind="solution_marker_inside",
                        detail=f"区间内第 {inner[0].global_line} 行有 {inner[0].kind} 标记",
                    )
                )
    return PairingReport(issues=issues, checked=len(items))
