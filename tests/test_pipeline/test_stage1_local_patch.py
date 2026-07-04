"""WP-33 Stage1._apply_local_patches 集成——开关双态、干净窗口 0 调用、micro LLM/占位。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline
from sciforge.pipeline.llm_schemas import SingleItemExtractPayloadLLM
from sciforge.pipeline.stages.stage1_extract import Stage1Extract


def _stage1(**overrides: object) -> Stage1Extract:
    config = AppConfig(**overrides)  # type: ignore[arg-type]
    llm = AsyncMock()
    llm.last_usage = {}
    return Stage1Extract(
        llm_client=llm,
        config=config,
        checkpoint_root=Path("/tmp/nonexistent"),
        discipline=Discipline.MATH,
    )


def _annotated(lines: list[str], base: int) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


class _Payload:
    def __init__(self, items: list[object]) -> None:
        self.items = items


def _item(number: str, start: int, end: int, **overrides: object):  # type: ignore[no-untyped-def]
    from sciforge.pipeline.llm_schemas import SingleItemExtractLLM

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


_WINDOW = [
    "定理 3.1 内容说明。",  # 101
    "证明：显然。",  # 102
    "习题 3.1 求极限。",  # 103（将被"漏提"）
    "",  # 104
]


@pytest.mark.asyncio
async def test_flag_off_returns_empty() -> None:
    stage = _stage1()
    result = await stage._apply_local_patches(
        _annotated(_WINDOW, 101), 101, 4, _Payload([_item("3.1", 101, 102)])
    )
    assert result == []
    stage.llm.generate_json.assert_not_called()


@pytest.mark.asyncio
async def test_patch_requires_ledger_enabled() -> None:
    """patch 开但 ledger 未开 → 跳过并 warning，不调用 LLM。"""
    stage = _stage1(patch_local_extraction_enabled=True, marker_ledger_enabled=False)
    result = await stage._apply_local_patches(
        _annotated(_WINDOW, 101), 101, 4, _Payload([_item("3.1", 101, 102)])
    )
    assert result == []
    stage.llm.generate_json.assert_not_called()


@pytest.mark.asyncio
async def test_clean_window_zero_llm_calls() -> None:
    """全部 marker 都被覆盖 → 审计 clean → 0 额外 LLM 调用。"""
    stage = _stage1(patch_local_extraction_enabled=True, marker_ledger_enabled=True)
    items = [
        _item("3.1", 101, 102),
        _item(
            "3.1",
            103,
            103,
            category="question",
            item_type="exercise",
            solution_status="no_solution_in_source",
            corrected_proof=None,
        ),
    ]
    result = await stage._apply_local_patches(_annotated(_WINDOW, 101), 101, 4, _Payload(items))
    assert result == []
    stage.llm.generate_json.assert_not_called()


@pytest.mark.asyncio
async def test_missed_marker_micro_extract_success() -> None:
    """习题 3.1 漏提 → micro LLM 补抽成功 → 返回新条目。"""
    stage = _stage1(patch_local_extraction_enabled=True, marker_ledger_enabled=True)
    fake_extracted = _item(
        "3.1", 103, 103, category="question", item_type="exercise", corrected_content="求极限。"
    )
    stage.llm.generate_json = AsyncMock(
        return_value=SingleItemExtractPayloadLLM(extracted=fake_extracted).model_dump(mode="json")
    )
    items = [_item("3.1", 101, 102)]  # 只提了定理，漏了习题 3.1
    result = await stage._apply_local_patches(_annotated(_WINDOW, 101), 101, 4, _Payload(items))
    assert len(result) == 1
    assert result[0].number == "3.1"
    assert result[0].provisional_id.startswith("w101_patch")


@pytest.mark.asyncio
async def test_missed_marker_unresolved_creates_uncertain_placeholder() -> None:
    """micro LLM 补不出 → 占位条目 status=uncertain，宁可入库待复核。"""
    stage = _stage1(patch_local_extraction_enabled=True, marker_ledger_enabled=True)
    stage.llm.generate_json = AsyncMock(
        return_value=SingleItemExtractPayloadLLM(extracted=None, issues=["无法确认"]).model_dump(
            mode="json"
        )
    )
    items = [_item("3.1", 101, 102)]
    result = await stage._apply_local_patches(_annotated(_WINDOW, 101), 101, 4, _Payload(items))
    assert len(result) == 1
    assert result[0].status == "uncertain"
    assert result[0].item_type == "exercise"


@pytest.mark.asyncio
async def test_type_fix_corrects_without_llm_call() -> None:
    """item_type 与 marker.kind 不符 → 确定性纠正，不调 LLM。"""
    stage = _stage1(patch_local_extraction_enabled=True, marker_ledger_enabled=True)
    items = [
        _item("3.1", 101, 102, item_type="definition"),  # marker 是 theorem，应纠正
        _item(
            "3.1",
            103,
            103,
            category="question",
            item_type="exercise",
            solution_status="no_solution_in_source",
            corrected_proof=None,
        ),
    ]
    result = await stage._apply_local_patches(_annotated(_WINDOW, 101), 101, 4, _Payload(items))
    assert result == []  # type_fix 不产生新条目
    assert items[0].item_type == "theorem"  # 就地纠正
    stage.llm.generate_json.assert_not_called()


@pytest.mark.asyncio
async def test_exception_degrades_to_empty_list() -> None:
    """审计内部异常（如损坏输入）→ 静默降级为 []，不阻断流水线。"""
    stage = _stage1(patch_local_extraction_enabled=True, marker_ledger_enabled=True)

    class _Broken:
        items = None  # 触发迭代 None 异常

    result = await stage._apply_local_patches(_annotated(_WINDOW, 101), 101, 4, _Broken())
    assert result == []
