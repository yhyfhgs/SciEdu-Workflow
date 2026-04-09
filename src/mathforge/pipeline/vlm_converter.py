"""VLM (Vision Language Model) 文档转 Markdown 转换器

逐页渲染文档为图像，送入多模态 LLM 识别，输出高精度 Markdown+LaTeX。
支持 PDF 和 DOCX（通过 LibreOffice 转 PDF），带 checkpoint/resume。

图片裁剪：VLM 标注插图百分比坐标 → 从 PDF 页面重新渲染裁剪区域 → 保存到 assets/。
多页批量：可配置每次发送多页图片给 VLM，减少调用次数。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from mathforge.core.exceptions import VlmConversionError
from mathforge.prompts.templates import VLM_CONVERT_PROMPT

if TYPE_CHECKING:
    from mathforge.llm.client import LLMClient

logger = structlog.get_logger()

_CONTEXT_TAIL_LINES = 10

_SYSTEM_PROMPT = (
    "你是一位高精度数学文档转写专家，擅长将文档页面图像精确转换为 Markdown+LaTeX 格式。"
    "你只输出正文内容，自行判断并忽略页眉、页脚、页码、水印等非正文元素。"
    "你特别注意保持原文的段落结构，不会随意合并或拆分段落。"
)

# VLM 输出的图片占位符：![描述]({{FIGURE:top,left,bottom,right}})
_FIGURE_RE = re.compile(r"!\[([^\]]*)\]\(\{\{FIGURE:(\d+),(\d+),(\d+),(\d+)\}\}\)")


class VlmConverter:
    """多模态 LLM 文档转 Markdown 转换器"""

    def __init__(
        self,
        llm_client: LLMClient,
        checkpoint_dir: Path | None = None,
        output_dir: Path | None = None,
        dpi: int = 200,
        max_concurrent: int = 2,
        pages_per_batch: int = 4,
    ) -> None:
        self.llm = llm_client
        self.checkpoint_dir = checkpoint_dir
        self.output_dir = output_dir or Path("data")
        self.dpi = dpi
        self.max_concurrent = max_concurrent
        self.pages_per_batch = pages_per_batch

    async def convert_file_async(self, file_path: Path) -> str:
        """异步转换文件为 Markdown"""
        file_hash = self._compute_file_hash(file_path)
        vlm_dir = self._vlm_dir(file_hash)

        # 检查已完成的缓存
        merged_path = vlm_dir / "merged.md" if vlm_dir else None
        if merged_path and merged_path.exists():
            cached = merged_path.read_text(encoding="utf-8")
            if cached.strip():
                logger.info("vlm_cache_hit", file=file_path.name, hash=file_hash)
                return cached

        # 打开文档（PDF / DOCX→PDF）
        doc, tmp_pdf = self._open_document(file_path)

        try:
            total_pages = doc.page_count
            logger.info(
                "vlm_convert_start",
                file=file_path.name,
                total_pages=total_pages,
                dpi=self.dpi,
                pages_per_batch=self.pages_per_batch,
            )

            assets_dir = self._assets_dir(file_path)
            completed = self._load_completed_pages(vlm_dir) if vlm_dir else {}

            sem = asyncio.Semaphore(self.max_concurrent)
            page_results: dict[int, str] = dict(completed)
            prev_context = ""

            if completed:
                max_done = max(completed.keys())
                prev_context = self._tail_lines(completed[max_done])

            # 按 batch 处理
            batch_start = 0
            # 跳过已完成的连续页
            while batch_start < total_pages and batch_start in page_results:
                prev_context = self._tail_lines(page_results[batch_start])
                batch_start += 1

            while batch_start < total_pages:
                batch_end = min(batch_start + self.pages_per_batch, total_pages)

                # 收集本批次需要处理的页
                batch_page_nums: list[int] = []
                batch_images: list[bytes] = []
                for pn in range(batch_start, batch_end):
                    if pn in page_results:
                        prev_context = self._tail_lines(page_results[pn])
                        continue
                    batch_page_nums.append(pn)
                    page = doc[pn]
                    pix = page.get_pixmap(dpi=self.dpi)
                    batch_images.append(pix.tobytes("png"))

                if not batch_page_nums:
                    batch_start = batch_end
                    continue

                async with sem:
                    raw_md = await self._convert_batch(
                        page_nums=batch_page_nums,
                        images=batch_images,
                        total_pages=total_pages,
                        prev_context=prev_context,
                    )

                # 裁剪插图并替换占位符
                md = self._crop_figures_from_md(
                    raw_md, doc, batch_page_nums, file_path.stem, assets_dir
                )

                # 将整个 batch 结果存入每页（用于 checkpoint 和合并）
                # 整个 batch 产出一个连贯的 md block
                for pn in batch_page_nums:
                    page_results[pn] = md if pn == batch_page_nums[0] else ""

                prev_context = self._tail_lines(md)

                # 保存 checkpoint
                if vlm_dir:
                    self._save_batch_checkpoint(vlm_dir, batch_page_nums, md, total_pages)

                logger.debug(
                    "vlm_batch_done",
                    file=file_path.name,
                    pages=f"{batch_page_nums[0] + 1}-{batch_page_nums[-1] + 1}",
                    total=total_pages,
                    md_length=len(md),
                )

                batch_start = batch_end

        finally:
            doc.close()
            if tmp_pdf:
                tmp_pdf.cleanup()

        # 合并各页结果
        merged = self._merge_results(page_results, total_pages)

        if vlm_dir:
            vlm_dir.mkdir(parents=True, exist_ok=True)
            (vlm_dir / "merged.md").write_text(merged, encoding="utf-8")

        logger.info(
            "vlm_convert_done",
            file=file_path.name,
            total_pages=total_pages,
            md_length=len(merged),
        )
        return merged

    def convert_file(self, file_path: Path) -> str:
        """同步入口"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, self.convert_file_async(file_path)).result()
        else:
            return asyncio.run(self.convert_file_async(file_path))

    async def convert_file_streaming(
        self, file_path: Path
    ) -> AsyncGenerator[str, None]:
        """流式转换：每完成一个 batch 即 yield 对应 Markdown 文本。

        与 convert_file_async 共享全部内部方法，但不等所有页面完成才返回。
        用于 Task0[VlmStream] → StreamingLineBuffer → Task1[BlockSplitter] 管线。
        """
        file_hash = self._compute_file_hash(file_path)
        vlm_dir = self._vlm_dir(file_hash)

        # 检查已完成的缓存
        merged_path = vlm_dir / "merged.md" if vlm_dir else None
        if merged_path and merged_path.exists():
            cached = merged_path.read_text(encoding="utf-8")
            if cached.strip():
                logger.info("vlm_cache_hit", file=file_path.name, hash=file_hash)
                yield cached
                return

        doc, tmp_pdf = self._open_document(file_path)
        prev_md_tail = ""
        all_page_results: dict[int, str] = {}

        try:
            total_pages = doc.page_count
            logger.info(
                "vlm_stream_start",
                file=file_path.name,
                total_pages=total_pages,
                pages_per_batch=self.pages_per_batch,
            )

            assets_dir = self._assets_dir(file_path)
            completed = self._load_completed_pages(vlm_dir) if vlm_dir else {}
            all_page_results.update(completed)

            sem = asyncio.Semaphore(self.max_concurrent)

            if completed:
                max_done = max(completed.keys())
                prev_md_tail = completed[max_done]

            batch_start = 0
            while batch_start < total_pages and batch_start in all_page_results:
                # 已完成的 batch：yield 缓存内容
                cached_md = all_page_results[batch_start]
                if cached_md.strip():
                    yield cached_md
                    prev_md_tail = cached_md
                batch_start += 1

            while batch_start < total_pages:
                batch_end = min(batch_start + self.pages_per_batch, total_pages)

                batch_page_nums: list[int] = []
                batch_images: list[bytes] = []
                for pn in range(batch_start, batch_end):
                    if pn in all_page_results:
                        # 已有该页结果（checkpoint 恢复）
                        cached_md = all_page_results[pn]
                        if cached_md.strip():
                            yield cached_md
                            prev_md_tail = cached_md
                        continue
                    batch_page_nums.append(pn)
                    page = doc[pn]
                    pix = page.get_pixmap(dpi=self.dpi)
                    batch_images.append(pix.tobytes("png"))

                if not batch_page_nums:
                    batch_start = batch_end
                    continue

                async with sem:
                    raw_md = await self._convert_batch(
                        page_nums=batch_page_nums,
                        images=batch_images,
                        total_pages=total_pages,
                        prev_context=self._tail_lines(prev_md_tail),
                    )

                md = self._crop_figures_from_md(
                    raw_md, doc, batch_page_nums, file_path.stem, assets_dir
                )

                # 去重边界
                if prev_md_tail.strip():
                    md = self._deduplicate_boundary(prev_md_tail, md)

                for pn in batch_page_nums:
                    all_page_results[pn] = md if pn == batch_page_nums[0] else ""

                if vlm_dir:
                    self._save_batch_checkpoint(vlm_dir, batch_page_nums, md, total_pages)

                logger.debug(
                    "vlm_stream_batch_done",
                    file=file_path.name,
                    pages=f"{batch_page_nums[0] + 1}-{batch_page_nums[-1] + 1}",
                    total=total_pages,
                    md_length=len(md),
                )

                prev_md_tail = md
                yield md

                batch_start = batch_end

        finally:
            _total = doc.page_count
            doc.close()
            if tmp_pdf:
                tmp_pdf.cleanup()

        # 写入 merged.md 缓存（流结束后）
        if vlm_dir:
            merged = self._merge_results(all_page_results, _total)
            vlm_dir.mkdir(parents=True, exist_ok=True)
            (vlm_dir / "merged.md").write_text(merged, encoding="utf-8")

        logger.info(
            "vlm_stream_done",
            file=file_path.name,
            total_pages=_total,
        )

    # ── 文档打开 ──────────────────────────────────────────

    def _open_document(self, file_path: Path):
        """打开 PDF 或 DOCX（转为 PDF），返回 (fitz.Document, tmp_dir_or_None)"""
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
        prompt = VLM_CONVERT_PROMPT.format(
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
                system=_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=16384,
            )

        # 多图批量调用，通过 LLMClient 标准接口
        return await self.llm.generate_with_images(
            prompt=prompt,
            images=images,
            image_media_type="image/png",
            system=_SYSTEM_PROMPT,
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
        doc,
        page_nums: list[int],
        file_stem: str,
        assets_dir: Path,
    ) -> str:
        """解析 FIGURE 坐标，从 PDF page 重新渲染裁剪区域并替换路径。

        页面归属策略：按 FIGURE 在 md 文本中的位置比例，映射到 batch 内的
        对应页面。单页 batch 时所有 FIGURE 都属于该页。
        """
        import fitz

        matches = list(_FIGURE_RE.finditer(md))
        if not matches:
            return md

        md_len = len(md) or 1
        n_pages = len(page_nums)

        # 正序处理，收集 (match, replacement) 对
        replacements: list[tuple[re.Match, str]] = []  # type: ignore[type-arg]
        fig_idx = 0

        for match in matches:
            alt = match.group(1)
            top_pct = max(0, min(100, int(match.group(2))))
            left_pct = max(0, min(100, int(match.group(3))))
            bottom_pct = max(0, min(100, int(match.group(4))))
            right_pct = max(0, min(100, int(match.group(5))))

            # 坐标合理性校验
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

    # ── 结果合并 ─────────────────────────────────────────

    def _merge_results(self, page_results: dict[int, str], total_pages: int) -> str:
        """合并各 batch 的 MD 结果"""
        parts: list[str] = []

        for i in range(total_pages):
            md = page_results.get(i, "")
            if not md.strip():
                continue

            if parts:
                md = self._deduplicate_boundary(parts[-1], md)
                parts.append(f"\n\n{md}")
            else:
                parts.append(md)

        return "".join(parts).strip() + "\n"

    @staticmethod
    def _deduplicate_boundary(prev_md: str, curr_md: str, overlap_lines: int = 3) -> str:
        prev_lines = prev_md.strip().splitlines()
        curr_lines = curr_md.strip().splitlines()

        if len(prev_lines) < overlap_lines or len(curr_lines) < overlap_lines:
            return curr_md

        prev_tail = [ln.strip() for ln in prev_lines[-overlap_lines:]]

        best_skip = 0
        for start in range(min(overlap_lines + 2, len(curr_lines))):
            curr_head = [ln.strip() for ln in curr_lines[start : start + overlap_lines]]
            if curr_head == prev_tail:
                best_skip = start + overlap_lines
                break

        return "\n".join(curr_lines[best_skip:]) if best_skip > 0 else curr_md

    # ── Checkpoint ────────────────────────────────────────

    def _vlm_dir(self, file_hash: str) -> Path | None:
        if not self.checkpoint_dir:
            return None
        return self.checkpoint_dir / file_hash / "vlm"

    def _load_completed_pages(self, vlm_dir: Path | None) -> dict[int, str]:
        if not vlm_dir or not vlm_dir.exists():
            return {}

        progress_file = vlm_dir / "progress.json"
        if not progress_file.exists():
            return {}

        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

        completed_pages: list[int] = progress.get("completed_pages", [])
        if not completed_pages:
            return {}

        # 按 batch 首页分组
        batches: dict[int, list[int]] = {}
        sorted_pages = sorted(completed_pages)
        batch_first = sorted_pages[0]
        batch_list = [batch_first]
        for pn in sorted_pages[1:]:
            if pn == batch_list[-1] + 1:
                batch_list.append(pn)
            else:
                batches[batch_list[0]] = batch_list
                batch_list = [pn]
        batches[batch_list[0]] = batch_list

        completed: dict[int, str] = {}
        for first, pages in batches.items():
            last = pages[-1]
            # 新格式：page_000-004.md
            batch_file = vlm_dir / f"page_{first:03d}-{last:03d}.md"
            if batch_file.exists():
                md = batch_file.read_text(encoding="utf-8")
                completed[first] = md
                for pn in pages[1:]:
                    completed[pn] = ""
                continue
            # 兼容旧格式：page_000.md
            old_file = vlm_dir / f"page_{first:03d}.md"
            if old_file.exists():
                completed[first] = old_file.read_text(encoding="utf-8")
                for pn in pages[1:]:
                    old_pn = vlm_dir / f"page_{pn:03d}.md"
                    completed[pn] = old_pn.read_text(encoding="utf-8") if old_pn.exists() else ""

        if completed:
            logger.info(
                "vlm_checkpoint_loaded",
                completed_pages=len(completed),
                total_pages=progress.get("total_pages", "?"),
            )
        return completed

    def _save_batch_checkpoint(
        self,
        vlm_dir: Path,
        page_nums: list[int],
        md: str,
        total_pages: int,
    ) -> None:
        """保存 batch 的 checkpoint

        文件名格式：page_000-004.md（表示第 0-4 页的批次结果）
        """
        vlm_dir.mkdir(parents=True, exist_ok=True)

        # 整个 batch 的 MD 存入一个以范围命名的文件
        first = page_nums[0]
        last = page_nums[-1]
        (vlm_dir / f"page_{first:03d}-{last:03d}.md").write_text(md, encoding="utf-8")

        progress_file = vlm_dir / "progress.json"
        try:
            progress = json.loads(progress_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            progress = {"total_pages": total_pages, "completed_pages": []}

        for pn in page_nums:
            if pn not in progress["completed_pages"]:
                progress["completed_pages"].append(pn)
        progress["completed_pages"].sort()
        progress["total_pages"] = total_pages

        progress_file.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
        )

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
