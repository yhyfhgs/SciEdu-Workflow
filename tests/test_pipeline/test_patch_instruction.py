"""WP-33 PatchInstruction 局部补抽（III.3）——确定性修复 + micro LLM。"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sciforge.pipeline.boundary_patch import (
    BoundarySuspect,
    MarkerEntry,
    build_marker_ledger,
)
from sciforge.pipeline.llm_schemas import (
    SingleItemExtractLLM,
    SingleItemExtractPayloadLLM,
)
from sciforge.pipeline.patch_instruction import (
    PatchInstruction,
    apply_deterministic_fixes,
    build_pairing_refs,
    build_patch_instructions,
    find_type_mismatches,
    micro_extract_item,
)


def _item(
    provisional_id: str,
    number: str,
    start: int,
    end: int,
    *,
    item_type: str = "theorem",
    content: str = "内容正文。",
    proof: str | None = "证明正文。",
    status: str = "complete",
) -> SingleItemExtractLLM:
    return SingleItemExtractLLM(
        category="knowledge",
        item_type=item_type,
        number=number,
        corrected_content=content,
        corrected_proof=proof,
        solution_status=status,
        start_line=start,
        end_line=end,
        provisional_id=provisional_id,
    )


# ── build_patch_instructions（①）─────────────────────────


def test_build_instructions_from_missed_markers() -> None:
    marker = MarkerEntry(global_line=5, kind="theorem", marker_text="定理 1", strength="strong")
    report = _report_with(missed=[marker])
    instructions = build_patch_instructions(report)
    assert len(instructions) == 1
    assert instructions[0].reason == "missed_marker"
    assert instructions[0].around_marker == marker
    assert instructions[0].expected_kind == "theorem"


def test_build_instructions_from_boundary_suspects() -> None:
    marker = MarkerEntry(global_line=8, kind="theorem", marker_text="定理 2", strength="strong")
    suspect = BoundarySuspect(item_ref="p1", marker=marker, detail="内部标记")
    report = _report_with(boundary=[suspect])
    instructions = build_patch_instructions(report)
    assert instructions[0].reason == "boundary_fix"
    assert instructions[0].item_ref == "p1"


def test_build_instructions_from_pairing_and_type() -> None:
    report = _report_with()
    instructions = build_patch_instructions(
        report,
        pairing_issue_refs={"p2": "声称无解答但有证明标记"},
        type_mismatches=[("p3", "definition")],
    )
    reasons = {i.reason for i in instructions}
    assert reasons == {"pairing_fix", "type_fix"}


def _report_with(missed=None, boundary=None):  # type: ignore[no-untyped-def]
    from sciforge.pipeline.boundary_patch import AuditReport

    return AuditReport(missed_markers=missed or [], boundary_suspects=boundary or [])


# ── apply_deterministic_fixes（②）─────────────────────────


def test_boundary_fix_trims_trailing_content() -> None:
    item = _item("p1", "3.1", 1, 5, content="定理内容说明。证毕。定理 3.2 无关内容。")
    instr = PatchInstruction(
        reason="boundary_fix", target_line_start=1, target_line_end=1, item_ref="p1"
    )
    records, remaining = apply_deterministic_fixes([instr], [item])
    assert remaining == []
    assert records[0].method == "deterministic"
    assert item.corrected_content == "定理内容说明。证毕。"


def test_boundary_fix_skips_when_no_better_boundary() -> None:
    item = _item("p1", "3.1", 1, 5, content="没有明确边界标记的一段话")
    instr = PatchInstruction(
        reason="boundary_fix", target_line_start=1, target_line_end=1, item_ref="p1"
    )
    records, _ = apply_deterministic_fixes([instr], [item])
    assert records[0].method == "skipped"
    assert item.corrected_content == "没有明确边界标记的一段话"  # 不劣于现状


def test_pairing_fix_marks_needs_patch() -> None:
    item = _item("p1", "3.1", 1, 5, proof=None, status="no_solution_in_source")
    instr = PatchInstruction(
        reason="pairing_fix", target_line_start=1, target_line_end=1, item_ref="p1"
    )
    records, _ = apply_deterministic_fixes([instr], [item])
    assert item.status == "needs_patch"
    assert records[0].method == "deterministic"


def test_type_fix_corrects_item_type() -> None:
    item = _item("p1", "3.1", 1, 5, item_type="definition")
    instr = PatchInstruction(
        reason="type_fix",
        target_line_start=1,
        target_line_end=1,
        item_ref="p1",
        expected_kind="theorem",
    )
    records, _ = apply_deterministic_fixes([instr], [item])
    assert item.item_type == "theorem"
    assert records[0].method == "deterministic"


def test_type_fix_skipped_when_already_matches() -> None:
    item = _item("p1", "3.1", 1, 5, item_type="theorem")
    instr = PatchInstruction(
        reason="type_fix",
        target_line_start=1,
        target_line_end=1,
        item_ref="p1",
        expected_kind="theorem",
    )
    records, _ = apply_deterministic_fixes([instr], [item])
    assert records[0].method == "skipped"


def test_missed_marker_passed_through_unconsumed() -> None:
    marker = MarkerEntry(global_line=5, kind="theorem", marker_text="定理", strength="strong")
    instr = PatchInstruction(
        reason="missed_marker", target_line_start=5, target_line_end=5, around_marker=marker
    )
    records, remaining = apply_deterministic_fixes([instr], [])
    assert records == []
    assert remaining == [instr]


def test_unknown_item_ref_skipped_gracefully() -> None:
    instr = PatchInstruction(
        reason="type_fix",
        target_line_start=1,
        target_line_end=1,
        item_ref="不存在",
        expected_kind="theorem",
    )
    records, _ = apply_deterministic_fixes([instr], [_item("p1", "1", 1, 2)])
    assert records[0].method == "skipped"
    assert "未找到" in records[0].detail


# ── find_type_mismatches ──────────────────────────────────


def test_find_type_mismatches_detects_wrong_kind() -> None:
    item = _item("p1", "3.1", 1, 3, item_type="definition")
    marker = MarkerEntry(
        global_line=1,
        kind="theorem",
        marker_text="定理 3.1",
        strength="strong",
        status="covered",
        covered_by="p1",
    )
    mismatches = find_type_mismatches([item], [marker])
    assert mismatches == [("p1", "theorem")]


def test_find_type_mismatches_none_when_matching() -> None:
    item = _item("p1", "3.1", 1, 3, item_type="theorem")
    marker = MarkerEntry(
        global_line=1,
        kind="theorem",
        marker_text="定理 3.1",
        strength="strong",
        status="covered",
        covered_by="p1",
    )
    assert find_type_mismatches([item], [marker]) == []


# ── build_pairing_refs ────────────────────────────────────


def test_build_pairing_refs_flags_near_tail_no_solution() -> None:
    item = _item(
        "p1",
        "3.1",
        101,
        195,
        item_type="exercise",
        proof=None,
        status="no_solution_in_source",
    )
    refs = build_pairing_refs([item], window_last_line=200)
    assert "p1" in refs


def test_build_pairing_refs_flags_solution_marker_inside_with_ledger() -> None:
    item = _item(
        "p1", "3.1", 101, 103, proof=None, content="习题内容。", status="no_solution_in_source"
    )
    ledger = build_marker_ledger(["习题 3.1 求证。", "解：直接计算。"], line_base=101)
    refs = build_pairing_refs([item], window_last_line=200, markers=ledger.markers)
    assert "p1" in refs


def test_build_pairing_refs_empty_when_clean() -> None:
    item = _item("p1", "3.1", 101, 103)
    assert build_pairing_refs([item], window_last_line=200) == {}


# ── micro_extract_item（③，mock LLM）──────────────────────


def _annotated(lines: list[str], base: int) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


@pytest.mark.asyncio
async def test_micro_extract_returns_item_on_success() -> None:
    window = _annotated(["定理 5.1 陈述内容。", "证明：显然。"], 101)
    marker = MarkerEntry(global_line=101, kind="theorem", marker_text="定理 5.1", strength="strong")
    instr = PatchInstruction(
        reason="missed_marker",
        target_line_start=101,
        target_line_end=102,
        around_marker=marker,
        expected_kind="theorem",
        context_lines=2,
    )
    fake_item = SingleItemExtractLLM(
        category="knowledge",
        item_type="theorem",
        number="5.1",
        corrected_content="陈述内容。",
        corrected_proof="显然。",
        solution_status="complete",
        start_line=101,
        end_line=102,
    )
    llm = AsyncMock()
    llm.last_usage = {}
    llm.generate_json = AsyncMock(
        return_value=SingleItemExtractPayloadLLM(extracted=fake_item).model_dump(mode="json")
    )
    item, cost = await micro_extract_item(llm, window, instr, line_base=101)
    assert item is not None
    assert item.number == "5.1"
    assert cost >= 0


@pytest.mark.asyncio
async def test_micro_extract_returns_none_when_llm_says_not_an_item() -> None:
    window = _annotated(["这只是正文引用。"], 101)
    instr = PatchInstruction(
        reason="missed_marker", target_line_start=101, target_line_end=101, context_lines=1
    )
    llm = AsyncMock()
    llm.last_usage = {}
    llm.generate_json = AsyncMock(
        return_value=SingleItemExtractPayloadLLM(extracted=None, issues=["非独立条目"]).model_dump(
            mode="json"
        )
    )
    item, cost = await micro_extract_item(llm, window, instr, line_base=101)
    assert item is None
    assert cost == 0


@pytest.mark.asyncio
async def test_micro_extract_returns_none_on_retry_exhausted() -> None:
    window = _annotated(["内容"], 101)
    instr = PatchInstruction(
        reason="missed_marker", target_line_start=101, target_line_end=101, context_lines=1
    )
    llm = AsyncMock()
    llm.last_usage = {}
    llm.generate_json = AsyncMock(side_effect=RuntimeError("network down"))
    item, cost = await micro_extract_item(llm, window, instr, line_base=101, max_attempts=1)
    assert item is None
    assert cost == 0


@pytest.mark.asyncio
async def test_micro_extract_empty_context_short_circuits() -> None:
    """行号越界导致取不到上下文 → 不调用 LLM，直接返回 None。"""
    llm = AsyncMock()
    instr = PatchInstruction(reason="missed_marker", target_line_start=999, target_line_end=999)
    item, cost = await micro_extract_item(llm, [], instr, line_base=1)
    assert item is None
    assert cost == 0
    llm.generate_json.assert_not_called()
