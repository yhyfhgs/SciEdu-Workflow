from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline, DocumentFormat
from sciforge.pipeline.orchestrator import PipelineOrchestrator
from sciforge.pipeline.recognizers import AdapterRecognizer, ImageRecognizer, get_recognizer


@pytest.mark.asyncio
async def test_adapter_recognizer_checkpoints_csv_blocks(tmp_path: Path) -> None:
    source = tmp_path / "questions.csv"
    source.write_text("id,stem\n1,Alpha\n2,Beta\n", encoding="utf-8")
    output = tmp_path / "recognize"
    recognizer = get_recognizer(
        DocumentFormat.CSV,
        backend="auto",
        llm_client=None,
        config=AppConfig(adapter_rows_per_block=1),
    )

    assert isinstance(recognizer, AdapterRecognizer)
    batches = [
        batch
        async for batch in recognizer.recognize_in_batches(
            source,
            batch_pages=5,
            output_dir=output,
            already_done=[],
        )
    ]

    assert [(batch.page_start, batch.page_end) for batch in batches] == [(1, 1), (2, 2)]
    assert "Alpha" in batches[0].md_text
    assert "Beta" in batches[1].md_text
    assert sorted(path.name for path in output.glob("page_*.md")) == [
        "page_001-001.md",
        "page_002-002.md",
    ]


class _ImageLLM:
    supports_vision = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_with_image(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "## Problem 1\n\nSolve $x+1=2$."


@pytest.mark.asyncio
async def test_image_recognizer_uses_one_bounded_vlm_call(tmp_path: Path) -> None:
    source = tmp_path / "problem.png"
    source.write_bytes(b"not-a-real-png-but-the-fake-backend-does-not-decode")
    output = tmp_path / "recognize"
    llm = _ImageLLM()
    recognizer = get_recognizer(
        DocumentFormat.IMAGE,
        backend="auto",
        llm_client=llm,  # type: ignore[arg-type]
        config=AppConfig(vlm_image_max_tokens=4096),
        discipline=Discipline.MATH,
    )

    assert isinstance(recognizer, ImageRecognizer)
    batches = [
        batch
        async for batch in recognizer.recognize_in_batches(
            source,
            batch_pages=5,
            output_dir=output,
            already_done=[],
        )
    ]

    assert len(batches) == 1
    assert len(llm.calls) == 1
    assert llm.calls[0]["image_media_type"] == "image/png"
    assert llm.calls[0]["max_tokens"] == 4096

    # checkpoint 命中时不得再次调用 VLM。
    cached = [
        batch
        async for batch in recognizer.recognize_in_batches(
            source,
            batch_pages=5,
            output_dir=output,
            already_done=[(1, 1)],
        )
    ]
    assert cached[0].md_text == batches[0].md_text
    assert len(llm.calls) == 1


def test_format_detection_and_directory_allowlist(tmp_path: Path) -> None:
    expected = {
        "a.tsv": DocumentFormat.TSV,
        "a.xlsx": DocumentFormat.XLSX,
        "a.htm": DocumentFormat.HTML,
        "a.ndjson": DocumentFormat.JSONL,
        "a.rst": DocumentFormat.TEXT,
        "a.webp": DocumentFormat.IMAGE,
    }
    for name, fmt in expected.items():
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        assert PipelineOrchestrator._detect_format(path) == fmt

    assert {
        path.name for path in PipelineOrchestrator._collect_files(tmp_path, ["xlsx", "rst"])
    } == {
        "a.xlsx",
        "a.rst",
    }


def test_unknown_format_is_rejected() -> None:
    with pytest.raises(ValueError, match="不支持的输入格式"):
        PipelineOrchestrator._detect_format(Path("source.bin"))
