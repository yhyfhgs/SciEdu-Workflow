"""VLM 内容审核误判 → 逐页回退（隔离单页失败）回归测试。

场景：MiniMax 等 provider 把某页图片误判为 sensitive（错误码 1026），
整批失败本会丢掉所有页。修复后回退逐页识别，只损失被误判的那一页。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sciforge.pipeline.vlm_converter import VlmConverter, _OpenedDocument


def _make_converter() -> VlmConverter:
    conv = VlmConverter.__new__(VlmConverter)
    conv.rate_limit_base_sleep = 0.0
    conv.rate_limit_max_sleep = 0.0
    conv.rate_limit_total_budget = 0.0
    conv._render_batch_images = lambda doc, pages: [b"img"]  # type: ignore[method-assign]
    conv._crop_figures_from_md = (  # type: ignore[method-assign]
        lambda md, doc, pages, stem, assets: md
    )
    return conv


def test_per_page_fallback_isolates_sensitive_page(tmp_path: Path) -> None:
    conv = _make_converter()

    # page index 1（第 2 页）触发内容审核，其余页正常
    async def fake_convert(*, page_nums, images, total_pages, prev_context):  # type: ignore[no-untyped-def]
        pno = page_nums[0]
        if pno == 1:
            raise RuntimeError(
                "Error code: 500 - image is sensitive, please check your input (1026)"
            )
        return f"# page {pno + 1} content"

    conv._convert_batch = fake_convert  # type: ignore[method-assign]
    doc = _OpenedDocument(pdf_path=Path("/x.pdf"), page_count=3, fitz_doc=None)

    async def run() -> list:
        out = []
        async for tup in conv._recognize_pages_individually(
            doc=doc,
            doc_lock=asyncio.Lock(),
            page_nums_0based=[0, 1, 2],
            total_pages=3,
            output_dir=tmp_path,
            prev_md_tail="",
            file_path=Path("/x.pdf"),
            assets_dir=tmp_path / "assets",
            max_retries=1,
        ):
            out.append(tup)
        return out

    results = asyncio.run(run())
    by_page = {r[0]: r for r in results}

    assert len(results) == 3
    # 第 1、3 页恢复成功
    assert by_page[1][4] is False and "page 1" in by_page[1][2]
    assert by_page[3][4] is False and "page 3" in by_page[3][2]
    # 第 2 页（被误判）隔离为失败，内容为空 —— 只丢这一页
    assert by_page[2][4] is True and by_page[2][2] == ""
    # 成功页落盘 page_NNN-NNN.md（单页）供 resume
    assert (tmp_path / "page_001-001.md").exists()
    assert (tmp_path / "page_003-003.md").exists()


def test_per_page_fallback_all_succeed(tmp_path: Path) -> None:
    """无误判时逐页全部恢复（回退路径本身不引入丢页）。"""
    conv = _make_converter()

    async def fake_convert(*, page_nums, images, total_pages, prev_context):  # type: ignore[no-untyped-def]
        return f"# p{page_nums[0] + 1}"

    conv._convert_batch = fake_convert  # type: ignore[method-assign]
    doc = _OpenedDocument(pdf_path=Path("/x.pdf"), page_count=2, fitz_doc=None)

    async def run() -> list:
        return [
            tup
            async for tup in conv._recognize_pages_individually(
                doc=doc,
                doc_lock=asyncio.Lock(),
                page_nums_0based=[0, 1],
                total_pages=2,
                output_dir=tmp_path,
                prev_md_tail="",
                file_path=Path("/x.pdf"),
                assets_dir=tmp_path / "assets",
                max_retries=1,
            )
        ]

    results = asyncio.run(run())
    assert len(results) == 2
    assert all(not r[4] for r in results)  # 全部成功
