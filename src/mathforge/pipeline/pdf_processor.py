"""PDF 处理器 — 支持超长文档分批处理和跨页检测"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import structlog

from mathforge.core.exceptions import PdfParseError
from mathforge.core.models import (
    BatchProgress,
    DocumentSkeleton,
    MdBlock,
    TocEntry,
)
from mathforge.pipeline.cross_page_detector import CrossPageDetector

logger = structlog.get_logger()

# 文档类型的批处理配置
BATCH_CONFIG: dict[str, dict[str, int]] = {
    "textbook": {"batch_size": 20, "overlap": 3},
    "problem_book": {"batch_size": 30, "overlap": 2},
    "slides": {"batch_size": 50, "overlap": 0},
}


def _compute_pdf_hash(pdf_path: Path) -> str:
    """计算 PDF 文件哈希（前 1MB + 文件大小）"""
    h = hashlib.sha256()
    size = pdf_path.stat().st_size
    h.update(str(size).encode())
    with open(pdf_path, "rb") as f:
        h.update(f.read(1024 * 1024))
    return h.hexdigest()[:16]


def _detect_doc_type(pdf_path: Path) -> str:
    """从文件名推断文档类型"""
    name = pdf_path.name.lower()
    if "ppt" in name or "slides" in str(pdf_path).lower():
        return "slides"
    if "题" in name or "problem" in name:
        return "problem_book"
    return "textbook"


class PdfProcessor:
    """三阶段 PDF 处理器"""

    def __init__(
        self,
        checkpoint_dir: Path | None = None,
        max_span_pages: int = 5,
    ):
        self.checkpoint_dir = checkpoint_dir or Path("checkpoint")
        self.max_span_pages = max_span_pages

    def process_pdf(self, pdf_path: Path, backend: str = "auto") -> list[MdBlock]:
        """处理单个 PDF 文件"""
        if not pdf_path.exists():
            raise PdfParseError(str(pdf_path), detail="文件不存在")

        try:
            import fitz  # pymupdf
        except ImportError:
            raise PdfParseError(
                str(pdf_path),
                detail="需要安装 pymupdf: uv pip install pymupdf4llm",
            )

        pdf_hash = _compute_pdf_hash(pdf_path)
        doc_type = _detect_doc_type(pdf_path)
        config = BATCH_CONFIG[doc_type]

        # Phase A: 结构分析
        skeleton = self._analyze_structure(pdf_path, fitz)
        is_scanned = skeleton.page_types.get(-1) == "scanned"

        logger.info(
            "pdf_structure_analyzed",
            file=pdf_path.name,
            pages=skeleton.total_pages,
            toc_entries=len(skeleton.toc_entries),
            doc_type=doc_type,
            has_text=not is_scanned,
        )

        if is_scanned:
            logger.warning(
                "pdf_scanned_detected",
                file=pdf_path.name,
                message="扫描 PDF 无文本层，使用 pymupdf4llm Markdown 模式提取",
            )

        # Phase B: 分批提取
        progress = BatchProgress(
            pdf_hash=pdf_hash,
            pdf_path=str(pdf_path),
            total_pages=skeleton.total_pages,
            phase=2,
        )

        # 检查断点
        checkpoint_path = self.checkpoint_dir / pdf_hash
        progress_file = checkpoint_path / "progress.json"
        start_batch = 0
        if progress_file.exists():
            saved = json.loads(progress_file.read_text())
            start_batch = saved.get("batches_completed", 0)
            logger.info("pdf_resume_from_checkpoint", batch=start_batch)

        batch_size = config["batch_size"]
        overlap = config["overlap"]
        total_pages = skeleton.total_pages
        step = max(batch_size - overlap, 1)

        batches: list[tuple[int, int]] = []
        start = 0
        while start < total_pages:
            end = min(start + batch_size, total_pages)
            batches.append((start, end))
            start += step

        progress.batches_total = len(batches)

        all_blocks: list[MdBlock] = []
        detector = CrossPageDetector(self.max_span_pages)
        seen_hashes: set[str] = set()

        doc = fitz.open(str(pdf_path))
        try:
            for batch_idx, (page_start, page_end) in enumerate(batches):
                if batch_idx < start_batch:
                    continue

                # 检查缓存
                batch_file = checkpoint_path / f"batch_{batch_idx:03d}.json"
                if batch_file.exists():
                    cached = json.loads(batch_file.read_text())
                    batch_blocks = [MdBlock.model_validate(b) for b in cached]
                elif is_scanned:
                    # 扫描 PDF: 使用 pymupdf4llm 的 markdown 模式
                    batch_blocks = self._extract_batch_markdown(pdf_path, page_start, page_end)
                else:
                    batch_blocks = self._extract_batch(doc, page_start, page_end, detector, backend)
                    # 保存 checkpoint
                    checkpoint_path.mkdir(parents=True, exist_ok=True)
                    batch_file.write_text(
                        json.dumps([b.model_dump() for b in batch_blocks], ensure_ascii=False),
                        encoding="utf-8",
                    )

                # 去重
                for block in batch_blocks:
                    content_hash = hashlib.md5(block.content.encode()).hexdigest()
                    if content_hash not in seen_hashes:
                        seen_hashes.add(content_hash)
                        block.source_file = str(pdf_path)
                        all_blocks.append(block)

                progress.batches_completed = batch_idx + 1
                progress.blocks_extracted = len(all_blocks)

                # 保存进度
                progress_file.parent.mkdir(parents=True, exist_ok=True)
                progress_file.write_text(progress.model_dump_json(indent=2), encoding="utf-8")

                logger.info(
                    "pdf_batch_complete",
                    batch=f"{batch_idx + 1}/{len(batches)}",
                    pages=f"{page_start + 1}-{page_end}",
                    blocks=len(batch_blocks),
                )
        finally:
            doc.close()

        # Phase C: 刷出剩余块
        remaining = detector.flush()
        for block in remaining:
            content_hash = hashlib.md5(block.content.encode()).hexdigest()
            if content_hash not in seen_hashes:
                seen_hashes.add(content_hash)
                block.source_file = str(pdf_path)
                all_blocks.append(block)

        logger.info(
            "pdf_processing_complete",
            file=pdf_path.name,
            total_blocks=len(all_blocks),
            total_pages=skeleton.total_pages,
        )

        return all_blocks

    def _analyze_structure(self, pdf_path: Path, fitz_module: object) -> DocumentSkeleton:
        """Phase A: 提取文档结构，并检测是否有文本层"""
        fitz = fitz_module  # type: ignore
        doc = fitz.open(str(pdf_path))
        try:
            total_pages = doc.page_count

            # 提取 PDF outline (TOC)
            toc_entries: list[TocEntry] = []
            toc = doc.get_toc()
            for level, title, page in toc:
                toc_entries.append(
                    TocEntry(
                        title=title,
                        level=level,
                        page=page,
                    )
                )

            # 检测是否有文本层（采样几页）
            has_text = False
            sample_pages = [min(5, total_pages - 1), min(15, total_pages - 1), total_pages // 2]
            for p in sample_pages:
                if len(doc[p].get_text().strip()) > 20:
                    has_text = True
                    break

            skeleton = DocumentSkeleton(
                total_pages=total_pages,
                toc_entries=toc_entries,
            )
            # 存到 page_types 里标记是否有文本层
            skeleton.page_types = {-1: "has_text" if has_text else "scanned"}
            return skeleton
        finally:
            doc.close()

    def _extract_batch(
        self,
        doc: object,
        page_start: int,
        page_end: int,
        detector: CrossPageDetector,
        backend: str,
    ) -> list[MdBlock]:
        """Phase B: 提取一批页面"""
        blocks: list[MdBlock] = []

        for page_num in range(page_start, page_end):
            page = doc[page_num]  # type: ignore
            text = page.get_text()  # type: ignore

            # 通过跨页检测器处理
            page_blocks = detector.process_page(text, page_num)
            blocks.extend(page_blocks)

        return blocks

    def _extract_batch_markdown(
        self,
        pdf_path: Path,
        page_start: int,
        page_end: int,
    ) -> list[MdBlock]:
        """Phase B (替代): 使用 pymupdf4llm 整批转 Markdown 再用 MD 解析器处理"""
        import pymupdf4llm

        from mathforge.pipeline.md_parser import parse_file as parse_md_content

        pages = list(range(page_start, page_end))
        md_text = pymupdf4llm.to_markdown(str(pdf_path), pages=pages)

        if not md_text or len(md_text.strip()) < 20:
            return []

        # 用 MD 解析器处理生成的 Markdown
        blocks = parse_md_content(pdf_path, content=md_text, source_type="textbook")

        # 标记页码范围
        for block in blocks:
            block.page_start = page_start
            block.page_end = page_end

        return blocks

    def process_directory(self, dirpath: Path, backend: str = "auto") -> list[MdBlock]:
        """处理目录下所有 PDF 文件"""
        all_blocks: list[MdBlock] = []
        for pdf_file in sorted(dirpath.rglob("*.pdf")):
            try:
                blocks = self.process_pdf(pdf_file, backend)
                all_blocks.extend(blocks)
            except PdfParseError as e:
                logger.error("pdf_process_error", file=pdf_file.name, error=str(e))
            except Exception as e:
                logger.error("pdf_unexpected_error", file=pdf_file.name, error=str(e))
        return all_blocks
