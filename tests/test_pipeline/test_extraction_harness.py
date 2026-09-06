"""WP-43 ExtractionHarness 骨架——契约行为 + 干净窗口 1 次 LLM + 降级链。"""

from __future__ import annotations

import pytest

from sciforge.pipeline.boundary_patch import IncompleteItemCarry
from sciforge.pipeline.extraction_harness import HarnessResult, WindowContext, run_harness
from sciforge.pipeline.llm_schemas import BatchItemExtractPayloadLLM, SingleItemExtractLLM


def _annotated(lines: list[str], base: int) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


def _item(number: str, start: int, end: int, **overrides: object) -> SingleItemExtractLLM:
    base: dict[str, object] = {
        "category": "knowledge",
        "item_type": "theorem",
        "number": number,
        "corrected_content": f"内容 {number}",
        "corrected_proof": "证明。",
        "solution_status": "complete",
        "start_line": start,
        "end_line": end,
    }
    base.update(overrides)
    return SingleItemExtractLLM.model_validate(base)


class _CountingExtractor:
    """记录调用次数的 fake extract_fn，供验证"干净窗口恰 1 次 LLM"。"""

    def __init__(self, payload: BatchItemExtractPayloadLLM) -> None:
        self.payload = payload
        self.call_count = 0

    async def __call__(self, ctx: WindowContext) -> BatchItemExtractPayloadLLM:
        self.call_count += 1
        return self.payload


def _ctx(lines: list[str], *, line_base: int = 101, **overrides: object) -> WindowContext:
    base: dict[str, object] = {"window_lines": _annotated(lines, line_base), "line_base": line_base}
    base.update(overrides)
    return WindowContext.model_validate(base)


# ── HarnessResult 契约字段 ────────────────────────────────


@pytest.mark.asyncio
async def test_result_has_all_contract_fields() -> None:
    payload = BatchItemExtractPayloadLLM(
        items=[_item("3.1", 101, 102)], stop_reason="all_complete", last_consumed_line=102
    )
    extractor = _CountingExtractor(payload)
    result = await run_harness(_ctx(["定理 3.1 内容。", "证明：显然。"]), extract_fn=extractor)
    assert isinstance(result, HarnessResult)
    for field in (
        "accepted_items",
        "carry_out",
        "markers_total",
        "markers_covered",
        "audit_clean",
        "patch_log",
        "risk_score",
        "token_usage",
        "last_consumed_line",
        "stop_reason",
        "degraded",
    ):
        assert hasattr(result, field)


# ── 干净窗口恰 1 次 LLM ───────────────────────────────────


@pytest.mark.asyncio
async def test_clean_window_calls_extract_fn_exactly_once() -> None:
    payload = BatchItemExtractPayloadLLM(
        items=[_item("3.1", 101, 102)], stop_reason="all_complete", last_consumed_line=102
    )
    extractor = _CountingExtractor(payload)
    result = await run_harness(_ctx(["定理 3.1 内容。", "证明：显然。"]), extract_fn=extractor)
    assert extractor.call_count == 1
    assert result.audit_clean is True
    assert result.risk_score == 0.0
    assert result.degraded is False


@pytest.mark.asyncio
async def test_accepted_items_pass_through() -> None:
    items = [_item("3.1", 101, 102)]
    payload = BatchItemExtractPayloadLLM(
        items=items, stop_reason="all_complete", last_consumed_line=102
    )
    extractor = _CountingExtractor(payload)
    result = await run_harness(_ctx(["定理 3.1 内容。", "证明：显然。"]), extract_fn=extractor)
    assert result.accepted_items == items
    assert result.last_consumed_line == 102
    assert result.stop_reason == "all_complete"


# ── 审计发现遗漏 → risk_score 升高 ────────────────────────


@pytest.mark.asyncio
async def test_missed_marker_raises_risk_score() -> None:
    # 窗口内"习题 3.1"（103行）夹在两条已提取条目之间，但被漏提
    lines = [
        "定理 3.1 内容。",  # 101
        "证明：显然。",  # 102
        "习题 3.1 求极限。",  # 103（漏提）
        "定理 3.2 内容。",  # 104
    ]
    payload = BatchItemExtractPayloadLLM(
        items=[_item("3.1", 101, 102), _item("3.2", 104, 104)],
        stop_reason="all_complete",
        last_consumed_line=104,
    )
    extractor = _CountingExtractor(payload)
    result = await run_harness(_ctx(lines), extract_fn=extractor)
    assert result.audit_clean is False
    assert result.risk_score > 0.0
    assert extractor.call_count == 1  # 审计仍是 0 LLM


