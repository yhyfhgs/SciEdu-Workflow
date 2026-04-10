"""ChunkExtractor — LLM 直接提取条目的文档处理器

滑动窗口架构（复用自 BlockSplitter）：
1. 按 chunk_size 行读取文档 + 下一个 chunk 作为后续上下文
2. LLM 直接返回截断行号 + 结构上下文 + 提取的条目 JSON
3. 截断行纳入下一个 chunk（防止行内分割漏字）
4. LLM 可返回 "incomplete" 表示需要更多内容
5. 循环直到文档末尾

输出为 async generator，yield ChunkExtractionResult。
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import AsyncGenerator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

import structlog

from mathforge.core.models import (
    ChunkExtractionResult,
    DocumentFormat,
    ExtractedItem,
    StructuralContext,
)
from mathforge.prompts.templates import CHUNK_EXTRACT_PROMPT

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig
    from mathforge.llm.client import LLMClient

logger = structlog.get_logger()


# ── StreamingLineBuffer ────────────────────────────────────


class StreamingLineBuffer(Sequence[str]):
    """可增量追加内容的行缓冲区，供流式 VLM→ChunkExtractor 管线使用。

    VLM producer 调用 append_chunk() 追加 Markdown 文本，
    ChunkExtractor consumer 调用 wait_for_lines() 等待足够的行可用。
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._finished = False
        self._new_content = asyncio.Event()

    def append_chunk(self, text: str) -> None:
        """追加一段 Markdown 文本（由 VLM producer 调用）"""
        if text:
            new_lines = text.splitlines(keepends=True)
            self._lines.extend(new_lines)
        self._new_content.set()

    def finish(self) -> None:
        """标记流结束"""
        self._finished = True
        self._new_content.set()

    @property
    def is_finished(self) -> bool:
        return self._finished

    def __len__(self) -> int:
        return len(self._lines)

    @overload
    def __getitem__(self, index: int) -> str: ...

    @overload
    def __getitem__(self, index: slice) -> list[str]: ...

    def __getitem__(self, index: int | slice) -> str | list[str]:
        return self._lines[index]

    async def wait_for_lines(self, needed: int) -> bool:
        """等待至少 needed 行可用或流结束。"""
        while len(self._lines) < needed and not self._finished:
            self._new_content.clear()
            await self._new_content.wait()
        return len(self._lines) >= needed


# ── ChunkExtractor ─────────────────────────────────────────


