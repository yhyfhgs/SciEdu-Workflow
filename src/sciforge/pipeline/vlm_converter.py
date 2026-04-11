"""VLM (Vision Language Model) 文档转 Markdown 转换器

逐页渲染文档为图像，送入多模态 LLM 识别，输出高精度 Markdown+LaTeX。
支持 PDF 和 DOCX（通过 LibreOffice 转 PDF）。

核心接口: `recognize_in_batches` — 异步生成器，每 N 页为一个 batch yield
`(page_start, page_end, md_text, md_path)` 给上游 Producer 使用。

图片裁剪: VLM 按 `{{FIGURE:top,left,bottom,right}}` 的百分比坐标标注插图，
本模块从原 PDF 页面用 fitz 重新渲染裁剪区域并保存到 assets/，最后把占位符
替换成 `![alt](rel_path)` 的 Markdown 图片语法。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import subprocess
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from sciforge.core.exceptions import VlmConversionError
from sciforge.core.models import Discipline
from sciforge.disciplines import DISCIPLINE_REGISTRY
from sciforge.prompts.templates import get_vlm_prompt_template

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient

logger = structlog.get_logger()

_CONTEXT_TAIL_LINES = 10

# System prompt 模板（{discipline_vlm_expert} 由 VlmConverter.__init__ 注入）
_SYSTEM_PROMPT_TEMPLATE = (
    "你是一位{discipline_vlm_expert}，擅长将文档页面图像精确转换为 Markdown+LaTeX 格式。"
    "你只输出正文内容，自行判断并忽略页眉、页脚、页码、水印等非正文元素。"
    "你特别注意保持原文的段落结构，不会随意合并或拆分段落。"
)

# VLM 输出的图片占位符：![描述]({{FIGURE:top,left,bottom,right}})
_FIGURE_RE = re.compile(r"!\[([^\]]*)\]\(\{\{FIGURE:(\d+),(\d+),(\d+),(\d+)\}\}\)")


class VlmConverter:
    """多模态 LLM 文档转 Markdown 转换器（仅保留 recognize_in_batches 入口）"""

    def __init__(
        self,
        llm_client: LLMClient,
        checkpoint_dir: Path | None = None,
        output_dir: Path | None = None,
        dpi: int = 200,
        max_concurrent: int = 2,
        pages_per_batch: int = 4,
        discipline: Discipline = Discipline.MATH,
    ) -> None:
        self.llm = llm_client
        self.checkpoint_dir = checkpoint_dir
        self.output_dir = output_dir or Path("data")
        self.dpi = dpi
        self.max_concurrent = max_concurrent
        self.pages_per_batch = pages_per_batch
        self.discipline = discipline
        # 按 discipline 构造实例级 system prompt + user prompt 模板
        spec = DISCIPLINE_REGISTRY.get(discipline)
        self._system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace(
            "{discipline_vlm_expert}", spec.prompt_vars["discipline_vlm_expert"]
        )
        self._prompt_template = get_vlm_prompt_template(discipline)

    # ── 文档打开 ──────────────────────────────────────────

    def _open_document(
        self, file_path: Path
    ) -> tuple[Any, tempfile.TemporaryDirectory[str] | None]:
        """打开 PDF 或 DOCX（自动转为 PDF）

        Returns:
            (fitz.Document, tmp_dir_or_None) — tmp_dir 仅 DOCX 路径非空
        """
        import fitz

        suffix = file_path.suffix.lower()
        if suffix == ".pdf":
            return fitz.open(str(file_path)), None

        if suffix == ".docx":
            soffice = shutil.which("soffice")
            if not soffice:
                raise VlmConversionError("VLM 转换 DOCX 需要 LibreOffice")

            tmp = tempfile.TemporaryDirectory()
            result = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    tmp.name,
                    str(file_path),
                ],
                capture_output=True,
                timeout=120,
            )
            if result.returncode != 0:
                tmp.cleanup()
                raise VlmConversionError(
                    f"LibreOffice 转换失败: {result.stderr.decode(errors='replace')}"
                )
            pdf_files = list(Path(tmp.name).glob("*.pdf"))
            if not pdf_files:
                tmp.cleanup()
                raise VlmConversionError("LibreOffice 未生成 PDF")
            return fitz.open(str(pdf_files[0])), tmp

        raise VlmConversionError(f"不支持的格式: {suffix}")

    # ── 多页批量 VLM 调用 ─────────────────────────────────

    async def _convert_batch(
        self,
        page_nums: list[int],
        images: list[bytes],
        total_pages: int,
        prev_context: str,
    ) -> str:
        """将多页图像一次发送给 VLM，返回合并后的 Markdown"""
        context_block = ""
        if prev_context:
            context_block = f"前页结尾内容：\n```\n{prev_context}\n```"

        page_range = (
            f"第 {page_nums[0] + 1} 页"
            if len(page_nums) == 1
            else f"第 {page_nums[0] + 1}-{page_nums[-1] + 1} 页"
        )

        safe_context = context_block.replace("{", "{{").replace("}", "}}")
        prompt = self._prompt_template.format(
            page_number=page_range,
            total_pages=total_pages,
            previous_context=safe_context,
            figure_hint="",
        )

        if len(images) == 1:
            return await self.llm.generate_with_image(
                prompt=prompt,
                image_data=images[0],
                image_media_type="image/png",
                system=self._system_prompt,
                temperature=0.0,
                max_tokens=16384,
            )

        return await self.llm.generate_with_images(
            prompt=prompt,
            images=images,
            image_media_type="image/png",
            system=self._system_prompt,
            temperature=0.0,
            max_tokens=16384,
        )

    # ── 插图裁剪 ─────────────────────────────────────────

    def _assets_dir(self, file_path: Path) -> Path:
        stem = file_path.stem
        assets = self.output_dir / "assets" / stem
        assets.mkdir(parents=True, exist_ok=True)
        return assets

    def _crop_figures_from_md(
        self,
        md: str,
        doc: Any,
        page_nums: list[int],
        file_stem: str,
        assets_dir: Path,
    ) -> str:
        """解析 FIGURE 坐标，从 PDF page 重新渲染裁剪区域并替换路径

        页面归属策略：按 FIGURE 在 md 文本中的位置比例，映射到 batch 内的
        对应页面。单页 batch 时所有 FIGURE 都属于该页。
        """
        import fitz

        matches = list(_FIGURE_RE.finditer(md))
        if not matches:
            return md

        md_len = len(md) or 1
        n_pages = len(page_nums)

        replacements: list[tuple[re.Match[str], str]] = []
        fig_idx = 0

        for match in matches:
            alt = match.group(1)
            top_pct = max(0, min(100, int(match.group(2))))
            left_pct = max(0, min(100, int(match.group(3))))
            bottom_pct = max(0, min(100, int(match.group(4))))
            right_pct = max(0, min(100, int(match.group(5))))

            if top_pct >= bottom_pct or left_pct >= right_pct:
                replacements.append((match, f"[图：{alt}]"))
                continue

            # 按文本位置比例推断 FIGURE 所属页面
            text_pos_ratio = match.start() / md_len
            page_idx = min(int(text_pos_ratio * n_pages), n_pages - 1)
            target_page = page_nums[page_idx]

            page = doc[target_page]
            page_rect = page.rect

            x0 = page_rect.x0 + page_rect.width * left_pct / 100
            y0 = page_rect.y0 + page_rect.height * top_pct / 100
            x1 = page_rect.x0 + page_rect.width * right_pct / 100
            y1 = page_rect.y0 + page_rect.height * bottom_pct / 100

            clip = fitz.Rect(x0, y0, x1, y1)
            clip = clip & page_rect
            if clip.is_empty:
                replacements.append((match, f"[图：{alt}]"))
                continue

            clip_w = clip.width * self.dpi / 72
            clip_h = clip.height * self.dpi / 72
            if clip_w < 20 or clip_h < 20:
                replacement = f"[图：{alt}]"
            else:
                fig_idx += 1
                try:
                    pix = page.get_pixmap(dpi=self.dpi, clip=clip)
                    filename = f"{file_stem}_p{target_page + 1:03d}_fig{fig_idx}.png"
                    fig_path = assets_dir / filename
                    pix.save(str(fig_path))

                    rel_path = f"assets/{assets_dir.name}/{filename}"
                    replacement = f"![{alt}]({rel_path})"

                    logger.debug(
                        "vlm_figure_cropped",
                        page=target_page + 1,
                        fig=fig_idx,
                        bbox_pct=f"({top_pct},{left_pct},{bottom_pct},{right_pct})",
                        file=filename,
                    )
                except Exception as exc:
                    logger.warning(
                        "vlm_figure_crop_failed",
                        page=target_page + 1,
                        bbox_pct=f"({top_pct},{left_pct},{bottom_pct},{right_pct})",
                        error=str(exc),
                    )
                    replacement = f"[图：{alt}]"

            replacements.append((match, replacement))

        # 倒序替换避免偏移
        result = md
        for match, replacement in reversed(replacements):
            result = result[: match.start()] + replacement + result[match.end() :]

        return result

    # ── 工具 ─────────────────────────────────────────────

    @staticmethod
    def _compute_file_hash(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()[:16]

    @staticmethod
    def _tail_lines(text: str, n: int = _CONTEXT_TAIL_LINES) -> str:
        lines = text.strip().splitlines()
        return "\n".join(lines[-n:]) if lines else ""

    # ── 主入口: recognize_in_batches ─────────────────────

    async def recognize_in_batches(
        self,
        file_path: Path,
        *,
        batch_pages: int,
        output_dir: Path,
        already_done: list[tuple[int, int]],
        max_retries: int = 3,
    ) -> AsyncGenerator[tuple[int, int, str, Path], None]:
        """把文档按 batch_pages 一批识别为 markdown 并 yield

        Args:
            file_path: 源文件 (PDF / DOCX, DOCX 自动转 PDF)
            batch_pages: 每批多少页 (默认 5)
            output_dir: 该文件的 batch 落盘目录，文件名 page_NNN-NNN.md (1-based)
            already_done: 已完成的 (page_start, page_end) 1-based 元组列表，跳过这些 batch
            max_retries: 单 batch 调用 LLM 失败时的重试次数

        Yields:
            (page_start_1based, page_end_1based, md_text, md_path)
        """
        from sciforge.pipeline.retry import RetryExhausted, with_retry

        output_dir.mkdir(parents=True, exist_ok=True)
        done_set = {tuple(p) for p in already_done}

        doc, tmp_pdf = self._open_document(file_path)
        prev_md_tail = ""

        try:
            total_pages = doc.page_count
            assets_dir = self._assets_dir(file_path)

            logger.info(
                "recognize_start",
                file=file_path.name,
                total_pages=total_pages,
                batch_pages=batch_pages,
            )

            page_idx = 0
            while page_idx < total_pages:
                batch_end_excl = min(page_idx + batch_pages, total_pages)
                page_start_1based = page_idx + 1
                page_end_1based = batch_end_excl

                # checkpoint 快速路径
                cache_path = output_dir / f"page_{page_start_1based:03d}-{page_end_1based:03d}.md"
                if (page_start_1based, page_end_1based) in done_set or cache_path.exists():
                    md_text = cache_path.read_text(encoding="utf-8") if cache_path.exists() else ""
                    if md_text.strip():
                        logger.info(
                            "recognize_cached",
                            file=file_path.name,
                            pages=f"{page_start_1based}-{page_end_1based}",
                        )
                        prev_md_tail = self._tail_lines(md_text)
                        yield (page_start_1based, page_end_1based, md_text, cache_path)
                    page_idx = batch_end_excl
                    continue

                # 收集本批次图像 — get_pixmap 放后台线程避免阻塞主协程
                batch_page_nums_0based: list[int] = list(range(page_idx, batch_end_excl))
                batch_images: list[bytes] = await asyncio.to_thread(
                    self._render_batch_images, doc, batch_page_nums_0based
                )

                # 调用 VLM (含 retry)
                async def _call() -> str:
                    return await self._convert_batch(
                        page_nums=batch_page_nums_0based,
                        images=batch_images,
                        total_pages=total_pages,
                        prev_context=prev_md_tail,
                    )

                try:
                    raw_md = await with_retry(
                        _call,
                        max_attempts=max_retries,
                        base_delay=2.0,
                        on_retry=lambda i, e: logger.warning(
                            "vlm_batch_retry",
                            file=file_path.name,
                            pages=f"{page_start_1based}-{page_end_1based}",
                            attempt=i,
                            error=str(e)[:200],
                        ),
                    )
                except RetryExhausted as exc:
                    logger.error(
                        "vlm_batch_failed",
                        file=file_path.name,
                        pages=f"{page_start_1based}-{page_end_1based}",
                        attempts=exc.attempts,
                    )
                    page_idx = batch_end_excl
                    continue

                md_text = self._crop_figures_from_md(
                    raw_md, doc, batch_page_nums_0based, file_path.stem, assets_dir
                )

                cache_path.write_text(md_text, encoding="utf-8")
                prev_md_tail = self._tail_lines(md_text)

                logger.info(
                    "recognize_batch_done",
                    file=file_path.name,
                    pages=f"{page_start_1based}-{page_end_1based}",
                    total=total_pages,
                    md_length=len(md_text),
                )

                yield (page_start_1based, page_end_1based, md_text, cache_path)
                page_idx = batch_end_excl

        finally:
            doc.close()
            if tmp_pdf:
                tmp_pdf.cleanup()

    def _render_batch_images(self, doc: Any, page_nums_0based: list[int]) -> list[bytes]:
        """把一批页面渲染为 PNG bytes，供 asyncio.to_thread 调用"""
        images: list[bytes] = []
        for pn in page_nums_0based:
            page = doc[pn]
            pix = page.get_pixmap(dpi=self.dpi)
            images.append(pix.tobytes("png"))
        return images