@pytest.mark.asyncio
async def test_incomplete_at_tail_reflected_in_stop_reason() -> None:
    payload = BatchItemExtractPayloadLLM(
        items=[_item("3.1", 101, 101)], stop_reason="incomplete_at_tail", last_consumed_line=101
    )
    extractor = _CountingExtractor(payload)
    lines = ["定理 3.1 内容。", "习题 3.2 求极限。"]  # 尾部未消费
    result = await run_harness(_ctx(lines), extract_fn=extractor)
    assert result.stop_reason == "incomplete_at_tail"


# ── carry_out 构建 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_carry_out_built_from_unconsumed_tail() -> None:
    lines = ["定理 3.1 内容。", "习题 3.2 设 $G$ 是群，证明"]
    payload = BatchItemExtractPayloadLLM(
        items=[_item("3.1", 101, 101)], stop_reason="incomplete_at_tail", last_consumed_line=101
    )
    extractor = _CountingExtractor(payload)
    result = await run_harness(_ctx(lines), extract_fn=extractor)
    assert result.carry_out is not None
    assert result.carry_out.number == "3.2"


@pytest.mark.asyncio
async def test_carry_abandoned_when_exceeding_max_windows() -> None:
    lines = ["习题 3.2 续写内容"]
    payload = BatchItemExtractPayloadLLM(
        items=[], stop_reason="incomplete_at_tail", last_consumed_line=0
    )
    extractor = _CountingExtractor(payload)
    carry_in = IncompleteItemCarry(
        item_type="exercise",
        number="3.2",
        category="question",
        head_anchor_start="习题 3.2",
        original_start_line=101,
        carry_window_count=4,  # 已超 max(3)+1
        tail_state="stem_unfinished",
    )
    result = await run_harness(
        _ctx(lines, carry_in=carry_in), extract_fn=extractor, carry_max_windows=3
    )
    assert result.carry_out is None  # 到顶放弃


# ── ② EXTRACT 失败：异常直接向上抛，不吞、不重试 ──────────


@pytest.mark.asyncio
async def test_extract_fn_exception_propagates() -> None:
    async def _failing_extract(ctx: WindowContext) -> BatchItemExtractPayloadLLM:
        raise RuntimeError("retry exhausted")

    with pytest.raises(RuntimeError, match="retry exhausted"):
        await run_harness(_ctx(["内容"]), extract_fn=_failing_extract)


# ── ①③⑤ 降级：不重复调用 extract_fn ───────────────────────


@pytest.mark.asyncio
async def test_downstream_failure_degrades_without_recalling_llm(monkeypatch) -> None:
    """③AUDIT 内部异常（模拟 bug）→ degraded=True，但 extract_fn 只调 1 次。"""
    import sciforge.pipeline.extraction_harness as harness_mod

    def _broken_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("audit bug")

    monkeypatch.setattr(harness_mod, "audit_coverage", _broken_audit)

    items = [_item("3.1", 101, 102)]
    payload = BatchItemExtractPayloadLLM(
        items=items, stop_reason="all_complete", last_consumed_line=102
    )
    extractor = _CountingExtractor(payload)
    result = await run_harness(_ctx(["定理 3.1 内容。", "证明：显然。"]), extract_fn=extractor)

    assert extractor.call_count == 1  # 关键：不因降级而重复调用 LLM
    assert result.degraded is True
    assert result.accepted_items == items  # 已拿到的提取结果不丢失
    assert result.stop_reason == "all_complete"


@pytest.mark.asyncio
async def test_empty_window_lines_handled() -> None:
    payload = BatchItemExtractPayloadLLM(
        items=[], stop_reason="no_items_found", last_consumed_line=0
    )
    extractor = _CountingExtractor(payload)
    result = await run_harness(WindowContext(window_lines=[], line_base=1), extract_fn=extractor)
    assert result.accepted_items == []
    assert extractor.call_count == 1
