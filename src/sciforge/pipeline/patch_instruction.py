"""PatchInstruction 局部补抽（v11 III.3，M3）——替代整窗 retry 的增量修复。

核心区别（Docs/20 III.3）：retry 是"整窗重发、重算全部 token、可能再错一样的
地方"；patch 是"只补缺口那几行、其余已 accepted 的 item 一字不重算"。

补抽流程（harness 步骤 ④，本模块提供①③②三个纯函数 + 一个可选 async micro
LLM 补抽入口，Stage1 集成见 ``stages/stage1_extract.py``）：
1. **确定性优先**（0 LLM）：``boundary_fix``（trim_item 回退边界）/
   ``pairing_fix``（标记 needs_patch，交后续 micro LLM 补内容）/
   ``type_fix``（marker.kind 与 item.item_type 不符则直接纠正）
2. **micro LLM**（仅 ``missed_marker`` 且确定性补不出时）：取遗漏 marker
   附近 ±context_lines 行 → 单条提取 prompt（不重跑整窗）→ 产出 1 条 item
3. 补出的 item 标 ``accepted`` 插入正确 order 位置；补不出 → marker 标
   ``ambiguous``，造一条 ``uncertain`` 占位（宁可入库待复核，不丢弃）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

from sciforge.pipeline.boundary_patch import AuditReport, MarkerEntry
from sciforge.pipeline.llm_schemas import SingleItemExtractLLM, SingleItemExtractPayloadLLM
from sciforge.pipeline.retry import call_llm_json_with_schema_retry_with_feedback
from sciforge.pipeline.text_tools.pairing import PairingItem
from sciforge.pipeline.text_tools.regex_dsl import MarkerStyle, trim_tail
from sciforge.pipeline.text_tools.structure import extract_subinterval

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient

PatchReason = Literal["missed_marker", "boundary_fix", "pairing_fix", "type_fix"]
PatchMethod = Literal["deterministic", "micro_llm", "skipped"]


class PatchInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: PatchReason
    target_line_start: int = Field(ge=1)
    target_line_end: int = Field(ge=1)
    around_marker: MarkerEntry | None = None
    expected_kind: str = ""
    item_ref: str = ""  # boundary_fix/pairing_fix/type_fix 指向的已提取条目
    context_lines: int = 8
    prefer_deterministic: bool = True


class PatchRecord(BaseModel):
    """入 ``HarnessResult.patch_log``（可观测）。"""

    model_config = ConfigDict(extra="forbid")

    instruction: PatchInstruction
    method: PatchMethod
    result_pid: str = ""
    detail: str = ""
    token_cost: int = 0


# ── ① 从 AuditReport 生成待修复指令（纯函数，0 LLM）─────────


def build_patch_instructions(
    report: AuditReport,
    *,
    pairing_issue_refs: dict[str, str] | None = None,
    type_mismatches: list[tuple[str, str]] | None = None,
) -> list[PatchInstruction]:
    """把审计发现的三类问题转成待处理指令列表。

    Args:
        report: III.1 覆盖率审计结果（missed_markers / boundary_suspects）。
        pairing_issue_refs: ``{item_ref: detail}``，来自 T8 ``check_pairing``
            的问题条目（本函数只负责生成指令，不重复跑 T8）。
        type_mismatches: ``[(item_ref, expected_kind), ...]``——item_type
            与其覆盖的 marker.kind 不一致的条目（由调用方对照 marker 得出）。
    """
    instructions: list[PatchInstruction] = []
    for marker in report.missed_markers:
        instructions.append(
            PatchInstruction(
                reason="missed_marker",
                target_line_start=marker.global_line,
                target_line_end=marker.global_line,
                around_marker=marker,
                expected_kind=marker.kind,
            )
        )
    for suspect in report.boundary_suspects:
        instructions.append(
            PatchInstruction(
                reason="boundary_fix",
                target_line_start=suspect.marker.global_line,
                target_line_end=suspect.marker.global_line,
                around_marker=suspect.marker,
                item_ref=suspect.item_ref,
            )
        )
    for item_ref, detail in (pairing_issue_refs or {}).items():
        instructions.append(
            PatchInstruction(
                reason="pairing_fix",
                target_line_start=1,
                target_line_end=1,
                item_ref=item_ref,
                expected_kind=detail,
            )
        )
    for item_ref, expected_kind in type_mismatches or []:
        instructions.append(
            PatchInstruction(
                reason="type_fix",
                target_line_start=1,
                target_line_end=1,
                item_ref=item_ref,
                expected_kind=expected_kind,
            )
        )
    return instructions


# ── ② 确定性修复（0 LLM，就地修改 items）────────────────────


def apply_deterministic_fixes(
    instructions: list[PatchInstruction],
    items: list[SingleItemExtractLLM],
    *,
    marker_style: MarkerStyle = "mixed",
) -> tuple[list[PatchRecord], list[PatchInstruction]]:
    """尝试确定性修复 boundary_fix / pairing_fix / type_fix；``missed_marker``
    原样传递到剩余列表（留给调用方决定是否 micro LLM 补抽）。

    Returns:
        ``(records, remaining)``——``records`` 为已处理（含 skipped）的记录，
        ``remaining`` 为需要 micro LLM 或人工介入的指令（含未消耗的
        ``missed_marker``）。
    """
    by_ref = {getattr(it, "provisional_id", "") or str(i): it for i, it in enumerate(items)}
    records: list[PatchRecord] = []
    remaining: list[PatchInstruction] = []

    for instr in instructions:
        if instr.reason == "missed_marker":
            remaining.append(instr)
            continue

        item = by_ref.get(instr.item_ref)
        if item is None:
            records.append(
                PatchRecord(instruction=instr, method="skipped", detail="item_ref 未找到")
            )
            continue

        if instr.reason == "boundary_fix":
            end_offset = trim_tail(item.corrected_content)
            if 0 < end_offset < len(item.corrected_content):
                item.corrected_content = item.corrected_content[:end_offset].rstrip()
                records.append(
                    PatchRecord(
                        instruction=instr,
                        method="deterministic",
                        result_pid=instr.item_ref,
                        detail=f"边界回退至偏移 {end_offset}",
                    )
                )
            else:
                records.append(
                    PatchRecord(instruction=instr, method="skipped", detail="无更优边界可回退")
                )

        elif instr.reason == "pairing_fix":
            # 确定性只能"识别+标记"，真正补解答内容需要 LLM（留 needs_patch）。
            item.status = "needs_patch"
            records.append(
                PatchRecord(
                    instruction=instr,
                    method="deterministic",
                    result_pid=instr.item_ref,
                    detail="标记 needs_patch，待 micro LLM 补解答内容",
                )
            )

        elif instr.reason == "type_fix":
            if instr.expected_kind and instr.expected_kind != item.item_type:
                item.item_type = instr.expected_kind
                records.append(
                    PatchRecord(
                        instruction=instr,
                        method="deterministic",
                        result_pid=instr.item_ref,
                        detail=f"item_type 纠正为 {instr.expected_kind}",
                    )
                )
            else:
                records.append(
                    PatchRecord(instruction=instr, method="skipped", detail="类型已一致")
                )

    return records, remaining


def find_type_mismatches(
    items: list[SingleItemExtractLLM], markers: list[MarkerEntry]
) -> list[tuple[str, str]]:
    """对照条目**起始行**的 marker.kind 找出与 item_type 不一致的条目。

    只取 ``marker.global_line == item.start_line`` 的标记（条目真正的类型
    标识）——一个 item 区间内还可能覆盖内部的 proof/solution 等标记，不能
    以"最后一个覆盖到的 marker"为准，否则会用内部标记的 kind 误判类型。
    """
    markers_by_line = {m.global_line: m.kind for m in markers if m.status == "covered"}
    mismatches: list[tuple[str, str]] = []
    for i, item in enumerate(items):
        ref = getattr(item, "provisional_id", "") or str(i)
        expected = markers_by_line.get(item.start_line)
        if expected and expected != item.item_type:
            mismatches.append((ref, expected))
    return mismatches


# ── ③ micro LLM 补抽（仅 missed_marker 确定性补不出时）──────


async def micro_extract_item(
    llm: LLMClient,
    window_lines: list[str],
    instr: PatchInstruction,
    *,
    line_base: int,
    max_attempts: int = 3,
) -> tuple[SingleItemExtractLLM | None, int]:
    """对遗漏 marker 附近 ``±context_lines`` 行做单条提取（不重跑整窗）。

    Returns:
        ``(item_or_none, token_cost_est)``。LLM 判定"非独立条目"或提取失败
        （重试耗尽）→ ``(None, 0)``——调用方据此把 marker 标 ``ambiguous`` +
        造 ``uncertain`` 占位（宁可入库待复核，不丢弃）。
    """
    context_text = extract_subinterval(
        window_lines,
        instr.target_line_start,
        instr.target_line_end,
        line_base=line_base,
        context_lines=instr.context_lines,
    )
    if not context_text.strip():
        return None, 0

    from sciforge.prompts.loader import load_prompt

    template = load_prompt("pipeline/patch_micro_extract.txt")

    def build_prompt(feedback: str) -> str:
        prompt = template.format(expected_kind=instr.expected_kind, context_text=context_text)
        if feedback:
            prompt += f"\n\n## 上一次输出被拒绝，错误信息（请修正）\n{feedback}\n"
        return prompt

    try:
        result = await call_llm_json_with_schema_retry_with_feedback(
            llm,
            build_prompt,
            model=SingleItemExtractPayloadLLM,
            max_attempts=max_attempts,
            stage_name="patch_micro_extract",
        )
    except Exception:  # noqa: BLE001 — RetryExhausted 或其他失败一律降级为"补不出"
        return None, 0

    if result.extracted is None:
        return None, 0
    token_cost = len(context_text) // 4  # 粗估（无需精确，仅供成本仪表盘参考）
    return result.extracted, token_cost


def build_pairing_refs(
    items: list[SingleItemExtractLLM],
    *,
    window_last_line: int,
    near_tail_margin: int = 10,
    markers: list[MarkerEntry] | None = None,
) -> dict[str, str]:
    """跑 T8 check_pairing，转成 ``build_patch_instructions`` 需要的 ref→detail 映射。

    ``markers`` 传窗口台账（``build_marker_ledger`` 产物）时启用
    ``solution_marker_inside`` 检查（声称无解答但区间内有解答/证明标记）。
    """
    from sciforge.pipeline.text_tools.pairing import check_pairing

    pairing_items = [
        PairingItem(
            item_ref=getattr(item, "provisional_id", "") or str(i),
            category=item.category,
            item_type=item.item_type,
            solution_status=item.solution_status,
            has_payload=bool(item.corrected_proof) or bool(item.corrected_solution),
            start_line=item.start_line,
            end_line=item.end_line,
        )
        for i, item in enumerate(items)
    ]
    report = check_pairing(
        pairing_items,
        window_last_line=window_last_line,
        near_tail_margin=near_tail_margin,
        markers=markers,
    )
    return {issue.item_ref: issue.detail for issue in report.issues}
