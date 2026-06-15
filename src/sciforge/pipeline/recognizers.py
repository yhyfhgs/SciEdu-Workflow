"""Producer (Task0) — 文档识别 Recognizer 协议与具体实现

每个 Recognizer 暴露统一的异步生成器接口 `recognize_in_batches`，
按 N 页一批 yield BatchResult，每个 batch 同时落盘到 page_NNN-NNN.md。

已支持的格式:
- VLM (PDF / DOCX): 多模态 LLM 高精度识别
- PyMuPDF (PDF): 纯文本提取，无 LLM
- Markdown / Tex: 整文件单 batch
"""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import structlog

from sciforge.core.models import Discipline, DocumentFormat

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()

# 结构化边界检测正则（Phase 2：.md/.tex 预分块）
_MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_TEX_SECTION_RE = re.compile(
    r"^\s*\\(chapter|section|subsection|subsubsection|part)\*?\s*\{(.+?)\}",
    re.IGNORECASE,
)
_CN_CHAPTER_RE = re.compile(
    r"^\s*\*\*\s*(第[零〇一二三四五六七八九十百千\d]+[章节部分篇]|§[\d.]+)\s*.+?\*\*\s*$"
)


def detect_section_boundaries(lines: list[str]) -> list[tuple[int, int, str]]:
    """扫描行列表，检测章节/小节/子节边界。

    Args:
        lines: 源文档的行列表（每行含或不含 '\\n' 都可）

    Returns:
        列表，每项 (line_index, level, title)：
        - line_index: 0-based 行号
        - level: 1 (chapter/#) ~ 4 (subsubsection/####)
        - title: 标题文本（已去除标记）
    """
    results: list[tuple[int, int, str]] = []
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n\r")
        # Markdown heading
        m = _MD_HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            results.append((i, level, title))
            continue
        # LaTeX sectioning
        m = _TEX_SECTION_RE.match(line)
        if m:
            tex_cmd = m.group(1).lower()
            level_map = {
                "part": 0,
                "chapter": 1,
                "section": 2,
                "subsection": 3,
                "subsubsection": 4,
            }
            level = level_map.get(tex_cmd, 2)
            title = m.group(2).strip()
            results.append((i, level, title))
            continue
        # 中文粗体章节
        if _CN_CHAPTER_RE.match(line):
            # 中文章节按"章"=1, "节"=2, 其他=3 判层级
            if "章" in line or "篇" in line or "部分" in line:
                level = 1
            elif "节" in line:
                level = 2
            else:
                level = 3
            title = line.strip().strip("*").strip()
            results.append((i, level, title))
    return results


def _has_fitz() -> bool:
    try:
        import fitz  # noqa: F401
    except ImportError:
        return False
    return True


def _has_pdf_image_renderer() -> bool:
    return (
        _has_fitz()
        or shutil.which("pdftocairo") is not None
        or shutil.which("pdftoppm") is not None
    )


