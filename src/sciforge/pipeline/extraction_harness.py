"""ExtractionHarness（v11 子系统 I，M4）——把窗口当作一次有界 agent 任务。

对外是原子单元（输入一个窗口、输出一批 item），对内按需多轮
（locate→extract→audit→carry），全程复用确定性工具做 ground truth（§4）。

回路（Docs/20 I.2）：
    ① LOCATE（确定性，0 LLM）：T4 find_item_markers 建立 MarkerLedger
    ② EXTRACT（1 次 LLM，主路径）：调用注入的 ``extract_fn``
    ③ AUDIT（确定性，0 LLM）：T6 audit_coverage 对账
    ④ PATCH（条件触发，本骨架版本不接入——留给调用方在 HarnessResult 之外
       决定是否触发 M3 patch_instruction 或 M4 WP-44 evaluator）
    ⑤ CARRY + REMAP（确定性）：M2 carry 四态判定，remap 由调用方在
       HarnessResult 之外用 M3 ``_finalize_item_ids`` 完成（保持单一职责）

**关键：② 只调 1 次 LLM 是常态**——①③⑤ 全确定性，不产生额外 LLM 调用。
本模块通过依赖注入（``extract_fn``）与 Stage1Extract 解耦，可独立单测
（mock extract_fn 验证契约行为），不依赖真实 LLM/Stage1 实例。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.pipeline.boundary_patch import (
    AuditItem,
    AuditReport,
    IncompleteItemCarry,
    audit_coverage,
    build_carry,
    build_marker_ledger,
    carry_match_confidence,
)
from sciforge.pipeline.llm_schemas import BatchItemExtractPayloadLLM, SingleItemExtractLLM

StopReason = Literal["all_complete", "incomplete_at_tail", "no_items_found"]


class WindowContext(BaseModel):
    """harness 单次运行的全部输入（一个窗口 + 跨窗上下文）。"""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    window_lines: list[str]  # 带行号前缀的窗口（与现有 _annotate 同构）
    line_base: int = Field(ge=1)  # 窗口首行的全局行号
    is_last_window: bool = False  # 是否文档/章节末窗
    carry_in: IncompleteItemCarry | None = None  # 上一窗未闭合条目（III.2）
    genre: str = "unknown"
    marker_style: str = "mixed"
    discipline: str = "math"
    provisional_id_base: str = ""  # 本窗 provisional id 前缀


class HarnessResult(BaseModel):
    """harness 单次运行的全部输出。"""

    model_config = ConfigDict(extra="forbid")

    accepted_items: list[SingleItemExtractLLM] = Field(default_factory=list)
    carry_out: IncompleteItemCarry | None = None
    markers_total: int = 0  # MarkerLedger 摘要（避免整个 ledger 进 result 体积过大）
    markers_covered: int = 0
    audit_clean: bool = True
    patch_log: list[str] = Field(default_factory=list)  # 本版骨架恒为空（④ 未接入）
    risk_score: float = 0.0  # 0-1，决定是否触发 evaluator（WP-44 消费）
    token_usage: dict[str, int] = Field(default_factory=dict)
    last_consumed_line: int = 0
    stop_reason: StopReason = "no_items_found"
    degraded: bool = False  # True = harness 内部异常，已降级为 extract_fn 原始结果


ExtractFn = Callable[[WindowContext], Awaitable[BatchItemExtractPayloadLLM]]


def _strip_line_prefix(line: str) -> str:
    head, sep, rest = line.partition(" | ")
    return rest if sep and head.strip().isdigit() else line


def _compute_risk_score(report: AuditReport, payload: BatchItemExtractPayloadLLM) -> float:
    """确定性风险打分（0-1）：复用现有信号，供 WP-44 evaluator 触发判定。

    信号：审计不 clean（missed/boundary/pairing 各计一份权重）+
    stop_reason 非 all_complete + marker 数远超 item 数（>2x，Docs/20 III.1）。
    """
    score = 0.0
    if report.missed_markers:
        score += 0.4
    if report.boundary_suspects:
        score += 0.2
    if report.pairing_suspects:
        score += 0.2
    if payload.stop_reason != "all_complete":
        score += 0.1
    if report.markers_total > 0 and len(payload.items) > 0:
        if report.markers_total > 2 * len(payload.items):
            score += 0.3
    return min(1.0, score)


async def run_harness(
    ctx: WindowContext,
    *,
    extract_fn: ExtractFn,
    carry_max_windows: int = 3,
) -> HarnessResult:
    """单窗口 harness 回路：①LOCATE→②EXTRACT→③AUDIT→⑤CARRY。

    **② EXTRACT 是唯一一次 LLM 调用，且只调一次**——``extract_fn`` 内部已有
    自己的 retry-with-feedback 机制，这里失败不重试、直接向上抛出，交调用方
    走现状的 ``RetryExhausted`` 处理（harness 不吞、不二次重试 LLM 调用）。

    ①③⑤ 全部确定性（0 LLM），任何内部异常只影响这些**增强层**分析，用
    ``degraded=True`` 返回已拿到的 ``payload`` 基本结果——不会因为审计/carry
    计算的 bug 而丢失已完成的 LLM 提取结果，也不会导致重复调用。
    """
    # ① LOCATE（确定性，0 LLM）：失败不影响②的执行（纯观测数据缺失，非致命）
    try:
        raw_lines = [_strip_line_prefix(ln) for ln in ctx.window_lines]
    except Exception:  # noqa: BLE001
        raw_lines = []

    # ② EXTRACT（1 次 LLM，主路径）——异常直接向上抛，不在此处吞掉或重试
    payload = await extract_fn(ctx)

    try:
        # 只审计已消费段（尾部延迟条目下窗重扫，避免假 missed，同 WP-13）
        last_line = ctx.line_base + len(ctx.window_lines) - 1
        consumed_end = payload.last_consumed_line if payload.items else last_line
        consumed_count = sum(
            1 for i, _ in enumerate(raw_lines) if ctx.line_base + i <= consumed_end
        )
        consumed_raw_lines = raw_lines[:consumed_count]
        consumed_ledger = build_marker_ledger(consumed_raw_lines, line_base=ctx.line_base)

        # ③ AUDIT（确定性，0 LLM）
        audit_items = [
            AuditItem(
                item_ref=str(i),
                category=item.category,
                item_type=item.item_type,
                solution_status=item.solution_status,
                start_line=item.start_line,
                end_line=max(item.end_line, item.start_line),
            )
            for i, item in enumerate(payload.items)
        ]
        report = audit_coverage(consumed_ledger, audit_items)

        # ⑤ CARRY（确定性）：尾部未消费区域重建 carry；carry_in 定案匹配交调用方
        # （Stage1Extract 已有 _update_carry_after_window 状态机，harness 骨架
        # 只负责"本窗尾部是否需要新 carry"这一半，避免跨窗状态在此重复维护）
        carry_out: IncompleteItemCarry | None = None
        if payload.stop_reason == "incomplete_at_tail":
            tail_raw = raw_lines[consumed_count:]
            if tail_raw:
                prev_count = ctx.carry_in.carry_window_count if ctx.carry_in else 0
                carry_out = build_carry(
                    tail_raw,
                    line_base=ctx.line_base + consumed_count,
                    genre=ctx.genre,
                    prev_count=prev_count,
                )
                if (
                    carry_out is not None
                    and ctx.carry_in is not None
                    and carry_out.original_start_line == ctx.carry_in.original_start_line
                    and carry_out.carry_window_count > carry_max_windows + 1
                ):
                    carry_out = None  # 到顶仍未闭合 → 放弃，退回被动重扫

        # carry_in 是否已被本窗任一条目定案匹配（供调用方参考，不强制处理）
        if ctx.carry_in is not None and payload.items:
            for item in payload.items:
                if carry_match_confidence(ctx.carry_in, item) != "none":
                    break

        return HarnessResult(
            accepted_items=payload.items,
            carry_out=carry_out,
            markers_total=report.markers_total,
            markers_covered=report.markers_covered,
            audit_clean=report.clean,
            risk_score=_compute_risk_score(report, payload),
            last_consumed_line=payload.last_consumed_line,
            stop_reason=payload.stop_reason,
            degraded=False,
        )
    except Exception:  # noqa: BLE001 — ①③⑤ 是增强层，异常不应丢失已拿到的②结果
        return HarnessResult(
            accepted_items=payload.items,
            last_consumed_line=payload.last_consumed_line,
            stop_reason=payload.stop_reason,
            degraded=True,
        )
