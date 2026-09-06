"""REC-02 — VLM 批内有界并发 + 保序 yield。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from sciforge.pipeline.vlm_converter import VlmConverter, _OpenedDocument


def _make_conv(max_concurrent: int, tmp_path: Path, monkeypatch: Any) -> VlmConverter:
    conv = VlmConverter.__new__(VlmConverter)
    conv.max_concurrent = max_concurrent
    conv.rate_limit_base_sleep = 0.0
    conv.rate_limit_max_sleep = 0.0
    conv.rate_limit_total_budget = 0.0
    fake_doc = _OpenedDocument(pdf_path=tmp_path / "x.pdf", page_count=6, fitz_doc=None)
    monkeypatch.setattr(conv, "_open_document", lambda fp: fake_doc)
    monkeypatch.setattr(conv, "_assets_dir", lambda fp: tmp_path / "assets")
    monkeypatch.setattr(conv, "_render_batch_images", lambda doc, nums: [b"img" for _ in nums])
    monkeypatch.setattr(conv, "_crop_figures_from_md", lambda raw, doc, nums, stem, assets: raw)
    return conv


async def _collect(conv: VlmConverter, tmp_path: Path) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    async for ps, pe, md, _path, failed in conv.recognize_in_batches(
        tmp_path / "x.pdf",
        batch_pages=1,
        output_dir=tmp_path / "rec",
        already_done=[],
        max_retries=1,
    ):
        assert not failed
        out.append((ps, pe, md))
    return out


@pytest.mark.asyncio
async def test_concurrent_batches_ordered_and_parallel(tmp_path: Path, monkeypatch: Any) -> None:
    conv = _make_conv(3, tmp_path, monkeypatch)

    inflight = 0
    max_inflight = 0

    async def fake_convert(
        *, page_nums: list[int], images: list[bytes], total_pages: int, prev_context: str
    ) -> str:
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.02)
        inflight -= 1
        return f"md p{page_nums[0] + 1}"

    monkeypatch.setattr(conv, "_convert_batch", fake_convert)

    out = await _collect(conv, tmp_path)
    assert [o[0] for o in out] == [1, 2, 3, 4, 5, 6]  # 保序
    assert [o[2] for o in out] == [f"md p{i}" for i in range(1, 7)]
    assert max_inflight > 1, f"应并发，实际最大在途 {max_inflight}"


@pytest.mark.asyncio
async def test_serial_mode_when_max_concurrent_one(tmp_path: Path, monkeypatch: Any) -> None:
    conv = _make_conv(1, tmp_path, monkeypatch)

    inflight = 0
    max_inflight = 0

    async def fake_convert(
        *, page_nums: list[int], images: list[bytes], total_pages: int, prev_context: str
    ) -> str:
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.005)
        inflight -= 1
        return f"md p{page_nums[0] + 1}"

    monkeypatch.setattr(conv, "_convert_batch", fake_convert)

    out = await _collect(conv, tmp_path)
    assert [o[0] for o in out] == [1, 2, 3, 4, 5, 6]
    assert max_inflight == 1, "max_concurrent=1 应严格串行"