def pdf_page_count(file_path: Path) -> int | None:
    """返回 PDF 总页数；优先 fitz，缺失时退回 pdfinfo。"""
    if _has_fitz():
        try:
            import fitz

            with fitz.open(str(file_path)) as doc:
                total = int(doc.page_count)
            return total if total > 0 else None
        except Exception:
            logger.debug("pdf_page_count_fitz_failed", file=str(file_path), exc_info=True)

    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        return None

    try:
        result = subprocess.run(
            [pdfinfo, str(file_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except OSError:
        logger.debug("pdf_page_count_pdfinfo_failed", file=str(file_path), exc_info=True)
        return None

    if result.returncode != 0:
        return None

    for line in result.stdout.splitlines():
        if not line.lower().startswith("pages:"):
            continue
        raw = line.split(":", 1)[1].strip()
        try:
            total = int(raw)
        except ValueError:
            return None
        return total if total > 0 else None
    return None


def pdf_has_extractable_text_layer(
    file_path: Path,
    *,
    min_chars_per_page: int = 100,
    sample_pages: int = 5,
) -> bool:
    """探测 PDF 是否含可用的可提取文字层（REC-01）。

    用 fitz 抽样前 ``sample_pages`` 页 ``get_text``，当平均字符/页 ≥
    ``min_chars_per_page`` 且乱码（U+FFFD 替换符）占比低时判为有文字层。
    用于把电子版教材 PDF 自动降级到 0-LLM 的 PyMuPdfRecognizer，扫描件
    （文字层缺失或纯乱码）则保留 VLM。fitz 不可用或读取失败时返回 False
    （保守：不误判扫描件，继续走 VLM）。
    """
    if not _has_fitz():
        return False
    try:
        import fitz

        with fitz.open(str(file_path)) as doc:
            total = int(doc.page_count)
            if total <= 0:
                return False
            n = min(sample_pages, total)
            total_chars = 0
            garbled = 0
            for pn in range(n):
                text = doc[pn].get_text("text")
                total_chars += len(text)
                garbled += text.count("�")
    except Exception:
        logger.debug("pdf_text_layer_probe_failed", file=str(file_path), exc_info=True)
        return False

    if total_chars <= 0:
        return False
    avg_chars = total_chars / max(1, n)
    garbled_ratio = garbled / total_chars
    return avg_chars >= min_chars_per_page and garbled_ratio < 0.05


# ── 共享数据结构 ───────────────────────────────────────


@dataclass
class BatchResult:
    """单个识别 batch 的产物

    is_failed=True 时表示该 batch 重试耗尽失败（md_text 为空）；Producer 应据此
    记录 state.failed_batches 并标记 producer_had_error，避免误将 task0_completed
    置 True 而走缓存直通模式。
    """

    page_start: int  # 1-based
    page_end: int  # 1-based, inclusive
    md_text: str
    md_path: Path
    is_failed: bool = False


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

    def __init__(
        self,
        llm_client: LLMClient,
        config: AppConfig,
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        from sciforge.pipeline.vlm_converter import VlmConverter

        self._converter = VlmConverter(
            llm_client=llm_client,
            checkpoint_dir=None,  # recognizers 自己管 checkpoint 路径
            output_dir=config.data_dir,
            dpi=config.vlm_dpi,
            max_concurrent=config.vlm_max_concurrent,
            pages_per_batch=config.pages_per_batch,
            discipline=discipline,
            rate_limit_base_sleep=float(getattr(config, "rate_limit_base_sleep", 300.0)),
            rate_limit_max_sleep=float(getattr(config, "rate_limit_max_sleep", 1800.0)),
            rate_limit_total_budget=float(getattr(config, "rate_limit_total_budget", 21600.0)),
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
        async for ps, pe, md, path, is_failed in self._converter.recognize_in_batches(
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
                is_failed=is_failed,
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
                    md_text = cache_path.read_text(encoding="utf-8") if cache_path.exists() else ""
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


class PdfToTextRecognizer:
    """基于 poppler `pdftotext` 的纯文本 PDF 识别器（不依赖 fitz）"""

    def __init__(self, command: str | None = None) -> None:
        resolved_command = command or shutil.which("pdftotext")
        if not resolved_command:
            raise RuntimeError("缺少 PDF 提取能力：既没有 pymupdf(fitz)，也没有系统命令 pdftotext")
        self._command = resolved_command

    def _extract_page_range(self, file_path: Path, page_start: int, page_end: int) -> str:
        result = subprocess.run(
            [
                self._command,
                "-enc",
                "UTF-8",
                "-layout",
                "-nopgbrk",
                "-f",
                str(page_start),
                "-l",
                str(page_end),
                str(file_path),
                "-",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(f"pdftotext 失败: {stderr or 'unknown error'}")
        return result.stdout.strip()

    async def recognize_in_batches(
        self,
        file_path: Path,
        *,
        batch_pages: int,
        output_dir: Path,
        already_done: list[tuple[int, int]],
        max_retries: int = 3,  # 未使用，签名一致
    ) -> AsyncIterator[BatchResult]:
        del max_retries
        total_pages = pdf_page_count(file_path)
        if total_pages is None:
            raise RuntimeError(f"无法确定 PDF 页数: {file_path}")

        output_dir.mkdir(parents=True, exist_ok=True)
        done_set = {tuple(p) for p in already_done}

        page_idx = 0
        while page_idx < total_pages:
            page_start = page_idx + 1
            page_end = min(page_idx + batch_pages, total_pages)
            cache_path = output_dir / f"page_{page_start:03d}-{page_end:03d}.md"

            if (page_start, page_end) in done_set or cache_path.exists():
                md_text = cache_path.read_text(encoding="utf-8") if cache_path.exists() else ""
                if md_text.strip():
                    yield BatchResult(page_start, page_end, md_text, cache_path)
                page_idx = page_end
                continue

            md_text = await asyncio.to_thread(
                self._extract_page_range,
                file_path,
                page_start,
                page_end,
            )
            cache_path.write_text(md_text, encoding="utf-8")

            logger.info(
                "pdftotext_batch_done",
                file=file_path.name,
                pages=f"{page_start}-{page_end}",
                md_length=len(md_text),
            )
            yield BatchResult(page_start, page_end, md_text, cache_path)
            page_idx = page_end


# ── 工厂 ────────────────────────────────────────────


def get_recognizer(
    fmt: DocumentFormat,
    backend: str,
    llm_client: LLMClient | None,
    config: AppConfig,
    discipline: Discipline = Discipline.MATH,
    file_path: Path | None = None,
) -> Recognizer:
    """根据文件格式 + backend 选择 Recognizer

    Args:
        fmt: 文件格式
        backend: 'vlm' / 'pymupdf' / 'auto'
        llm_client: vlm 后端必需
        discipline: 学科（仅 VLM 后端使用，用于参数化 VLM prompt）
        file_path: 待识别文件路径；提供后可对 PDF 探测文字层（REC-01），
            电子版 PDF 自动降级走 0-LLM 的 PyMuPdfRecognizer
    """
    if fmt in (DocumentFormat.MARKDOWN, DocumentFormat.TEX):
        return MarkdownRecognizer()

    if fmt == DocumentFormat.PDF:
        if backend == "vlm" and llm_client is not None and llm_client.supports_vision:
            # REC-01：探测到可提取文字层（电子版教材）→ 自动走 0-LLM 文本提取，
            # VLM 仅保留给扫描件/图表密集件。--force-vlm 关闭探测时跳过本分支。
            if (
                getattr(config, "vlm_auto_text_detect", False)
                and file_path is not None
                and _has_fitz()
                and pdf_has_extractable_text_layer(
                    file_path,
                    min_chars_per_page=getattr(config, "vlm_text_layer_min_chars_per_page", 100),
                    sample_pages=getattr(config, "vlm_text_layer_sample_pages", 5),
                )
            ):
                logger.info(
                    "recognizer_pdf_text_layer_autodowngrade",
                    file=file_path.name,
                    reason="extractable_text_layer_detected",
                    backend_requested=backend,
                    chosen="pymupdf",
                )
                return PyMuPdfRecognizer()
            if not _has_pdf_image_renderer():
                logger.warning(
                    "recognizer_pdf_vlm_fallback",
                    backend=backend,
                    reason="no_pdf_image_renderer",
                    fallback="pdftotext",
                )
                return PdfToTextRecognizer()
            return VlmRecognizer(llm_client, config, discipline=discipline)
        if _has_fitz():
            return PyMuPdfRecognizer()
        logger.warning(
            "recognizer_pdf_text_fallback",
            backend=backend,
            reason="fitz_missing",
            fallback="pdftotext",
        )
        return PdfToTextRecognizer()

    if fmt in (DocumentFormat.DOCX, DocumentFormat.PPTX):
        if backend == "vlm" and llm_client is not None and llm_client.supports_vision:
            if not _has_fitz():
                raise RuntimeError(f"格式 {fmt.value} 的 VLM 识别需要 pymupdf(fitz)")
            return VlmRecognizer(llm_client, config, discipline=discipline)
        raise RuntimeError(f"格式 {fmt.value} 在 backend={backend} 下不支持；请使用 vlm 后端")

    raise RuntimeError(f"暂不支持的格式: {fmt.value}")