class ChunkExtractor:
    """文档条目提取器 — 滑动窗口 + LLM 直接提取"""

    def __init__(
        self,
        llm_client: LLMClient | None,
        config: AppConfig,
    ) -> None:
        self.llm = llm_client
        self.config = config

    async def extract_file(
        self,
        file_path: Path,
        content: str,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[ChunkExtractionResult, None]:
        """从完整文本中提取条目流"""
        t0 = time.monotonic()

        if not self.llm:
            logger.warning("chunk_extract_no_llm", file=file_path.name)
            return

        lines = content.splitlines(keepends=True)
        chunk_index = 0
        async for result in self._extract_sliding_window(file_path, lines, source_format):
            result.chunk_index = chunk_index
            chunk_index += 1
            elapsed = time.monotonic() - t0
            logger.info(
                "chunk_extracted",
                chunk_index=result.chunk_index,
                lines=f"{result.line_start}-{result.line_end}",
                items_count=len(result.items),
                elapsed_sec=round(elapsed, 2),
            )
            yield result

    async def extract_file_streaming(
        self,
        file_path: Path,
        line_buffer: StreamingLineBuffer,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[ChunkExtractionResult, None]:
        """从 StreamingLineBuffer 增量提取条目流"""
        t0 = time.monotonic()

        if not self.llm:
            logger.warning("chunk_extract_no_llm", file=file_path.name)
            return

        chunk_index = 0
        async for result in self._extract_sliding_window_streaming(
            file_path, line_buffer, source_format
        ):
            result.chunk_index = chunk_index
            chunk_index += 1
            elapsed = time.monotonic() - t0
            logger.info(
                "chunk_extracted",
                chunk_index=result.chunk_index,
                lines=f"{result.line_start}-{result.line_end}",
                items_count=len(result.items),
                method="streaming",
                elapsed_sec=round(elapsed, 2),
            )
            yield result

    # ── 滑动窗口提取 ──

    async def _extract_sliding_window(
        self,
        file_path: Path,
        lines: list[str],
        source_format: DocumentFormat,
    ) -> AsyncGenerator[ChunkExtractionResult, None]:
        """全文档滑动窗口：循环推断截断点 + 直接提取条目"""
        assert self.llm is not None

        chunk_size = self.config.pipeline_split_batch_lines
        total = len(lines)
        raw_metadata = self._extract_file_metadata(file_path, source_format)

        pos = 0
        carry_over_lines: list[str] = []
        prev_tail = ""
        global_line_offset = 0

        while pos < total or carry_over_lines:
            # 1. 组装当前 chunk = carry_over + 新读取的行
            read_count = max(0, chunk_size - len(carry_over_lines))
            read_end = min(pos + read_count, total)
            chunk_lines = carry_over_lines + list(lines[pos:read_end])
            pos = read_end

            # 2. 后续上下文
            next_end = min(pos + chunk_size, total)
            next_block = "".join(lines[pos:next_end])

            is_last = pos >= total

            if is_last and not next_block.strip():
                # 最后一批：直接提取
                result = await self._call_extract(
                    chunk_lines, "", prev_tail, file_path, source_format,
                    global_line_offset, raw_metadata, force_complete=True,
                )
                if result:
                    yield result
                break

            # 3. LLM 调用
            chunk_text_numbered = self._number_lines(chunk_lines)
            llm_result = await self._infer_and_extract(
                chunk_text_numbered, next_block, prev_tail, file_path, source_format,
            )
            trunc = llm_result.get("truncation_line")

            # 4. 处理 "incomplete"
            while trunc == "incomplete" and pos < total:
                extend_end = min(pos + chunk_size, total)
                chunk_lines.extend(lines[pos:extend_end])
                pos = extend_end
                next_end = min(pos + chunk_size, total)
                next_block = "".join(lines[pos:next_end])
                chunk_text_numbered = self._number_lines(chunk_lines)

                logger.info(
                    "chunk_extract_extend",
                    chunk_lines=len(chunk_lines),
                    reason="incomplete",
                )

                llm_result = await self._infer_and_extract(
                    chunk_text_numbered, next_block, prev_tail, file_path, source_format,
                )
                trunc = llm_result.get("truncation_line")

            if trunc == "incomplete":
                trunc = None

            # 5. 按截断点分割
            if trunc is not None and isinstance(trunc, int) and 0 < trunc < len(chunk_lines):
                complete_lines = chunk_lines[:trunc]
                carry_over_lines = chunk_lines[trunc:]
            else:
                complete_lines = chunk_lines
                carry_over_lines = []

            # 6. 构建 ChunkExtractionResult
            complete_text = "".join(complete_lines)
            if complete_text.strip():
                line_end = global_line_offset + len(complete_lines)
                result = self._build_result(
                    llm_result, file_path, source_format,
                    global_line_offset, line_end, raw_metadata,
                )
                yield result
                prev_tail = "\n".join(complete_text.strip().splitlines()[-10:])
                global_line_offset = line_end

        # 处理最终残余
        if carry_over_lines:
            text = "".join(carry_over_lines)
            if text.strip():
                result = await self._call_extract(
                    carry_over_lines, "", prev_tail, file_path, source_format,
                    global_line_offset, raw_metadata, force_complete=True,
                )
                if result:
                    yield result

    async def _extract_sliding_window_streaming(
        self,
        file_path: Path,
        buf: StreamingLineBuffer,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[ChunkExtractionResult, None]:
        """流式滑动窗口：从 StreamingLineBuffer 增量读取"""
        assert self.llm is not None

        chunk_size = self.config.pipeline_split_batch_lines
        raw_metadata = self._extract_file_metadata(file_path, source_format)

        pos = 0
        carry_over_lines: list[str] = []
        prev_tail = ""
        global_line_offset = 0

        while True:
            needed = pos + max(0, chunk_size - len(carry_over_lines))
            await buf.wait_for_lines(needed)

            total = len(buf)
            if pos >= total and not carry_over_lines:
                if buf.is_finished:
                    break
                continue

            # 1. 组装 chunk
            read_count = max(0, chunk_size - len(carry_over_lines))
            read_end = min(pos + read_count, total)
            chunk_lines = carry_over_lines + list(buf[pos:read_end])
            pos = read_end

            # 2. 后续上下文
            next_needed = pos + chunk_size
            await buf.wait_for_lines(min(next_needed, pos + 1))
            cur_total = len(buf)
            next_end = min(pos + chunk_size, cur_total)
            next_block = "".join(buf[pos:next_end])

            is_last = buf.is_finished and pos >= cur_total

            if is_last and not next_block.strip():
                result = await self._call_extract(
                    chunk_lines, "", prev_tail, file_path, source_format,
                    global_line_offset, raw_metadata, force_complete=True,
                )
                if result:
                    yield result
                carry_over_lines = []
                break

            # 3. LLM 调用
            chunk_text_numbered = self._number_lines(chunk_lines)
            llm_result = await self._infer_and_extract(
                chunk_text_numbered, next_block, prev_tail, file_path, source_format,
            )
            trunc = llm_result.get("truncation_line")

            # 4. 处理 "incomplete"
            while trunc == "incomplete":
                extend_needed = pos + chunk_size
                await buf.wait_for_lines(min(extend_needed, pos + 1))
                cur_total = len(buf)

                if pos >= cur_total and buf.is_finished:
                    break

                extend_end = min(pos + chunk_size, cur_total)
                chunk_lines.extend(buf[pos:extend_end])
                pos = extend_end

                next_end = min(pos + chunk_size, len(buf))
                next_block = "".join(buf[pos:next_end])
                chunk_text_numbered = self._number_lines(chunk_lines)

                logger.info(
                    "chunk_extract_extend",
                    chunk_lines=len(chunk_lines),
                    reason="incomplete_streaming",
                )

                llm_result = await self._infer_and_extract(
                    chunk_text_numbered, next_block, prev_tail, file_path, source_format,
                )
                trunc = llm_result.get("truncation_line")

            if trunc == "incomplete":
                trunc = None

            # 5. 按截断点分割
            if trunc is not None and isinstance(trunc, int) and 0 < trunc < len(chunk_lines):
                complete_lines = chunk_lines[:trunc]
                carry_over_lines = chunk_lines[trunc:]
            else:
                complete_lines = chunk_lines
                carry_over_lines = []

            # 6. yield 结果
            complete_text = "".join(complete_lines)
            if complete_text.strip():
                line_end = global_line_offset + len(complete_lines)
                result = self._build_result(
                    llm_result, file_path, source_format,
                    global_line_offset, line_end, raw_metadata,
                )
                yield result
                prev_tail = "\n".join(complete_text.strip().splitlines()[-10:])
                global_line_offset = line_end

        # 最终残余
        if carry_over_lines:
            text = "".join(carry_over_lines)
            if text.strip():
                result = await self._call_extract(
                    carry_over_lines, "", prev_tail, file_path, source_format,
                    global_line_offset, raw_metadata, force_complete=True,
                )
                if result:
                    yield result

    # ── LLM 调用 ──

    async def _infer_and_extract(
        self,
        chunk_text_numbered: str,
        next_block: str,
        prev_tail: str,
        file_path: Path,
        source_format: DocumentFormat,
    ) -> dict[str, Any]:
        """调用 LLM 同时推断截断点和提取条目"""
        assert self.llm is not None

        safe_chunk = chunk_text_numbered.replace("{", "{{").replace("}", "}}")
        safe_next = next_block.replace("{", "{{").replace("}", "}}")
        safe_tail = prev_tail.replace("{", "{{").replace("}", "}}")

        prompt = CHUNK_EXTRACT_PROMPT.format(
            current_block=safe_chunk,
            next_block=safe_next if safe_next.strip() else "(文档结尾，无后续内容)",
            previous_tail=safe_tail or "(文档开头)",
            file_name=file_path.name,
            source_format=source_format.value,
        )

        t0 = time.monotonic()
        for attempt in range(2):
            try:
                result = await self.llm.generate_json(prompt, max_tokens=32768)
                if result and result.get("extracted_items") is not None:
                    latency = time.monotonic() - t0
                    logger.info(
                        "chunk_extract_llm",
                        file=file_path.name,
                        chunk_lines=len(chunk_text_numbered.splitlines()),
                        truncation=result.get("truncation_line"),
                        items=len(result.get("extracted_items", [])),
                        latency_sec=round(latency, 2),
                    )
                    return result
                # 空响应，重试
                if attempt == 0:
                    logger.info("chunk_extract_retry", file=file_path.name, reason="empty")
            except Exception:
                if attempt == 0:
                    logger.info("chunk_extract_retry", file=file_path.name, reason="parse_error")
                else:
                    logger.warning("chunk_extract_llm_failed", file=file_path.name, exc_info=True)
        return {"truncation_line": None, "structural_context": {}, "extracted_items": []}

    async def _call_extract(
        self,
        chunk_lines: list[str],
        next_block: str,
        prev_tail: str,
        file_path: Path,
        source_format: DocumentFormat,
        global_offset: int,
        raw_metadata: dict,
        *,
        force_complete: bool = False,
    ) -> ChunkExtractionResult | None:
        """对残余 chunk 做一次提取调用"""
        text = "".join(chunk_lines)
        if not text.strip():
            return None

        numbered = self._number_lines(chunk_lines)
        llm_result = await self._infer_and_extract(
            numbered, next_block, prev_tail, file_path, source_format,
        )
        line_end = global_offset + len(chunk_lines)
        return self._build_result(
            llm_result, file_path, source_format, global_offset, line_end, raw_metadata,
        )

    # ── 结果构建 ──

    def _build_result(
        self,
        llm_result: dict[str, Any],
        file_path: Path,
        source_format: DocumentFormat,
        line_start: int,
        line_end: int,
        raw_metadata: dict,
    ) -> ChunkExtractionResult:
        """从 LLM 输出构建 ChunkExtractionResult"""
        chunk_id = str(uuid.uuid4())

        # 解析 structural_context
        ctx_data = llm_result.get("structural_context", {})
        ctx = StructuralContext(
            chapter_title=ctx_data.get("chapter_title", ""),
            chapter_number=str(ctx_data.get("chapter_number", "")),
            section_title=ctx_data.get("section_title", ""),
            section_number=str(ctx_data.get("section_number", "")),
            subsection_title=ctx_data.get("subsection_title", ""),
            subsection_number=str(ctx_data.get("subsection_number", "")),
            document_type=ctx_data.get("document_type", ""),
        )

        # 解析 extracted_items
        items: list[ExtractedItem] = []
        for item_data in llm_result.get("extracted_items", []):
            try:
                # LLM 可能返回 null 而非 0，防御性处理
                item_line_start = item_data.get("line_start") or 0
                item_line_end = item_data.get("line_end") or 0
                item_content = item_data.get("content", "")
                if not item_content or not item_data.get("item_type"):
                    continue  # 跳过空条目
                items.append(ExtractedItem(
                    item_type=item_data.get("item_type", ""),
                    number=item_data.get("number"),
                    content=item_content,
                    proof=item_data.get("proof"),
                    solution=item_data.get("solution"),
                    line_start=line_start + item_line_start,
                    line_end=line_start + item_line_end,
                    source_chunk_id=chunk_id,
                    source_file=str(file_path),
                    structural_context=ctx,
                    raw_metadata=raw_metadata,
                ))
            except Exception:
                logger.warning(
                    "chunk_extract_item_parse_failed",
                    item_data=str(item_data)[:100],
                    exc_info=True,
                )

        return ChunkExtractionResult(
            chunk_id=chunk_id,
            structural_context=ctx,
            items=items,
            source_file=str(file_path),
            source_format=source_format,
            line_start=line_start,
            line_end=line_end,
            raw_metadata=raw_metadata,
        )

    # ── 工具方法 ──

    @staticmethod
    def _number_lines(lines: list[str]) -> str:
        """给行加上行号，方便 LLM 引用"""
        return "".join(f"{i + 1:4d} | {line}" for i, line in enumerate(lines))

    @staticmethod
    def _extract_file_metadata(file_path: Path, source_format: DocumentFormat) -> dict[str, Any]:
        """从文件路径提取通用元数据"""
        metadata: dict[str, Any] = {
            "source_file": str(file_path),
            "source_format": source_format.value,
        }

        name = file_path.stem
        year_match = re.search(r"(19|20)\d{2}", name)
        if year_match:
            metadata["year"] = int(year_match.group())

        for keyword, exam_type in [
            ("零试", "lingshi"),
            ("一试", "yishi"),
            ("女子", "nvzi"),
        ]:
            if keyword in name:
                metadata["exam_type"] = exam_type
                break

        return metadata
