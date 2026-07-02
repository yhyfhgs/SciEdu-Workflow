"""BoundaryAndPatchEngine（v11 子系统 III）——查漏底座：marker ledger + 覆盖率审计。

本模块 M1 落地 III.1（``build_marker_ledger`` + ``audit_coverage``，纯确定性 0 LLM）；
III.2 carry 四态（M2）、III.3 PatchInstruction（M3）、III.4 halo 仲裁（M2）后续在此扩展。

审计输出 ``AuditReport`` 从第一天就是 M3 PatchInstruction 可直接消费的结构
（missed_markers / boundary_suspects / pairing_suspects），避免后续重构。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.pipeline.text_tools.markers import ITEM_KINDS, MarkerEntry, find_item_markers

__all__ = [
    "AuditItem",
    "AuditReport",
    "BoundarySuspect",
    "CoverageInterval",
    "MarkerEntry",
    "MarkerLedger",
    "PairingSuspect",
    "audit_coverage",
    "build_marker_ledger",
]


class MarkerLedger(BaseModel):
    """一个窗口的标记真值台账（T4 扫描产物 + 覆盖状态）。"""

    model_config = ConfigDict(extra="forbid")

    markers: list[MarkerEntry] = Field(default_factory=list)
    window_line_span: tuple[int, int]  # 全局行号 [首行, 末行]（闭区间）

    def strong_item_markers(self) -> list[MarkerEntry]:
        """强条目类标记（missed 判定的分母；weak/结构类不计）。"""
        return [m for m in self.markers if m.kind in ITEM_KINDS and m.strength == "strong"]


class CoverageInterval(BaseModel):
    """已覆盖/缺口/待续行区间（全局行号闭区间）。"""

    model_config = ConfigDict(extra="forbid")

    start_global_line: int = Field(ge=1)
    end_global_line: int = Field(ge=1)
    kind: Literal["item", "gap", "carry"]
    item_ref: str = ""  # kind=item 时为条目引用


class AuditItem(BaseModel):
    """提取条目的最小审计视图（与 SingleItemExtractLLM 解耦，WP-13 负责转换）。"""

    model_config = ConfigDict(extra="forbid")

    item_ref: str  # item_id / provisional id 的字符串形式
    category: str = ""
    item_type: str = ""
    solution_status: str = ""
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class BoundarySuspect(BaseModel):
    """条目区间内部出现另一个强条目标记 → 疑似吞并了相邻条目。"""

    model_config = ConfigDict(extra="forbid")

    item_ref: str
    marker: MarkerEntry
    detail: str = ""


class PairingSuspect(BaseModel):
    """solution_status 与区间内解答/证明标记不自洽（完整判定 T8 在 M2）。"""

    model_config = ConfigDict(extra="forbid")

    item_ref: str
    detail: str = ""


class AuditReport(BaseModel):
    """覆盖率审计结论（III.1）：全 clean → 窗口 0 额外处理直接收尾。"""

    model_config = ConfigDict(extra="forbid")

    missed_markers: list[MarkerEntry] = Field(default_factory=list)
    boundary_suspects: list[BoundarySuspect] = Field(default_factory=list)
    pairing_suspects: list[PairingSuspect] = Field(default_factory=list)
    coverage_intervals: list[CoverageInterval] = Field(default_factory=list)
    markers_total: int = 0
    markers_covered: int = 0

    @property
    def clean(self) -> bool:
        return not (self.missed_markers or self.boundary_suspects or self.pairing_suspects)


def build_marker_ledger(window_lines: list[str], *, line_base: int = 1) -> MarkerLedger:
    """T4 扫描整窗（已剥离行号前缀的行）→ 标记台账。"""
    last_line = line_base + len(window_lines) - 1 if window_lines else line_base
    return MarkerLedger(
        markers=find_item_markers(window_lines, line_base=line_base),
        window_line_span=(line_base, last_line),
    )


_SOLUTION_KINDS = frozenset({"solution", "proof"})


def audit_coverage(ledger: MarkerLedger, items: Sequence[AuditItem]) -> AuditReport:
    """把条目区间与标记台账对账（III.1 审计算法，纯确定性 0 LLM）。

    副作用：就地更新 ``ledger.markers`` 的 ``status`` / ``covered_by``
    （covered / ambiguous；missed 保持 unseen 由 patch 阶段改写）。

    三类问题：
    - missed_marker：强条目标记未被任何条目区间覆盖（候选遗漏 → M3 局部补抽）
    - boundary_suspect：条目区间内部（非起始行）出现另一强条目标记（疑似吞并）
    - pairing_suspect：条目声称无解答，但区间内有解答/证明标记（不自洽）
    """
    sorted_items = sorted(items, key=lambda i: (i.start_line, i.end_line))
    intervals = [
        CoverageInterval(
            start_global_line=item.start_line,
            end_global_line=max(item.end_line, item.start_line),
            kind="item",
            item_ref=item.item_ref,
        )
        for item in sorted_items
    ]

    def _covering(line: int) -> AuditItem | None:
        for item in sorted_items:
            if item.start_line <= line <= item.end_line:
                return item
        return None

    missed: list[MarkerEntry] = []
    boundary: list[BoundarySuspect] = []
    covered_count = 0
    for marker in ledger.markers:
        owner = _covering(marker.global_line)
        if owner is not None:
            marker.status = "covered"
            marker.covered_by = owner.item_ref
            covered_count += 1
            # 条目内部（非起始行）的另一个强条目标记 → 疑似吞并相邻条目
            if (
                marker.kind in ITEM_KINDS
                and marker.strength == "strong"
                and marker.global_line > owner.start_line
            ):
                boundary.append(
                    BoundarySuspect(
                        item_ref=owner.item_ref,
                        marker=marker,
                        detail=f"条目区间内部第 {marker.global_line} 行出现 {marker.kind} 标记",
                    )
                )
            continue
        if marker.kind in ITEM_KINDS and marker.strength == "strong":
            missed.append(marker)  # status 保持 unseen，交 patch 阶段处置
        else:
            marker.status = "ambiguous"

    # 缺口区间：每个 missed marker 起，到下一条目起始行前（或窗口末行）
    gaps: list[CoverageInterval] = []
    window_end = ledger.window_line_span[1]
    for marker in missed:
        next_start = min(
            (i.start_line for i in sorted_items if i.start_line > marker.global_line),
            default=window_end + 1,
        )
        gaps.append(
            CoverageInterval(
                start_global_line=marker.global_line,
                end_global_line=max(next_start - 1, marker.global_line),
                kind="gap",
            )
        )

    # 极简配对自洽（完整 T8 在 M2）：声称无解答但区间内有解答/证明标记
    pairing: list[PairingSuspect] = []
    for item in sorted_items:
        if item.solution_status != "no_solution_in_source":
            continue
        inner = [
            m
            for m in ledger.markers
            if m.kind in _SOLUTION_KINDS and item.start_line < m.global_line <= item.end_line
        ]
        if inner:
            pairing.append(
                PairingSuspect(
                    item_ref=item.item_ref,
                    detail=(
                        f"声称 no_solution_in_source 但区间内第 "
                        f"{inner[0].global_line} 行有 {inner[0].kind} 标记"
                    ),
                )
            )

    return AuditReport(
        missed_markers=missed,
        boundary_suspects=boundary,
        pairing_suspects=pairing,
        coverage_intervals=intervals + gaps,
        markers_total=len(ledger.markers),
        markers_covered=covered_count,
    )
