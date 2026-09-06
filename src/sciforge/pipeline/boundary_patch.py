"""BoundaryAndPatchEngine（v11 子系统 III）——查漏底座 + carry 四态。

- III.1（M1）：``build_marker_ledger`` + ``audit_coverage``（覆盖率对账查漏）
- III.2（M2）：``classify_tail_state`` + ``build_carry`` + ``carry_matches_item``
  （跨窗尾部条目的四态判别与结构化交接）
- III.3 PatchInstruction（M3）、III.4 halo 仲裁（WP-23）后续在此扩展

全部纯确定性 0 LLM。审计输出 ``AuditReport`` 从第一天就是 M3 PatchInstruction
可直接消费的结构（missed_markers / boundary_suspects / pairing_suspects）。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.core.numbering import canonicalize_number
from sciforge.pipeline.llm_schemas import IncompleteItemCarry, TailState
from sciforge.pipeline.text_tools.markers import ITEM_KINDS, MarkerEntry, find_item_markers
from sciforge.pipeline.text_tools.math_balance import check_math_balance

__all__ = [
    "AuditItem",
    "AuditReport",
    "BoundarySuspect",
    "CoverageInterval",
    "IncompleteItemCarry",
    "MarkerEntry",
    "MarkerLedger",
    "PairingSuspect",
    "CarryMatchConfidence",
    "arbitrate_halo_overlap",
    "audit_coverage",
    "build_carry",
    "build_marker_ledger",
    "carry_match_confidence",
    "carry_matches_item",
    "classify_tail_state",
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


# ── III.2（M2）：carry 四态判别与结构化交接 ──────────────

_SOLUTION_MARKER_RE = re.compile(
    r"(?:^|\n)\s*(?:解答?|证明?|答案?|Solution|Proof)\s*[:：.．]|\\begin\{(?:solution|proof)\}"
)
# 句子终止标点（题干完结的弱信号）
_TERMINAL_PUNCT = tuple("。？！?!.；;：:$")


def classify_tail_state(
    tail_lines: list[str],
    *,
    genre: str = "unknown",
    carry_prior: TailState = "content_truncated",
) -> TailState:
    """对窗口末尾未闭合区域做四态判别（T12 + 解答标记 + genre 先验三路交叉）。

    判定顺序（强信号优先）：
    1. T12 数学环境不平衡（``open_env_at_tail``）→ content_truncated（公式中途断）
    2. 尾部已出现解答/证明标记 → content_truncated（解答写了一半被切断）
    3. 末行无终止标点 → stem_unfinished（题干本身没说完）
    4. 题干完整收尾 → genre 先验：exam_paper → answer_deferred（答案集中卷末）；
       problem_set → truly_no_answer（习题集常无解答）；其余 → carry_prior
    """
    text_lines = [ln for ln in tail_lines if ln.strip()]
    if not text_lines:
        return carry_prior
    if check_math_balance(text_lines).open_env_at_tail:
        return "content_truncated"
    if _SOLUTION_MARKER_RE.search("\n".join(text_lines)):
        return "content_truncated"
    if not text_lines[-1].rstrip().endswith(_TERMINAL_PUNCT):
        return "stem_unfinished"
    if genre == "exam_paper":
        return "answer_deferred"
    if genre == "problem_set":
        return "truly_no_answer"
    return carry_prior


def build_carry(
    tail_lines: list[str],
    *,
    line_base: int,
    genre: str = "unknown",
    carry_prior: TailState = "content_truncated",
    prev_count: int = 0,
) -> IncompleteItemCarry | None:
    """从窗口未消费尾部构建结构化 carry；尾部无条目起始标记时返回 None。

    ``tail_lines`` 为已剥离行号前缀的未消费行；``line_base`` 为其首行全局行号。
    取尾部**最后一个**强条目标记作条目头（其后所有内容属该条目）。
    """
    markers = [
        m
        for m in find_item_markers(tail_lines, line_base=line_base)
        if m.kind in ITEM_KINDS and m.strength == "strong"
    ]
    if not markers:
        return None
    head = markers[-1]
    head_idx = head.global_line - line_base
    item_lines = tail_lines[head_idx:]
    head_content = "\n".join(item_lines).strip()[:2000]
    tail_state = classify_tail_state(item_lines, genre=genre, carry_prior=carry_prior)
    first_line = item_lines[0].strip() if item_lines else ""
    return IncompleteItemCarry(
        item_type=head.kind,
        number=head.number_hint,
        category="knowledge"
        if head.kind in {"definition", "theorem", "lemma", "corollary", "proposition"}
        else "question",
        head_anchor_start=first_line[:40],
        head_content=head_content,
        tail_state=tail_state,
        original_start_line=head.global_line,
        carry_window_count=prev_count + 1,
    )


# ── III.4（M2）：segment halo 边界仲裁器 ──────────────────


def _item_completeness(item: object) -> tuple[bool, int, int]:
    """条目完整度排序键：有解答/证明 > 行跨度长 > 起始行早。"""
    has_payload = bool(getattr(item, "corrected_proof", None)) or bool(
        getattr(item, "corrected_solution", None)
    )
    start = int(getattr(item, "start_line", 0) or 0)
    end = int(getattr(item, "end_line", 0) or 0)
    return (has_payload, end - start, -start)


def _items_overlap(a: object, b: object) -> bool:
    """两条目视为同一条：行区间相交，或编号+正文证据同时一致。

    教材常在不同章节重复使用 ``1`` / ``3.2`` 等局部编号；仅凭编号会把边界
    两侧真实不同条目误删。非重叠区间必须再有相同/包含式正文证据才可归并。
    """
    a_s, a_e = int(getattr(a, "start_line", 0)), int(getattr(a, "end_line", 0))
    b_s, b_e = int(getattr(b, "start_line", 0)), int(getattr(b, "end_line", 0))
    if max(a_s, b_s) <= min(a_e, b_e):
        return True
    a_num = canonicalize_number(str(getattr(a, "number", "") or ""))
    b_num = canonicalize_number(str(getattr(b, "number", "") or ""))
    if not a_num or a_num != b_num:
        return False
    a_content = " ".join(str(getattr(a, "corrected_content", "") or "").split())
    b_content = " ".join(str(getattr(b, "corrected_content", "") or "").split())
    if not a_content or not b_content:
        return False
    if a_content == b_content:
        return True
    shorter, longer = sorted((a_content, b_content), key=len)
    return len(shorter) >= 40 and shorter in longer


def arbitrate_halo_overlap(
    prev_items: list[Any],
    next_items: list[Any],
    *,
    boundary_line: int,
) -> tuple[list[Any], list[Any], int]:
    """相邻段 halo 重叠区的确定性去重合并（III.4 boundary arbiter，0 LLM）。

    ``boundary_line`` = 下一段首行的全局行号（前段 halo 从此行起）。规则：

    - 前段条目**完全在本段内**（end < boundary）→ 保留
    - 前段条目**完全在 halo 内**（start ≥ boundary）→ 丢弃（邻段领土，邻段会提）
    - 前段**跨边界**条目 → 与后段重叠对手比完整度（有解答 > 跨度长 > 行号早）：
      前段更完整 → 保前段、剔除对手；否则丢前段保后段。
      后段无对手（漏提）→ 保前段——这正是 halo 兜住跨界条目的价值。
    - 超长跨界（条目长于 halo）由 III.2 carry 在段内滑窗层兜底，此处不特殊处理。

    返回 ``(kept_prev, kept_next, dropped)``；Stage3 content_hash + INSERT OR
    IGNORE 是最后的二保险。
    """
    kept_prev: list[Any] = []
    cross: list[Any] = []
    dropped = 0
    for item in prev_items:
        start = int(getattr(item, "start_line", 0) or 0)
        end = int(getattr(item, "end_line", 0) or 0)
        if end < boundary_line:
            kept_prev.append(item)
        elif start < boundary_line:
            cross.append(item)
        else:
            dropped += 1  # 完全落在 halo（邻段领土）

    kept_next = list(next_items)
    for item in cross:
        rivals = [r for r in kept_next if _items_overlap(item, r)]
        if not rivals:
            kept_prev.append(item)
            continue
        best_rival = max(rivals, key=_item_completeness)
        if _item_completeness(item) >= _item_completeness(best_rival):
            kept_prev.append(item)
            for rival in rivals:
                kept_next.remove(rival)
                dropped += 1
        else:
            dropped += 1  # 后段版本更完整 → 丢前段跨界残片

    kept_prev.sort(key=lambda i: int(getattr(i, "start_line", 0) or 0))
    return kept_prev, kept_next, dropped


CarryMatchConfidence = Literal["exact", "uncertain", "none"]


def carry_match_confidence(carry: IncompleteItemCarry, item: object) -> CarryMatchConfidence:
    """carry 匹配的置信度分级（编号 → 行号 → 锚点三路降级）。

    ``item`` 鸭子类型：需有 number / start_line / corrected_content 属性
    （即 SingleItemExtractLLM）。

    - ``exact``：编号精确匹配——可靠，标 ``accepted``
    - ``uncertain``：无编号可比，靠行号邻近或锚点模糊匹配——试探合并
      （v11 I.3），标 ``uncertain`` 供后续人工/patch 复核，但**仍入库**
      （宁可入库待复核，不丢弃）
    - ``none``：三路皆不命中
    """
    carry_num = canonicalize_number(carry.number) if carry.number else ""
    item_num = canonicalize_number(getattr(item, "number", "") or "")
    if carry_num and item_num:
        return "exact" if carry_num == item_num else "none"
    start_line = int(getattr(item, "start_line", 0) or 0)
    if abs(start_line - carry.original_start_line) <= 2:
        return "uncertain"
    anchor = carry.head_anchor_start.strip()
    if len(anchor) >= 4:
        content = str(getattr(item, "corrected_content", "") or "")
        # 锚点含标记头（如"习题 3.1 求…"），条目 content 通常去掉了标记头 → 双向包含
        if anchor in content[:120] or content[:40].strip() in anchor:
            return "uncertain"
    return "none"


def carry_matches_item(carry: IncompleteItemCarry, item: object) -> bool:
    """判断提取条目是否续完/定案了 carry 条目（``carry_match_confidence`` 的布尔视图）。"""
    return carry_match_confidence(carry, item) != "none"
