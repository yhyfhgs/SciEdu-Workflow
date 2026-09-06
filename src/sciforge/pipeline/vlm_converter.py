"""VLM (Vision Language Model) 文档转 Markdown 转换器

逐页渲染文档为图像，送入多模态 LLM 识别，输出高精度 Markdown+LaTeX。
支持 PDF / DOCX / PPTX（Office 文档通过 LibreOffice 转 PDF）。

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
from dataclasses import dataclass
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


@dataclass
class _OpenedDocument:
    pdf_path: Path
    page_count: int
    fitz_doc: Any | None = None
    tmp_dir: tempfile.TemporaryDirectory[str] | None = None

    def close(self) -> None:
        if self.fitz_doc is not None:
            self.fitz_doc.close()
        if self.tmp_dir is not None:
            self.tmp_dir.cleanup()


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
        rate_limit_base_sleep: float = 300.0,
        rate_limit_max_sleep: float = 1800.0,
        rate_limit_total_budget: float = 21600.0,
    ) -> None:
        self.llm = llm_client
        self.checkpoint_dir = checkpoint_dir
        self.output_dir = output_dir or Path("data")
        self.dpi = dpi
        self.max_concurrent = max_concurrent
        self.pages_per_batch = pages_per_batch
        self.discipline = discipline
        self.rate_limit_base_sleep = rate_limit_base_sleep
        self.rate_limit_max_sleep = rate_limit_max_sleep
        self.rate_limit_total_budget = rate_limit_total_budget
        # 按 discipline 构造实例级 system prompt + user prompt 模板
        spec = DISCIPLINE_REGISTRY.get(discipline)
        self._system_prompt = _SYSTEM_PROMPT_TEMPLATE.replace(
            "{discipline_vlm_expert}", spec.prompt_vars["discipline_vlm_expert"]
        )
        self._prompt_template = get_vlm_prompt_template(discipline)

    # ── 文档打开 ──────────────────────────────────────────

    @staticmethod
    def _has_fitz() -> bool:
        try:
            import fitz  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _pdf_page_count(pdf_path: Path) -> int:
        pdfinfo = shutil.which("pdfinfo")
        if not pdfinfo:
            raise VlmConversionError("缺少 pdfinfo，无法读取 PDF 页数")
        result = subprocess.run(
            [pdfinfo, str(pdf_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise VlmConversionError(
                f"pdfinfo 读取页数失败: {result.stderr or result.stdout or 'unknown error'}"
            )
        for line in result.stdout.splitlines():
            if line.lower().startswith("pages:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    total = int(raw)
                except ValueError as exc:
                    raise VlmConversionError(f"pdfinfo 返回非法页数: {raw}") from exc
                if total > 0:
                    return total
        raise VlmConversionError("pdfinfo 未返回 Pages 字段")

    def _open_document(self, file_path: Path) -> _OpenedDocument:
        """打开 PDF / Office 文档（自动转为 PDF）"""
        suffix = file_path.suffix.lower()
        tmp: tempfile.TemporaryDirectory[str] | None = None
        if suffix == ".pdf":
            pdf_path = file_path
        elif suffix in {".docx", ".pptx"}:
            soffice = shutil.which("soffice")
            if not soffice:
                raise VlmConversionError(f"VLM 转换 {suffix} 需要 LibreOffice")

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
            pdf_path = pdf_files[0]
        else:
            raise VlmConversionError(f"不支持的格式: {suffix}")

        if self._has_fitz():
            import fitz

            fitz_doc = fitz.open(str(pdf_path))
            return _OpenedDocument(
                pdf_path=pdf_path,
                page_count=int(fitz_doc.page_count),
                fitz_doc=fitz_doc,
                tmp_dir=tmp,
            )

        return _OpenedDocument(
            pdf_path=pdf_path,
            page_count=self._pdf_page_count(pdf_path),
            fitz_doc=None,
            tmp_dir=tmp,
        )

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

        prompt = self._prompt_template.format(
            page_number=page_range,
            total_pages=total_pages,
            previous_context=context_block,
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
        doc: _OpenedDocument,
        page_nums: list[int],
        file_stem: str,
        assets_dir: Path,
    ) -> str:
        """解析 FIGURE 坐标，从 PDF page 重新渲染裁剪区域并替换路径

        页面归属策略：按 FIGURE 在 md 文本中的位置比例，映射到 batch 内的
        对应页面。单页 batch 时所有 FIGURE 都属于该页。
        """
        if doc.fitz_doc is None:
            return _FIGURE_RE.sub(lambda m: f"[图：{m.group(1)}]", md)

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

            page = doc.fitz_doc[target_page]
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
    ) -> AsyncGenerator[tuple[int, int, str, Path, bool], None]:
        """把文档按 batch_pages 一批识别为 markdown 并 yield

        Args:
            file_path: 源文件 (PDF / DOCX, DOCX 自动转 PDF)
            batch_pages: 每批多少页 (默认 5)
            output_dir: 该文件的 batch 落盘目录，文件名 page_NNN-NNN.md (1-based)
            already_done: 已完成的 (page_start, page_end) 1-based 元组列表，跳过这些 batch
            max_retries: 单 batch 调用 LLM 失败时的重试次数

        Yields:
            (page_start_1based, page_end_1based, md_text, md_path, is_failed)
            is_failed=True 表示该 batch 重试耗尽，md_text 为空，md_path 指向未写入的缓存路径
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        done_set: set[tuple[int, int]] = {(p[0], p[1]) for p in already_done}

        doc = self._open_document(file_path)
        # fitz Document 非线程安全：渲染/裁图都访问 doc，须串行化（VLM 网络调用在锁外并发）。
        doc_lock = asyncio.Lock()

        try:
            total_pages = doc.page_count
            assets_dir = self._assets_dir(file_path)

            logger.info(
                "recognize_start",
                file=file_path.name,
                total_pages=total_pages,
                batch_pages=batch_pages,
                max_concurrent=self.max_concurrent,
            )

            # 预先按顺序枚举所有 batch（page_start_1based, page_end_1based, page_nums_0based）
            specs: list[tuple[int, int, list[int]]] = []
            page_idx = 0
            while page_idx < total_pages:
                end_excl = min(page_idx + batch_pages, total_pages)
                specs.append((page_idx + 1, end_excl, list(range(page_idx, end_excl))))
                page_idx = end_excl

            if self.max_concurrent <= 1:
                # 串行：保留 prev_context 跨批续接（identical to 旧行为）
                prev_md_tail = ""
                for ps, pe, nums in specs:
                    for tup in await self._process_one_batch(
                        doc=doc,
                        doc_lock=doc_lock,
                        page_start=ps,
                        page_end=pe,
                        page_nums_0based=nums,
                        total_pages=total_pages,
                        output_dir=output_dir,
                        assets_dir=assets_dir,
                        file_path=file_path,
                        prev_context=prev_md_tail,
                        done_set=done_set,
                        max_retries=max_retries,
                    ):
                        if not tup[4] and tup[2].strip():
                            prev_md_tail = self._tail_lines(tup[2])
                        yield tup
            else:
                # REC-02：批内有界并发 + 滑动窗口保序 yield。Semaphore 在渲染前获取，
                # 故在途图像 ≤ max_concurrent（内存可控）；并发模式放弃 prev_context
                # （接受轻微跨批续接损失，换取墙钟数倍提速）。
                sem = asyncio.Semaphore(self.max_concurrent)

                async def run_spec(
                    ps: int, pe: int, nums: list[int]
                ) -> list[tuple[int, int, str, Path, bool]]:
                    async with sem:
                        return await self._process_one_batch(
                            doc=doc,
                            doc_lock=doc_lock,
                            page_start=ps,
                            page_end=pe,
                            page_nums_0based=nums,
                            total_pages=total_pages,
                            output_dir=output_dir,
                            assets_dir=assets_dir,
                            file_path=file_path,
                            prev_context="",
                            done_set=done_set,
                            max_retries=max_retries,
                        )

                tasks = [asyncio.create_task(run_spec(ps, pe, nums)) for ps, pe, nums in specs]
                try:
                    for t in tasks:
                        for tup in await t:
                            yield tup
                finally:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
        finally:
            doc.close()

    async def _process_one_batch(
        self,
        *,
        doc: Any,
        doc_lock: asyncio.Lock,
        page_start: int,
        page_end: int,
        page_nums_0based: list[int],
        total_pages: int,
        output_dir: Path,
        assets_dir: Path,
        file_path: Path,
        prev_context: str,
        done_set: set[tuple[int, int]],
        max_retries: int,
    ) -> list[tuple[int, int, str, Path, bool]]:
        """识别单个 batch，返回 yield 元组列表（正常 1 个；内容审核误判逐页回退时 N 个）。

        doc 访问（渲染/裁图）在 ``doc_lock`` 内串行化；VLM 网络调用在锁外（可并发）。
        注：max_tokens 截断检测（stop_reason）由 REC-07 在 LLM 层统一接入后再补到此处。
        """
        from sciforge.pipeline.retry import RetryExhausted, with_retry

        cache_path = output_dir / f"page_{page_start:03d}-{page_end:03d}.md"
        # checkpoint 快速路径
        if (page_start, page_end) in done_set or cache_path.exists():
            md_text = cache_path.read_text(encoding="utf-8") if cache_path.exists() else ""
            if md_text.strip():
                logger.info(
                    "recognize_cached",
                    file=file_path.name,
                    pages=f"{page_start}-{page_end}",
                )
                return [(page_start, page_end, md_text, cache_path, False)]
            return []

        async with doc_lock:
            batch_images = await asyncio.to_thread(self._render_batch_images, doc, page_nums_0based)

        async def _call() -> str:
            return await self._convert_batch(
                page_nums=page_nums_0based,
                images=batch_images,
                total_pages=total_pages,
                prev_context=prev_context,
            )

        try:
            raw_md = await with_retry(
                _call,
                max_attempts=max_retries,
                base_delay=2.0,
                on_retry=lambda i, e: logger.warning(
                    "vlm_batch_retry",
                    file=file_path.name,
                    pages=f"{page_start}-{page_end}",
                    attempt=i,
                    error=str(e)[:200],
                ),
                rate_limit_base_sleep=self.rate_limit_base_sleep,
                rate_limit_max_sleep=self.rate_limit_max_sleep,
                rate_limit_total_budget=self.rate_limit_total_budget,
            )
        except RetryExhausted as exc:
            err_str = str(getattr(exc, "last_exc", "") or "")
            # MiniMax 等 provider 的内容审核**误判**（单张图触发 1026/new_sensitive）：
            # 整批失败会把整批页全丢 → 回退**逐页识别**，只损失被误判的那一页。
            is_content_flag = any(
                s in err_str.lower() for s in ("sensitive", "new_sensitive", "1026")
            )
            if is_content_flag and len(page_nums_0based) > 1:
                logger.warning(
                    "vlm_batch_sensitive_fallback_perpage",
                    file=file_path.name,
                    pages=f"{page_start}-{page_end}",
                    detail=err_str[:160],
                )
                results: list[tuple[int, int, str, Path, bool]] = []
                async for tup in self._recognize_pages_individually(
                    doc=doc,
                    doc_lock=doc_lock,
                    page_nums_0based=page_nums_0based,
                    total_pages=total_pages,
                    output_dir=output_dir,
                    prev_md_tail=prev_context,
                    file_path=file_path,
                    assets_dir=assets_dir,
                    max_retries=max_retries,
                ):
                    results.append(tup)
                return results

            logger.error(
                "vlm_batch_failed",
                file=file_path.name,
                pages=f"{page_start}-{page_end}",
                attempts=exc.attempts,
            )
            # yield is_failed=True 让上游 Producer 记入 state.failed_batches，避免缓存直通误判
            return [(page_start, page_end, "", cache_path, True)]

        async with doc_lock:
            md_text = await asyncio.to_thread(
                self._crop_figures_from_md,
                raw_md,
                doc,
                page_nums_0based,
                file_path.stem,
                assets_dir,
            )

        cache_path.write_text(md_text, encoding="utf-8")
        logger.info(
            "recognize_batch_done",
            file=file_path.name,
            pages=f"{page_start}-{page_end}",
            total=total_pages,
            md_length=len(md_text),
        )
        return [(page_start, page_end, md_text, cache_path, False)]

    async def _recognize_pages_individually(
        self,
        *,
        doc: Any,
        doc_lock: asyncio.Lock,
        page_nums_0based: list[int],
        total_pages: int,
        output_dir: Path,
        prev_md_tail: str,
        file_path: Path,
        assets_dir: Path,
        max_retries: int,
    ) -> AsyncGenerator[tuple[int, int, str, Path, bool], None]:
        """整批被内容审核拒绝时的**逐页回退**：逐张图单独识别。

        只让真正被误判的那一页 ``is_failed=True``，其余页正常恢复。每页落盘
        ``page_NNN-NNN.md``（单页范围），resume 时可命中跳过。
        """
        from sciforge.pipeline.retry import RetryExhausted, with_retry

        for pno in page_nums_0based:
            p1 = pno + 1
            cache_path = output_dir / f"page_{p1:03d}-{p1:03d}.md"
            if cache_path.exists():
                md_cached = cache_path.read_text(encoding="utf-8")
                if md_cached.strip():
                    prev_md_tail = self._tail_lines(md_cached)
                    yield (p1, p1, md_cached, cache_path, False)
                    continue

            async with doc_lock:
                images = await asyncio.to_thread(self._render_batch_images, doc, [pno])

            async def _call(_imgs: list[bytes] = images, _p: int = pno) -> str:
                return await self._convert_batch(
                    page_nums=[_p],
                    images=_imgs,
                    total_pages=total_pages,
                    prev_context=prev_md_tail,
                )

            try:
                raw_md = await with_retry(
                    _call,
                    max_attempts=max_retries,
                    base_delay=2.0,
                    on_retry=lambda i, e: logger.warning(
                        "vlm_page_retry",
                        file=file_path.name,
                        page=p1,
                        attempt=i,
                        error=str(e)[:160],
                    ),
                    rate_limit_base_sleep=self.rate_limit_base_sleep,
                    rate_limit_max_sleep=self.rate_limit_max_sleep,
                    rate_limit_total_budget=self.rate_limit_total_budget,
                )
            except RetryExhausted as exc:
                # 单页仍被拒（确属内容审核误判或图损坏）→ 只丢这一页
                logger.error(
                    "vlm_page_failed",
                    file=file_path.name,
                    page=p1,
                    detail=str(getattr(exc, "last_exc", "") or "")[:160],
                )
                yield (p1, p1, "", cache_path, True)
                continue

            async with doc_lock:
                md_text = await asyncio.to_thread(
                    self._crop_figures_from_md, raw_md, doc, [pno], file_path.stem, assets_dir
                )
            cache_path.write_text(md_text, encoding="utf-8")
            prev_md_tail = self._tail_lines(md_text)
            yield (p1, p1, md_text, cache_path, False)

    def _render_pdf_page_with_poppler(self, pdf_path: Path, page_number_1based: int) -> bytes:
        renderer = shutil.which("pdftocairo") or shutil.which("pdftoppm")
        if not renderer:
            raise VlmConversionError("缺少 PDF 渲染器：需要 pdftocairo 或 pdftoppm")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_prefix = Path(tmp_dir) / f"page_{page_number_1based:04d}"
            result = subprocess.run(
                [
                    renderer,
                    "-png",
                    "-singlefile",
                    "-r",
                    str(self.dpi),
                    "-f",
                    str(page_number_1based),
                    "-l",
                    str(page_number_1based),
                    str(pdf_path),
                    str(output_prefix),
                ],
                capture_output=True,
                timeout=120,
                check=False,
            )
            png_path = output_prefix.with_suffix(".png")
            if result.returncode != 0 or not png_path.exists():
                stderr = result.stderr.decode("utf-8", errors="replace")
                raise VlmConversionError(f"PDF 渲染失败: {stderr or 'unknown error'}")
            return png_path.read_bytes()

    def _render_batch_images(
        self,
        doc: _OpenedDocument,
        page_nums_0based: list[int],
    ) -> list[bytes]:
        """把一批页面渲染为 PNG bytes，供 asyncio.to_thread 调用"""
        images: list[bytes] = []
        if doc.fitz_doc is None:
            for pn in page_nums_0based:
                images.append(self._render_pdf_page_with_poppler(doc.pdf_path, pn + 1))
            return images
        for pn in page_nums_0based:
            page = doc.fitz_doc[pn]
            pix = page.get_pixmap(dpi=self.dpi)
            images.append(pix.tobytes("png"))
        return images
