"""retry.py with_feedback 接口测试"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from sciforge.core.exceptions import LLMError
from sciforge.pipeline.llm_schemas import SingleItemExtractPayloadLLM
from sciforge.pipeline.retry import (
    RetryExhausted,
    call_llm_json_with_schema_retry_with_feedback,
)


class _FakeLLM:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.prompts_seen: list[str] = []

    async def generate_json(self, prompt: str, **kw: Any) -> Any:
        self.prompts_seen.append(prompt)
        return self.responses.pop(0)


class _FlakyLLM(_FakeLLM):
    async def generate_json(self, prompt: str, **kw: Any) -> Any:
        self.prompts_seen.append(prompt)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def good_payload():
    return {
        "extracted": {
            "category": "knowledge",
            "item_type": "theorem",
            "number": "1",
            "corrected_content": "Statement.",
            "corrected_proof": "Proof.",
            "corrected_solution": None,
            "solution_status": "complete",
            "start_line": 1,
            "end_line": 5,
        }
    }


class TestWithFeedback:
    def test_first_attempt_succeeds(self, good_payload):
        llm = _FakeLLM([good_payload])

        def build(fb: str) -> str:
            return f"PROMPT|fb={fb}"

        result = asyncio.run(
            call_llm_json_with_schema_retry_with_feedback(
                llm,
                build,
                model=SingleItemExtractPayloadLLM,
                max_attempts=3,
                base_delay=0.01,
            )
        )
        assert result.extracted.item_type == "theorem"
        assert len(llm.prompts_seen) == 1
        assert llm.prompts_seen[0] == "PROMPT|fb="

    def test_retry_injects_validation_error(self, good_payload):
        bad = {"extracted": {"category": "wrong_category"}}
        llm = _FakeLLM([bad, good_payload])

        def build(fb: str) -> str:
            return f"PROMPT|fb={fb}"

        result = asyncio.run(
            call_llm_json_with_schema_retry_with_feedback(
                llm,
                build,
                model=SingleItemExtractPayloadLLM,
                max_attempts=3,
                base_delay=0.01,
            )
        )
        assert result.extracted is not None
        assert len(llm.prompts_seen) == 2
        # 第二次 prompt 里必须有反馈文本
        assert "fb=" in llm.prompts_seen[1]
        assert llm.prompts_seen[1] != "PROMPT|fb="
        assert "category" in llm.prompts_seen[1]

    def test_exhausts_retries(self, good_payload):
        bad = {"extracted": {"category": "wrong_category"}}
        llm = _FakeLLM([bad, bad, bad])

        def build(fb: str) -> str:
            return f"PROMPT|fb={fb}"

        with pytest.raises(RetryExhausted):
            asyncio.run(
                call_llm_json_with_schema_retry_with_feedback(
                    llm,
                    build,
                    model=SingleItemExtractPayloadLLM,
                    max_attempts=3,
                    base_delay=0.01,
                )
            )
        assert len(llm.prompts_seen) == 3

    def test_retry_retries_llm_error_without_polluting_feedback(self, good_payload):
        llm = _FlakyLLM([LLMError("429 rate_limit_error"), good_payload])

        def build(fb: str) -> str:
            return f"PROMPT|fb={fb}"

        result = asyncio.run(
            call_llm_json_with_schema_retry_with_feedback(
                llm,
                build,
                model=SingleItemExtractPayloadLLM,
                max_attempts=3,
                base_delay=0.01,
            )
        )

        assert result.extracted is not None
        assert llm.prompts_seen == ["PROMPT|fb=", "PROMPT|fb="]
