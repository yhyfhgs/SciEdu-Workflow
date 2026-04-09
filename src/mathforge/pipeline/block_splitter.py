"""BlockSplitter — 文档语义分块器

全文档滑动窗口架构：
1. 按 chunk_size 行读取文档 + 完整下一个 chunk 作为后续上下文
2. LLM 返回截断行号（语义完整边界）+ FormatSpec
3. 截断行包含在下一个 block 中（防止行内分割漏字）
4. LLM 可返回 "incomplete" 表示需要更多内容，系统自动追加下一个 chunk
5. 循环直到文档末尾

输出为 async generator，支持流水线消费。
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
    DocumentFormat,
    FormatSpec,
    MdBlockType,
    MdBlockV2,
)
from mathforge.pipeline.format_spec_library import FormatSpecLibrary
from mathforge.prompts.templates import BLOCK_BOUNDARY_INFER_PROMPT

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig
    from mathforge.llm.client import LLMClient

logger = structlog.get_logger()

# TeX 文件前导区行 pattern
_TEX_PREAMBLE_RE = re.compile(
    r"^\s*\\(?:documentclass|usepackage|newcommand|title|author|date|maketitle"
    r"|begin\{document\}|end\{document\}|include|input)"
)


class StreamingLineBuffer(Sequence[str]):
    """可增量追加内容的行缓冲区，供流式 VLM→BlockSplitter 管线使用。

    VLM producer 调用 append_chunk() 追加 Markdown 文本，
    BlockSplitter consumer 调用 wait_for_lines() 等待足够的行可用。
    所有操作在同一 event loop 上，无需锁。
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
        """标记流结束（不会再有新内容到达）"""
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
        """等待至少 needed 行可用或流结束。

        Returns:
            True 如果有足够行可用，False 如果流已结束且行数不够。
        """
        while len(self._lines) < needed and not self._finished:
            self._new_content.clear()
            await self._new_content.wait()
        return len(self._lines) >= needed


