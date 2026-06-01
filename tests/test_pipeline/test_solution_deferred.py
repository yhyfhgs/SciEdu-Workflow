"""solution_status 三态（Phase 2 #3）—— solution_deferred 校验与 near-tail 豁免"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sciforge.pipeline.llm_schemas import (
    BatchItemExtractPayloadLLM,
    SingleItemExtractLLM,
)


def test_solution_deferred_requires_null_payload() -> None:
    """solution_deferred 要求 proof/solution 均为 null。"""
    item = SingleItemExtractLLM(
        category="question",
        item_type="exercise",
        number="3.2",
        corrected_content="习题 3.2 求极限",
        corrected_proof=None,
        corrected_solution=None,
        solution_status="solution_deferred",
        start_line=10,
        end_line=12,
    )
    assert item.solution_status == "solution_deferred"

    with pytest.raises(ValidationError):
        SingleItemExtractLLM(
            category="question",
            item_type="exercise",
            number="3.2",
            corrected_content="习题 3.2",
            corrected_solution="解：x=1",  # deferred 不应带正文
            solution_status="solution_deferred",
            start_line=10,
            end_line=12,
        )


def test_solution_deferred_exempt_from_near_tail() -> None:
    """near-tail 守卫只拦 no_solution_in_source；solution_deferred 紧贴窗口末行也通过。"""
    deferred_item = SingleItemExtractLLM(
        category="question",
        item_type="exercise",
        number="3.2",
        corrected_content="习题 3.2 求极限（答案见章末）",
        solution_status="solution_deferred",
        start_line=95,
        end_line=99,
    )
    # window_last_line=100，end_line=99 → 距末行 1 < 10，若是 no_solution 会被拒
    payload = BatchItemExtractPayloadLLM.model_validate(
        {
            "items": [deferred_item.model_dump()],
            "stop_reason": "all_complete",
            "last_consumed_line": 99,
        },
        context={"window_last_line": 100},
    )
    assert len(payload.items) == 1

    # 对照：同位置 no_solution_in_source 应被拒
    with pytest.raises(ValidationError):
        BatchItemExtractPayloadLLM.model_validate(
            {
                "items": [
                    {
                        **deferred_item.model_dump(),
                        "solution_status": "no_solution_in_source",
                    }
                ],
                "stop_reason": "all_complete",
                "last_consumed_line": 99,
            },
            context={"window_last_line": 100},
        )
