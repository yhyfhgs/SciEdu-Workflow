"""ExtractionHarness / 风险 Evaluator 在 Stage1 生产窗口入口的回归测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline
from sciforge.llm.config import LLMProvider
from sciforge.pipeline.extract_evaluator import EvaluatorVerdict
from sciforge.pipeline.llm_schemas import BatchItemExtractPayloadLLM, SingleItemExtractLLM
from sciforge.pipeline.stages.stage1_extract import Stage1Extract


class _FakeLLMConfig:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider


class _FakeLLM:
    def __init__(self, provider: LLMProvider) -> None:
        self.config = _FakeLLMConfig(provider)


def _stage(provider: LLMProvider = LLMProvider.ANTHROPIC) -> Stage1Extract:
    return Stage1Extract(
        llm_client=_FakeLLM(provider),  # type: ignore[arg-type]
        config=AppConfig(
            extract_harness_enabled=True,
            extract_validator_enabled=True,
            extract_validator_risk_threshold=0.3,
        ),
        checkpoint_root=Path("/tmp/nonexistent"),
        discipline=Discipline.MATH,
    )


def _annotated(lines: list[str], base: int = 101) -> list[str]:
    return [f"{base + index:>7d} | {line}\n" for index, line in enumerate(lines)]


def _item(number: str, start: int, end: int) -> SingleItemExtractLLM:
    return SingleItemExtractLLM(
        category="knowledge",
        item_type="theorem",
        number=number,
        corrected_content=f"定理 {number}",
        corrected_proof="证明。",
        solution_status="complete",
        start_line=start,
        end_line=end,
    )


def _clean_payload() -> BatchItemExtractPayloadLLM:
    return BatchItemExtractPayloadLLM(
        items=[_item("3.1", 101, 102)],
        stop_reason="all_complete",
        last_consumed_line=102,
    )


def _high_risk_payload() -> BatchItemExtractPayloadLLM:
    return BatchItemExtractPayloadLLM(
        items=[_item("3.1", 101, 102), _item("3.2", 104, 104)],
        stop_reason="all_complete",
        last_consumed_line=104,
    )


@pytest.mark.asyncio
async def test_clean_window_calls_main_extract_once_and_no_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _stage()
    extract = AsyncMock(return_value=_clean_payload())
    evaluator = AsyncMock(return_value=EvaluatorVerdict(ran=True))
    monkeypatch.setattr(stage, "_extract_batch", extract)
    monkeypatch.setattr("sciforge.pipeline.extract_evaluator.run_evaluator", evaluator)
    window = _annotated(["定理 3.1 内容。", "证明：显然。"])

    result = await stage._extract_window(window, 101, 102)

    assert result == _clean_payload()
    extract.assert_awaited_once()
    evaluator.assert_not_awaited()


@pytest.mark.asyncio
async def test_high_risk_window_only_adds_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _stage()
    extract = AsyncMock(return_value=_high_risk_payload())
    evaluator = AsyncMock(
        return_value=EvaluatorVerdict(
            ran=True,
            turns_used=2,
            tool_calls_made=1,
            summary="发现疑似遗漏习题 3.1",
        )
    )
    monkeypatch.setattr(stage, "_extract_batch", extract)
    monkeypatch.setattr("sciforge.pipeline.extract_evaluator.run_evaluator", evaluator)
    window = _annotated(
        ["定理 3.1 内容。", "证明：显然。", "习题 3.1 求极限。", "定理 3.2 内容。"]
    )

    result = await stage._extract_window(window, 101, 104)

    assert result == _high_risk_payload()
    extract.assert_awaited_once()
    evaluator.assert_awaited_once()


@pytest.mark.asyncio
async def test_unsupported_provider_never_calls_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _stage(LLMProvider.OPENAI)
    extract = AsyncMock(return_value=_high_risk_payload())
    evaluator = AsyncMock(return_value=EvaluatorVerdict(ran=True))
    monkeypatch.setattr(stage, "_extract_batch", extract)
    monkeypatch.setattr("sciforge.pipeline.extract_evaluator.run_evaluator", evaluator)
    window = _annotated(
        ["定理 3.1 内容。", "证明：显然。", "习题 3.1 求极限。", "定理 3.2 内容。"]
    )

    await stage._extract_window(window, 101, 104)

    extract.assert_awaited_once()
    evaluator.assert_not_awaited()


@pytest.mark.asyncio
async def test_harness_failure_after_extract_uses_first_result_without_reextract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _stage()
    payload = _clean_payload()
    extract = AsyncMock(return_value=payload)
    monkeypatch.setattr(stage, "_extract_batch", extract)

    async def broken_harness(context, *, extract_fn, carry_max_windows):  # type: ignore[no-untyped-def]
        del carry_max_windows
        await extract_fn(context)
        raise RuntimeError("enhancement failed")

    monkeypatch.setattr("sciforge.pipeline.extraction_harness.run_harness", broken_harness)
    window = _annotated(["定理 3.1 内容。", "证明：显然。"])

    result = await stage._extract_window(window, 101, 102)

    assert result is payload
    extract.assert_awaited_once()


@pytest.mark.asyncio
async def test_evaluator_failure_keeps_first_result_without_reextract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = _stage()
    payload = _high_risk_payload()
    extract = AsyncMock(return_value=payload)
    evaluator = AsyncMock(side_effect=RuntimeError("validator unavailable"))
    monkeypatch.setattr(stage, "_extract_batch", extract)
    monkeypatch.setattr("sciforge.pipeline.extract_evaluator.run_evaluator", evaluator)
    window = _annotated(
        ["定理 3.1 内容。", "证明：显然。", "习题 3.1 求极限。", "定理 3.2 内容。"]
    )

    result = await stage._extract_window(window, 101, 104)

    assert result is payload
    extract.assert_awaited_once()
    evaluator.assert_awaited_once()