class BlockSplitter:
    """文档语义分块器 — 滑动窗口 + LLM 截断点推断"""

    def __init__(
        self,
        llm_client: LLMClient | None,
        config: AppConfig,
        spec_library: FormatSpecLibrary | None = None,
    ) -> None:
        self.llm = llm_client
        self.config = config
        self._spec_library = spec_library

    async def split_file(
        self,
        file_path: Path,
        content: str,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[MdBlockV2, None]:
        """将文档拆分为语义完整的 MdBlockV2 流，每个 block 附带 FormatSpec"""
        t0 = time.monotonic()

        if not self.llm:
            # 无 LLM: 整个文件作为单 block
            logger.warning("block_split_single_block", file=file_path.name)
            yield MdBlockV2(
                block_id=str(uuid.uuid4()),
                block_type=MdBlockType.TEXT,
                content=content,
                source_file=str(file_path),
                source_format=source_format,
                line_start=0,
                line_end=len(content.splitlines()),
            )
            return

        lines = content.splitlines(keepends=True)
        block_index = 0
        async for block in self._split_sliding_window(file_path, lines, source_format):
            block.block_index = block_index
            block_index += 1
            elapsed = time.monotonic() - t0
            logger.info(
                "block_split",
                block_index=block.block_index,
                lines=f"{block.line_start}-{block.line_end}",
                content_len=len(block.content),
                method="sliding_window",
                elapsed_sec=round(elapsed, 2),
            )
            yield block

    async def split_file_streaming(
        self,
        file_path: Path,
        line_buffer: StreamingLineBuffer,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[MdBlockV2, None]:
        """流式分块：从 StreamingLineBuffer 读取增量到达的内容。

        当 buffer 中的行不够组装一个 chunk 时，会等待 VLM producer 追加更多内容。
        """
        t0 = time.monotonic()

        if not self.llm:
            # 无 LLM: 等待全部内容，作为单 block
            while not line_buffer.is_finished:
                await line_buffer.wait_for_lines(len(line_buffer) + 1)
            content = "".join(line_buffer[:])
            if content.strip():
                yield MdBlockV2(
                    block_id=str(uuid.uuid4()),
                    block_type=MdBlockType.TEXT,
                    content=content,
                    source_file=str(file_path),
                    source_format=source_format,
                    line_start=0,
                    line_end=len(line_buffer),
                )
            return

        block_index = 0
        async for block in self._split_sliding_window_streaming(
            file_path, line_buffer, source_format
        ):
            block.block_index = block_index
            block_index += 1
            elapsed = time.monotonic() - t0
            logger.info(
                "block_split",
                block_index=block.block_index,
                lines=f"{block.line_start}-{block.line_end}",
                content_len=len(block.content),
                method="streaming_window",
                elapsed_sec=round(elapsed, 2),
            )
            yield block

    # ── 滑动窗口分块 ──

    async def _split_sliding_window(
        self,
        file_path: Path,
        lines: list[str],
        source_format: DocumentFormat,
    ) -> AsyncGenerator[MdBlockV2, None]:
        """全文档滑动窗口：循环推断截断点 + FormatSpec"""
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

            # 2. 取完整的下一个 chunk 作为后续上下文
            next_end = min(pos + chunk_size, total)
            next_block = "".join(lines[pos:next_end])

            is_last = pos >= total

            if is_last and not next_block.strip():
                # 最后一批，直接输出
                text = "".join(chunk_lines)
                if text.strip():
                    yield self._make_block(
                        text,
                        None,
                        file_path,
                        source_format,
                        global_line_offset,
                        global_line_offset + len(chunk_lines),
                        raw_metadata,
                    )
                break

            # 3. LLM 调用：截断点 + FormatSpec
            chunk_text = "".join(chunk_lines)
            result = await self._infer_boundary(
                chunk_text,
                next_block,
                prev_tail,
                file_path,
                source_format,
            )
            trunc = result.get("truncation_line")

            # 4. 处理 "incomplete" — 追加下一个 chunk 继续扩展
            while trunc == "incomplete" and pos < total:
                extend_end = min(pos + chunk_size, total)
                chunk_lines.extend(lines[pos:extend_end])
                pos = extend_end
                # 重新取下一段上下文
                next_end = min(pos + chunk_size, total)
                next_block = "".join(lines[pos:next_end])
                chunk_text = "".join(chunk_lines)

                logger.info(
                    "block_split_extend",
                    chunk_lines=len(chunk_lines),
                    reason="incomplete",
                )

                result = await self._infer_boundary(
                    chunk_text,
                    next_block,
                    prev_tail,
                    file_path,
                    source_format,
                )
                trunc = result.get("truncation_line")

            # 到文档末尾仍然 incomplete，直接全部输出
            if trunc == "incomplete":
                trunc = None

            spec_data = result.get("format_spec", {})
            spec = self._parse_format_spec(spec_data) if spec_data else None

            # 缓存有效 spec
            if spec and spec.content_blocks and self._spec_library:
                self._spec_library.put(lines, source_format, spec, 1.0)

            # 5. 按截断点分割（截断行纳入下一个 block）
            if trunc is not None and isinstance(trunc, int) and 0 < trunc < len(chunk_lines):
                complete_lines = chunk_lines[:trunc]
                carry_over_lines = chunk_lines[trunc:]
            else:
                complete_lines = chunk_lines
                carry_over_lines = []

            # 6. yield 完整部分
            complete_text = "".join(complete_lines)
            if complete_text.strip():
                line_end = global_line_offset + len(complete_lines)
                yield self._make_block(
                    complete_text,
                    spec,
                    file_path,
                    source_format,
                    global_line_offset,
                    line_end,
                    raw_metadata,
                )
                prev_tail = "\n".join(complete_text.strip().splitlines()[-10:])
                global_line_offset = line_end

        # 处理最终残余
        if carry_over_lines:
            text = "".join(carry_over_lines)
            if text.strip():
                yield self._make_block(
                    text,
                    None,
                    file_path,
                    source_format,
                    global_line_offset,
                    global_line_offset + len(carry_over_lines),
                    raw_metadata,
                )

    async def _split_sliding_window_streaming(
        self,
        file_path: Path,
        buf: StreamingLineBuffer,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[MdBlockV2, None]:
        """流式滑动窗口：与 _split_sliding_window 相同逻辑，但从 StreamingLineBuffer 读取。

        当 pos 到达 buffer 当前末尾但流未结束时，等待更多内容到达后继续。
        """
        assert self.llm is not None

        chunk_size = self.config.pipeline_split_batch_lines
        raw_metadata = self._extract_file_metadata(file_path, source_format)

        pos = 0
        carry_over_lines: list[str] = []
        prev_tail = ""
        global_line_offset = 0

        while True:
            # 等待足够的行来组装 chunk（或流结束）
            needed = pos + max(0, chunk_size - len(carry_over_lines))
            await buf.wait_for_lines(needed)

            total = len(buf)
            if pos >= total and not carry_over_lines:
                if buf.is_finished:
                    break
                # 流未结束但暂时没新内容，继续等
                continue

            # 1. 组装当前 chunk = carry_over + 新读取的行
            read_count = max(0, chunk_size - len(carry_over_lines))
            read_end = min(pos + read_count, total)
            chunk_lines = carry_over_lines + list(buf[pos:read_end])
            pos = read_end

            # 2. 取后续上下文（可能需要等待更多行到达）
            next_needed = pos + chunk_size
            await buf.wait_for_lines(min(next_needed, pos + 1))
            cur_total = len(buf)
            next_end = min(pos + chunk_size, cur_total)
            next_block = "".join(buf[pos:next_end])

            is_last = buf.is_finished and pos >= cur_total

            if is_last and not next_block.strip():
                text = "".join(chunk_lines)
                if text.strip():
                    yield self._make_block(
                        text,
                        None,
                        file_path,
                        source_format,
                        global_line_offset,
                        global_line_offset + len(chunk_lines),
                        raw_metadata,
                    )
                carry_over_lines = []
                break

            # 3. LLM 调用
            chunk_text = "".join(chunk_lines)
            result = await self._infer_boundary(
                chunk_text, next_block, prev_tail, file_path, source_format
            )
            trunc = result.get("truncation_line")

            # 4. 处理 "incomplete" — 追加更多行
            while trunc == "incomplete":
                # 等待更多行到达（或流结束）
                extend_needed = pos + chunk_size
                await buf.wait_for_lines(min(extend_needed, pos + 1))
                cur_total = len(buf)

                if pos >= cur_total and buf.is_finished:
                    break  # 流结束，不再有更多内容

                extend_end = min(pos + chunk_size, cur_total)
                chunk_lines.extend(buf[pos:extend_end])
                pos = extend_end

                next_end = min(pos + chunk_size, len(buf))
                next_block = "".join(buf[pos:next_end])
                chunk_text = "".join(chunk_lines)

                logger.info(
                    "block_split_extend",
                    chunk_lines=len(chunk_lines),
                    reason="incomplete_streaming",
                )

                result = await self._infer_boundary(
                    chunk_text, next_block, prev_tail, file_path, source_format
                )
                trunc = result.get("truncation_line")

            if trunc == "incomplete":
                trunc = None

            spec_data = result.get("format_spec", {})
            spec = self._parse_format_spec(spec_data) if spec_data else None

            if spec and spec.content_blocks and self._spec_library:
                self._spec_library.put(list(buf[:]), source_format, spec, 1.0)

            # 5. 按截断点分割
            if trunc is not None and isinstance(trunc, int) and 0 < trunc < len(chunk_lines):
                complete_lines = chunk_lines[:trunc]
                carry_over_lines = chunk_lines[trunc:]
            else:
                complete_lines = chunk_lines
                carry_over_lines = []

            # 6. yield 完整部分
            complete_text = "".join(complete_lines)
            if complete_text.strip():
                line_end = global_line_offset + len(complete_lines)
                yield self._make_block(
                    complete_text,
                    spec,
                    file_path,
                    source_format,
                    global_line_offset,
                    line_end,
                    raw_metadata,
                )
                prev_tail = "\n".join(complete_text.strip().splitlines()[-10:])
                global_line_offset = line_end

        # 处理最终残余
        if carry_over_lines:
            text = "".join(carry_over_lines)
            if text.strip():
                yield self._make_block(
                    text,
                    None,
                    file_path,
                    source_format,
                    global_line_offset,
                    global_line_offset + len(carry_over_lines),
                    raw_metadata,
                )

    # ── LLM 调用 ──

    async def _infer_boundary(
        self,
        chunk_text: str,
        next_block: str,
        prev_tail: str,
        file_path: Path,
        source_format: DocumentFormat,
    ) -> dict[str, Any]:
        """调用 LLM 推断截断点和 FormatSpec"""
        assert self.llm is not None

        safe_chunk = chunk_text.replace("{", "{{").replace("}", "}}")
        safe_next = next_block.replace("{", "{{").replace("}", "}}")
        safe_tail = prev_tail.replace("{", "{{").replace("}", "}}")

        prompt = BLOCK_BOUNDARY_INFER_PROMPT.format(
            current_block=safe_chunk,
            next_block=safe_next if safe_next.strip() else "(文档结尾，无后续内容)",
            previous_tail=safe_tail or "(文档开头)",
            file_name=file_path.name,
            source_format=source_format.value,
        )

        t0 = time.monotonic()
        try:
            result = await self.llm.generate_json(prompt)
            latency = time.monotonic() - t0
            logger.info(
                "block_boundary_infer",
                file=file_path.name,
                chunk_lines=len(chunk_text.splitlines()),
                truncation=result.get("truncation_line"),
                latency_sec=round(latency, 2),
            )
            return result
        except Exception:
            logger.error(
                "block_boundary_infer_failed",
                file=file_path.name,
                exc_info=True,
            )
            return {"truncation_line": None, "format_spec": {}}

    # ── 辅助方法 ──

    @staticmethod
    def _make_block(
        content: str,
        spec: FormatSpec | None,
        file_path: Path,
        source_format: DocumentFormat,
        line_start: int,
        line_end: int,
        raw_metadata: dict[str, Any],
    ) -> MdBlockV2:
        """构造 MdBlockV2"""
        return MdBlockV2(
            block_id=str(uuid.uuid4()),
            block_type=MdBlockType.TEXT,
            content=content,
            source_file=str(file_path),
            source_format=source_format,
            line_start=line_start,
            line_end=line_end,
            format_spec=spec,
            raw_metadata=raw_metadata,
        )

    @staticmethod
    def _parse_format_spec(data: dict[str, Any]) -> FormatSpec | None:
        """从 LLM 输出解析 FormatSpec"""
        if not data:
            return None

        from mathforge.core.models import AnswerFormatSpec, ContentBlockSpec

        blocks = []
        for b in data.get("content_blocks", []):
            try:
                blocks.append(ContentBlockSpec(**b))
            except Exception:
                pass

        answer_data = data.get("answer_format", {})
        try:
            answer = AnswerFormatSpec(**answer_data)
        except Exception:
            answer = AnswerFormatSpec()

        # LLM 可能返回 dict 列表而非 string 列表，需要归一化
        raw_structure = data.get("detected_structure", [])
        structure: list[str] = []
        for item in raw_structure:
            if isinstance(item, str):
                structure.append(item)
            elif isinstance(item, dict):
                structure.append(item.get("title", str(item)))
            else:
                structure.append(str(item))

        return FormatSpec(
            content_blocks=blocks,
            answer_format=answer,
            section_pattern=data.get("section_pattern"),
            source_type=data.get("source_type", ""),
            detected_structure=structure,
            document_description=data.get("document_description", ""),
        )

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
