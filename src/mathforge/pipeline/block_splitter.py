"""BlockSplitter — 文档拆分器

两阶段架构：
1. 一次性 FormatSpec 推断（1 次 LLM 调用，文档级）
2. 确定性分块（用推断出的 FormatSpec 正则切分）

Fallback：逐 chunk LLM 拆分（保留原有逻辑）。
输出为 async generator，支持流水线消费。
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from mathforge.core.models import (
    DocumentFormat,
    FormatSpec,
    MdBlockType,
    MdBlockV2,
    SplitCursor,
)
from mathforge.pipeline.format_spec_library import FormatSpecLibrary
from mathforge.pipeline.sampling import AdaptiveSampler
from mathforge.prompts.templates import BLOCK_SPLIT_PROMPT, FORMAT_SPEC_INFER_PROMPT

if TYPE_CHECKING:
    from mathforge.config.settings import AppConfig
    from mathforge.llm.client import LLMClient

logger = structlog.get_logger()

# TeX 文件前导区行 pattern
_TEX_PREAMBLE_RE = re.compile(
    r"^\s*\\(?:documentclass|usepackage|newcommand|title|author|date|maketitle"
    r"|begin\{document\}|end\{document\}|include|input)"
)


class BlockSplitter:
    """文档块拆分器"""

    def __init__(
        self,
        llm_client: LLMClient | None,
        config: AppConfig,
        spec_library: FormatSpecLibrary | None = None,
    ) -> None:
        self.llm = llm_client
        self.config = config
        self._sampler = AdaptiveSampler()
        self._spec_library = spec_library

    async def split_file(
        self,
        file_path: Path,
        content: str,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[MdBlockV2, None]:
        """拆分文件为 MdBlockV2 流

        Phase A: 一次性 LLM 推断 FormatSpec → 确定性分块
        Phase B (fallback): 逐 chunk LLM 分块
        """
        t0 = time.monotonic()

        # Phase A: 一次性推断 FormatSpec
        spec: FormatSpec | None = None
        if self.llm:
            spec = await self._infer_format_spec(file_path, content, source_format)

        if spec:
            logger.info(
                "block_split_spec_inferred",
                file=file_path.name,
                content_blocks=len(spec.content_blocks),
                has_section_pattern=spec.section_pattern is not None,
            )
            block_index = 0
            async for block in self._split_by_spec(file_path, content, spec, source_format):
                block.block_index = block_index
                block_index += 1
                elapsed = time.monotonic() - t0
                logger.info(
                    "block_split",
                    block_index=block.block_index,
                    lines=f"{block.line_start}-{block.line_end}",
                    method="spec",
                    elapsed_sec=round(elapsed, 2),
                )
                yield block
        elif self.llm:
            logger.info("block_split_llm_fallback", file=file_path.name)
            block_index = 0
            async for block in self._split_by_llm(file_path, content, source_format):
                block.block_index = block_index
                block_index += 1
                elapsed = time.monotonic() - t0
                logger.info(
                    "block_split",
                    block_index=block.block_index,
                    lines=f"{block.line_start}-{block.line_end}",
                    method="llm_fallback",
                    elapsed_sec=round(elapsed, 2),
                )
                yield block
        else:
            # 无 LLM，整个文件作为单 block
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

    # ── Phase A: 一次性 FormatSpec 推断 ──

    async def _infer_format_spec(
        self,
        file_path: Path,
        content: str,
        source_format: DocumentFormat,
    ) -> FormatSpec | None:
        """从文档采样中推断 FormatSpec

        优先查缓存库 → LLM 推断 → 验证 pattern → 失败则重试一次
        """
        assert self.llm is not None

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # 1. 查缓存库
        if self._spec_library:
            cached = self._spec_library.get(lines, source_format)
            if cached:
                is_valid, _ = self._validate_spec(cached, lines)
                if is_valid:
                    return cached
            # 尝试模糊匹配
            similar = self._spec_library.find_similar(lines, source_format)
            if similar:
                is_valid, _ = self._validate_spec(similar, lines)
                if is_valid:
                    return similar

        # 2. LLM 推断
        text_sample = self._sampler.sample(lines, source_format)
        spec = await self._llm_infer_spec(file_path, text_sample, source_format, total_lines)

        if spec:
            # 3. 验证 pattern 匹配有效性
            is_valid, failures = self._validate_spec(spec, lines)
            if not is_valid and failures:
                # 4. 用失败信息重试一次
                logger.warning(
                    "format_spec_validation_failed",
                    file=file_path.name,
                    failures=failures[:3],
                )
                retry_spec = await self._llm_infer_spec_retry(
                    file_path, text_sample, source_format, total_lines, failures
                )
                if retry_spec:
                    is_valid, _ = self._validate_spec(retry_spec, lines)
                    if is_valid:
                        spec = retry_spec
                    else:
                        # 重试后仍无效，放弃此 spec，降级到 LLM fallback
                        logger.warning(
                            "format_spec_retry_still_invalid",
                            file=file_path.name,
                        )
                        spec = None
                else:
                    # 重试失败，放弃此 spec
                    spec = None

            # 存入缓存库（仅存有效的）
            if spec and self._spec_library:
                self._spec_library.put(lines, source_format, spec, 1.0)

        return spec

    async def _llm_infer_spec(
        self,
        file_path: Path,
        text_sample: str,
        source_format: DocumentFormat,
        total_lines: int,
    ) -> FormatSpec | None:
        """单次 LLM FormatSpec 推断"""
        assert self.llm is not None

        prompt = FORMAT_SPEC_INFER_PROMPT.format(
            file_name=file_path.name,
            source_format=source_format.value,
            total_lines=total_lines,
            text_sample=text_sample,
        )

        t0 = time.monotonic()
        try:
            from mathforge.llm.config import LLMConfig

            llm_config = LLMConfig.for_task("split")
            result = await self.llm.generate_json(prompt, config=llm_config)
            latency = time.monotonic() - t0

            logger.info(
                "format_spec_infer_done",
                file=file_path.name,
                latency_sec=round(latency, 2),
            )

            format_spec_data = result.get("format_spec", result)
            spec = self._parse_format_spec(format_spec_data)

            if not spec.content_blocks:
                logger.warning(
                    "format_spec_empty_blocks",
                    file=file_path.name,
                )
                return None

            return spec

        except Exception:
            logger.error(
                "format_spec_infer_failed",
                file=file_path.name,
                exc_info=True,
            )
            return None

    async def _llm_infer_spec_retry(
        self,
        file_path: Path,
        text_sample: str,
        source_format: DocumentFormat,
        total_lines: int,
        failures: list[str],
    ) -> FormatSpec | None:
        """用失败信息重试一次 FormatSpec 推断"""
        assert self.llm is not None

        failure_text = "\n".join(f"- {f}" for f in failures[:5])
        retry_prompt = FORMAT_SPEC_INFER_PROMPT.format(
            file_name=file_path.name,
            source_format=source_format.value,
            total_lines=total_lines,
            text_sample=text_sample,
        ) + (
            f"\n\n--- 重要补充 ---\n"
            f"上一次推断的 FormatSpec 存在以下问题：\n{failure_text}\n"
            f"请修正这些 pattern，确保它们能匹配文档中的实际内容。"
        )

        try:
            from mathforge.llm.config import LLMConfig

            llm_config = LLMConfig.for_task("split")
            result = await self.llm.generate_json(retry_prompt, config=llm_config)
            format_spec_data = result.get("format_spec", result)
            spec = self._parse_format_spec(format_spec_data)
            if spec.content_blocks:
                logger.info("format_spec_retry_success", file=file_path.name)
                return spec
        except Exception:
            logger.error("format_spec_retry_failed", file=file_path.name, exc_info=True)
        return None

    @staticmethod
    def _validate_spec(spec: FormatSpec, lines: list[str]) -> tuple[bool, list[str]]:
        """验证 FormatSpec 的 pattern 是否能匹配实际文档内容

        返回 (is_valid, failure_descriptions)
        """
        if not spec.content_blocks:
            return False, ["content_blocks 为空"]

        # 用前 500 行测试
        test_text = "".join(lines[: min(500, len(lines))])
        failures: list[str] = []
        match_count = 0

        for bs in spec.content_blocks:
            try:
                pattern = re.compile(bs.open_pattern, re.MULTILINE)
                matches = list(pattern.finditer(test_text))
                if matches:
                    match_count += 1
                else:
                    failures.append(
                        f"pattern '{bs.open_pattern}' (type={bs.block_type}) 在前500行中无匹配"
                    )
            except re.error as e:
                failures.append(
                    f"pattern '{bs.open_pattern}' (type={bs.block_type}) 正则编译错误: {e}"
                )

        # 至少一半 pattern 要有匹配
        total = len(spec.content_blocks)
        is_valid = match_count >= max(total / 2, 1)

        return is_valid, failures

    # ── 确定性分块（用 FormatSpec）──

    async def _split_by_spec(
        self,
        file_path: Path,
        content: str,
        spec: FormatSpec,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[MdBlockV2, None]:
        """用 FormatSpec 的正则确定性拆分

        优先按 section_pattern 切分，否则按固定行数 + 语义边界切分。
        """
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        file_str = str(file_path)

        raw_metadata = self._extract_file_metadata(file_path, source_format)

        # 尝试按 section pattern 切分
        sections: list[tuple[int, int, str]] | None = None
        if spec.section_pattern:
            sections = self._split_by_section_pattern(lines, spec.section_pattern)

        if sections and len(sections) > 1:
            for sec_start, sec_end, sec_title in sections:
                block_content = "".join(lines[sec_start:sec_end])
                if not block_content.strip():
                    continue
                yield MdBlockV2(
                    block_id=str(uuid.uuid4()),
                    block_type=MdBlockType.TEXT,
                    content=block_content,
                    source_file=file_str,
                    source_format=source_format,
                    line_start=sec_start,
                    line_end=sec_end,
                    format_spec=spec,
                    section_title=sec_title,
                    raw_metadata=raw_metadata,
                )
        else:
            # 按固定行数切分 + 语义边界检测
            batch_size = self.config.pipeline_split_batch_lines
            start = 0
            while start < total_lines:
                end = min(start + batch_size, total_lines)
                end = self._find_semantic_boundary(lines, end, spec, block_start=start)
                block_content = "".join(lines[start:end])
                if block_content.strip():
                    yield MdBlockV2(
                        block_id=str(uuid.uuid4()),
                        block_type=MdBlockType.TEXT,
                        content=block_content,
                        source_file=file_str,
                        source_format=source_format,
                        line_start=start,
                        line_end=end,
                        format_spec=spec,
                        raw_metadata=raw_metadata,
                    )
                start = end

    def _split_by_section_pattern(
        self, lines: list[str], section_pattern: str
    ) -> list[tuple[int, int, str]]:
        """按 section pattern 切分，返回 (start, end, title) 列表"""
        try:
            pattern = re.compile(section_pattern, re.MULTILINE)
        except re.error:
            return []

        sections: list[tuple[int, int, str]] = []
        matches: list[tuple[int, str]] = []

        for i, line in enumerate(lines):
            m = pattern.match(line.rstrip())
            if m:
                title = m.group(1) if m.lastindex and m.lastindex >= 1 else line.strip()
                matches.append((i, title))

        for idx, (start, title) in enumerate(matches):
            end = matches[idx + 1][0] if idx + 1 < len(matches) else len(lines)
            sections.append((start, end, title))

        return sections

    def _find_semantic_boundary(
        self,
        lines: list[str],
        target_end: int,
        spec: FormatSpec,
        block_start: int = 0,
    ) -> int:
        """在 target_end 附近找到最佳语义切分点

        往前扫描最多 30 行，找到空行或 block open 标记作为切分点。
        保证返回值 > block_start，避免零进度循环。
        """
        max_lookback = 30
        # 不能退到 block_start 或更前，至少保留 1 行进度
        floor = block_start + 1

        for offset in range(min(max_lookback, target_end)):
            idx = target_end - offset
            if idx <= floor:
                break
            line = lines[idx - 1] if idx <= len(lines) else ""
            # 空行是好的切分点
            if not line.strip():
                return idx
            # block open 标记也是好的切分点（在前一行切）
            for bs in spec.content_blocks:
                try:
                    if re.match(bs.open_pattern, line.strip()):
                        return max(idx - 1, floor)
                except re.error:
                    pass

        return max(target_end, floor)

    # ── Fallback: 逐 chunk LLM 拆分 ──

    async def _split_by_llm(
        self,
        file_path: Path,
        content: str,
        source_format: DocumentFormat,
    ) -> AsyncGenerator[MdBlockV2, None]:
        """逐 chunk LLM 拆分（fallback 路径）"""
        assert self.llm is not None

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)
        file_str = str(file_path)
        batch_size = self.config.pipeline_split_batch_lines
        raw_metadata = self._extract_file_metadata(file_path, source_format)

        cursor = SplitCursor()

        while not cursor.is_finished and cursor.current_line < total_lines:
            start = cursor.current_line
            end = min(start + batch_size, total_lines)
            text_chunk = "".join(lines[start:end])

            prompt = BLOCK_SPLIT_PROMPT.format(
                start_line=start,
                end_line=end,
                text_chunk=text_chunk,
                previous_block_tail=cursor.previous_block_tail,
            )

            t0 = time.monotonic()
            try:
                from mathforge.llm.config import LLMConfig

                llm_config = LLMConfig.for_task("split")
                result = await self.llm.generate_json(prompt, config=llm_config)
                latency = time.monotonic() - t0

                logger.info(
                    "block_split_llm_call",
                    start_line=start,
                    end_line=end,
                    latency_sec=round(latency, 2),
                )
            except Exception:
                logger.error(
                    "block_split_llm_failed",
                    start_line=start,
                    end_line=end,
                    exc_info=True,
                )
                yield MdBlockV2(
                    block_id=str(uuid.uuid4()),
                    block_type=MdBlockType.TEXT,
                    content=text_chunk,
                    source_file=file_str,
                    source_format=source_format,
                    line_start=start,
                    line_end=end,
                    raw_metadata=raw_metadata,
                )
                cursor.current_line = end
                continue

            blocks_data = result.get("blocks", [])
            format_spec_data = result.get("format_spec", {})
            ends_mid = result.get("ends_mid_block", False)
            extend = result.get("suggested_extend_lines", 0)

            if ends_mid and extend > 0 and end < total_lines:
                new_end = min(end + extend, total_lines)
                text_chunk = "".join(lines[start:new_end])
                end = new_end

            spec = self._parse_format_spec(format_spec_data)

            if blocks_data:
                for bd in blocks_data:
                    block_content = bd.get("content", text_chunk)
                    yield MdBlockV2(
                        block_id=str(uuid.uuid4()),
                        block_type=MdBlockType.TEXT,
                        content=block_content,
                        source_file=file_str,
                        source_format=source_format,
                        line_start=bd.get("line_start", start),
                        line_end=bd.get("line_end", end),
                        format_spec=spec,
                        chapter_title=bd.get("chapter_title", ""),
                        section_title=bd.get("section_title", ""),
                        preceding_context=cursor.previous_block_tail,
                        raw_metadata=raw_metadata,
                    )
            else:
                yield MdBlockV2(
                    block_id=str(uuid.uuid4()),
                    block_type=MdBlockType.TEXT,
                    content=text_chunk,
                    source_file=file_str,
                    source_format=source_format,
                    line_start=start,
                    line_end=end,
                    format_spec=spec,
                    preceding_context=cursor.previous_block_tail,
                    raw_metadata=raw_metadata,
                )

            tail_lines = text_chunk.strip().splitlines()[-3:]
            cursor.previous_block_tail = "\n".join(tail_lines)
            cursor.current_line = end
            cursor.blocks_so_far += len(blocks_data) or 1

            if end >= total_lines:
                cursor.is_finished = True

    # ── 辅助方法 ──

    @staticmethod
    def _parse_format_spec(data: dict[str, Any]) -> FormatSpec:
        """从 LLM 输出解析 FormatSpec"""
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

        return FormatSpec(
            content_blocks=blocks,
            answer_format=answer,
            section_pattern=data.get("section_pattern"),
            source_type=data.get("source_type", ""),
            detected_structure=data.get("detected_structure", []),
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
        # 通用年份检测
        year_match = re.search(r"(19|20)\d{2}", name)
        if year_match:
            metadata["year"] = int(year_match.group())

        # 关键词类型提示
        for keyword, exam_type in [
            ("零试", "lingshi"),
            ("一试", "yishi"),
            ("女子", "nvzi"),
        ]:
            if keyword in name:
                metadata["exam_type"] = exam_type
                break

        return metadata
