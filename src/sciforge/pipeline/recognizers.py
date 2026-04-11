"""Producer (Task0) — 文档识别 Recognizer 协议与具体实现

每个 Recognizer 暴露统一的异步生成器接口 `recognize_in_batches`，
按 N 页一批 yield BatchResult，每个 batch 同时落盘到 page_NNN-NNN.md。

已支持的格式:
- VLM (PDF / DOCX): 多模态 LLM 高精度识别
- PyMuPDF (PDF): 纯文本提取，无 LLM
- Markdown / Tex: 整文件单 batch
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog

from sciforge.core.models import DocumentFormat

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()


# ── 共享数据结构 ───────────────────────────────────────


@dataclass
class BatchResult:
    """单个识别 batch 的产物"""

    page_start: int  # 1-based
    page_end: int  # 1-based, inclusive
    md_text: str
    md_path: Path


# ── 协议 ────────────────────────────────────────────


class Recognizer(Protocol):
    """文档识别器统一协议（async generator 接口）"""

    def recognize_in_batches(
        self,
        file_path: Path,
        *,
        batch_pages: int,
        output_dir: Path,
        already_done: list[tuple[int, int]],
        max_retries: int = 3,
    ) -> AsyncIterator[BatchResult]: ...


# ── 实现 1: VlmRecognizer ───────────────────────────


class VlmRecognizer:
    """基于 VlmConverter 的多模态 PDF/DOCX → Markdown 识别器"""

    def __init__(self, llm_client: LLMClient, config: AppConfig) -> None:
        from sciforge.pipeline.vlm_converter import VlmConverter

        self._converter = VlmConverter(
            llm_client=llm_client,
            checkpoint_dir=None,  # recognizers 自己管 checkpoint 路径
            output_dir=config.data_dir,
            dpi=config.vlm_dpi,
            max_concurrent=config.vlm_max_concurrent,
            pages_per_batch=config.pages_per_batch,
        )

    async def recognize_in_batches(
        self,
        file_path: Path,
        *,
        batch_pages: int,
        output_dir: Path,
        already_done: list[tuple[int, int]],
        max_retries: int = 3,
    ) -> AsyncIterator[BatchResult]:
        async for ps, pe, md, path in self._converter.recognize_in_batches(
            file_path,
            batch_pages=batch_pages,
            output_dir=output_dir,
            already_done=already_done,
            max_retries=max_retries,
        ):
            yield BatchResult(
                page_start=ps,
                page_end=pe,
                md_text=md,
                md_path=path,
            )


# ── 实现 2: PyMuPdfRecognizer (无 LLM) ──────────────


class PyMuPdfRecognizer:
    """纯 pymupdf 文本提取，每 N 页一个 batch（不调用 LLM）"""

    async def recognize_in_batches(
        self,
        file_path: Path,
        *,
        batch_pages: int,
        output_dir: Path,
        already_done: list[tuple[int, int]],
        max_retries: int = 3,  # 未使用，签名一致
    ) -> AsyncIterator[BatchResult]:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("pymupdf 未安装: uv pip install pymupdf") from exc

        output_dir.mkdir(parents=True, exist_ok=True)
        done_set = {tuple(p) for p in already_done}

        doc = fitz.open(str(file_path))
        try:
            total = doc.page_count
            page_idx = 0
            while page_idx < total:
                end_excl = min(page_idx + batch_pages, total)
                page_start = page_idx + 1
                page_end = end_excl
                cache_path = output_dir / f"page_{page_start:03d}-{page_end:03d}.md"

                if (page_start, page_end) in done_set or cache_path.exists():
                    md_text = (
                        cache_path.read_text(encoding="utf-8")
                        if cache_path.exists()
                        else ""
                    )
                    if md_text.strip():
                        yield BatchResult(page_start, page_end, md_text, cache_path)
                    page_idx = end_excl
                    continue

                # 拼接文本
                parts: list[str] = []
                for pn in range(page_idx, end_excl):
                    parts.append(doc[pn].get_text("text"))
                md_text = "\n\n".join(parts).strip()
                cache_path.write_text(md_text, encoding="utf-8")

                logger.info(
                    "pymupdf_batch_done",
                    file=file_path.name,
                    pages=f"{page_start}-{page_end}",
                    md_length=len(md_text),
                )
                yield BatchResult(page_start, page_end, md_text, cache_path)
                page_idx = end_excl
        finally:
            doc.close()


# ── 实现 3: MarkdownRecognizer (整文件单 batch) ──────


class MarkdownRecognizer:
    """Markdown / TeX 文件直接读取，单 batch"""

    async def recognize_in_batches(
        self,
        file_path: Path,
        *,
        batch_pages: int,  # 未使用
        output_dir: Path,
        already_done: list[tuple[int, int]],
        max_retries: int = 3,  # 未使用
    ) -> AsyncIterator[BatchResult]:
        output_dir.mkdir(parents=True, exist_ok=True)
        text = file_path.read_text(encoding="utf-8")
        line_count = max(1, text.count("\n") + 1)

        cache_path = output_dir / f"page_001-{line_count:03d}.md"
        if not cache_path.exists():
            cache_path.write_text(text, encoding="utf-8")

        logger.info(
            "markdown_recognize",
            file=file_path.name,
            line_count=line_count,
        )
        yield BatchResult(
            page_start=1,
            page_end=line_count,
            md_text=text,
            md_path=cache_path,
        )


# ── 工厂 ────────────────────────────────────────────


def get_recognizer(
    fmt: DocumentFormat,
    backend: str,
    llm_client: LLMClient | None,
    config: AppConfig,
) -> Recognizer:
    """根据文件格式 + backend 选择 Recognizer

    Args:
        fmt: 文件格式
        backend: 'vlm' / 'pymupdf' / 'auto'
        llm_client: vlm 后端必需
    """
    if fmt in (DocumentFormat.MARKDOWN, DocumentFormat.TEX):
        return MarkdownRecognizer()

    if fmt in (DocumentFormat.PDF, DocumentFormat.DOCX, DocumentFormat.PPTX):
        if backend == "vlm" and llm_client is not None and llm_client.supports_vision:
            return VlmRecognizer(llm_client, config)
        if fmt == DocumentFormat.PDF:
            return PyMuPdfRecognizer()
        raise RuntimeError(
            f"格式 {fmt.value} 在 backend={backend} 下不支持；请使用 vlm 后端"
        )

    raise RuntimeError(f"暂不支持的格式: {fmt.value}")
