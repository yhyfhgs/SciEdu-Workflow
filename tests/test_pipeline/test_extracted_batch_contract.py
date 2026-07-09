"""Stage1 最终出口 ExtractedBatch 的并行数组与顺序契约。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sciforge.core.models import ExtractedBatch
from sciforge.pipeline.llm_schemas import SingleItemExtractLLM


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


def test_accepts_aligned_strict_ids_and_nondecreasing_start_lines() -> None:
    batch = ExtractedBatch(
        item_ids=[7, 8, 9],
        items=[_item("a", 10, 11), _item("b", 10, 12), _item("c", 20, 21)],
    )
    assert batch.item_ids == [7, 8, 9]


def test_rejects_parallel_array_length_mismatch() -> None:
    with pytest.raises(ValidationError, match="长度必须一致"):
        ExtractedBatch(item_ids=[1], items=[_item("1", 10, 11), _item("2", 20, 21)])


@pytest.mark.parametrize("item_ids", ([1, 1], [2, 1]))
def test_rejects_non_increasing_item_ids(item_ids: list[int]) -> None:
    with pytest.raises(ValidationError, match="严格递增"):
        ExtractedBatch(
            item_ids=item_ids,
            items=[_item("1", 10, 11), _item("2", 20, 21)],
        )


def test_rejects_items_out_of_source_order() -> None:
    with pytest.raises(ValidationError, match="start_line 非降序"):
        ExtractedBatch(
            item_ids=[1, 2],
            items=[_item("2", 20, 21), _item("1", 10, 11)],
        )
