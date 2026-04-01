"""MetaExtractor — spec 驱动的通用条目提取引擎

读入 MdBlockV2 + 其 FormatSpec，按正则规则机械提取 RawItem。
不依赖 LLM，纯 Python 确定性逻辑。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from mathforge.core.models import FormatSpec, RawItem

if TYPE_CHECKING:
    from mathforge.core.models import MdBlockV2

logger = structlog.get_logger()


class MetaExtractor:
    """spec 驱动的通用提取引擎"""

    def extract(self, block: MdBlockV2) -> list[RawItem]:
        """从一个 MdBlockV2 中提取所有 RawItem

        根据 FormatSpec 中的 content_blocks 规则，使用两种模式提取：
        - 模式 A（显式边界）：open_pattern + close_pattern
        - 模式 B（隐式边界）：仅 open_pattern，到下一个 open 为止
        """
        if not block.format_spec or not block.format_spec.content_blocks:
            logger.warning("meta_extractor_no_spec", block_id=block.block_id)
            return self._fallback_extract(block)

        spec = block.format_spec
        content = block.content
        lines = content.splitlines(keepends=True)

        # 收集所有 skip patterns
        skip_patterns = [
            re.compile(p) for p in spec.skip_line_patterns if p
        ]

        # 过滤 skip 行（保留行号映射）
        filtered_lines: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            if not any(sp.search(line) for sp in skip_patterns):
                filtered_lines.append((i, line))

        filtered_text = "".join(line for _, line in filtered_lines)

        # 按两种模式分别提取（使用全局序号避免 ID 冲突）
        all_items: list[RawItem] = []
        _seq = 0

        for block_spec in spec.content_blocks:
            try:
                if block_spec.close_pattern:
                    items = self._extract_explicit(
                        block_spec, filtered_text, filtered_lines, block, spec, _seq
                    )
                else:
                    items = self._extract_implicit(
                        block_spec, filtered_text, filtered_lines, block, spec, _seq
                    )
                _seq += len(items)
                all_items.extend(items)
            except re.error as e:
                logger.error(
                    "meta_extractor_regex_error",
                    block_type=block_spec.block_type,
                    pattern=block_spec.open_pattern,
                    error=str(e),
                )

        # 排序：按 line_start
        all_items.sort(key=lambda x: x.line_start)

        # 配对逻辑
        self._pair_items(all_items, spec)

        logger.info(
            "items_extracted",
            block_id=block.block_id,
            item_count=len(all_items),
            source_file=block.source_file,
        )

        if not all_items:
            return self._fallback_extract(block)

        return all_items

    def _extract_explicit(
        self,
        block_spec,
        text: str,
        filtered_lines: list[tuple[int, str]],
        block: MdBlockV2,
        spec: FormatSpec,
        seq_start: int = 0,
    ) -> list[RawItem]:
        """模式 A：显式边界（open + close pattern）"""
        items: list[RawItem] = []
        open_re = re.compile(block_spec.open_pattern, re.MULTILINE)
        close_re = re.compile(block_spec.close_pattern, re.MULTILINE)  # type: ignore[arg-type]

        for open_match in open_re.finditer(text):
            close_match = close_re.search(text, open_match.end())
            if not close_match:
                content_raw = text[open_match.start() : ]
                end_pos = len(text)
            else:
                content_raw = text[open_match.start() : close_match.end()]
                end_pos = close_match.end()

            number = self._capture_group(open_match, block_spec.number_group)
            title = self._capture_group(open_match, block_spec.title_group)

            line_start = text[:open_match.start()].count("\n") + block.line_start
            line_end = text[:end_pos].count("\n") + block.line_start

            items.append(
                RawItem(
                    item_id=f"raw-{block.block_id[:8]}-{seq_start + len(items):03d}",
                    block_type=block_spec.block_type,
                    number=number,
                    title=title,
                    content_raw=content_raw.strip(),
                    content_latex=content_raw.strip(),
                    line_start=line_start,
                    line_end=line_end,
                    source_block_id=block.block_id,
                    source_file=block.source_file,
                    extraction_mode="explicit",
                    raw_metadata=block.raw_metadata,
                )
            )

        return items

    def _extract_implicit(
        self,
        block_spec,
        text: str,
        filtered_lines: list[tuple[int, str]],
        block: MdBlockV2,
        spec: FormatSpec,
        seq_start: int = 0,
    ) -> list[RawItem]:
        """模式 B：隐式边界（仅 open pattern，到下一个 open 为止）"""
        items: list[RawItem] = []
        open_re = re.compile(block_spec.open_pattern, re.MULTILINE)

        # 找到所有 open 标记的位置
        all_opens = list(open_re.finditer(text))
        if not all_opens:
            return items

        # 收集所有 block_spec 的 open patterns，用于检测下一个任意类型的 open
        all_open_patterns = []
        for bs in spec.content_blocks:
            try:
                all_open_patterns.append(re.compile(bs.open_pattern, re.MULTILINE))
            except re.error:
                pass

        for i, open_match in enumerate(all_opens):
            start_pos = open_match.start()

            # 找下一个边界：当前类型的下一个 open，或者其他类型的 open
            end_pos = len(text)
            for pattern in all_open_patterns:
                next_match = pattern.search(text, open_match.end())
                if next_match and next_match.start() < end_pos:
                    end_pos = next_match.start()

            content_raw = text[start_pos:end_pos].strip()

            number = self._capture_group(open_match, block_spec.number_group)
            title = self._capture_group(open_match, block_spec.title_group)

            line_start = text[:start_pos].count("\n") + block.line_start
            line_end = text[:end_pos].count("\n") + block.line_start

            items.append(
                RawItem(
                    item_id=f"raw-{block.block_id[:8]}-{seq_start + len(items):03d}",
                    block_type=block_spec.block_type,
                    number=number,
                    title=title,
                    content_raw=content_raw,
                    content_latex=content_raw,
                    line_start=line_start,
                    line_end=line_end,
                    source_block_id=block.block_id,
                    source_file=block.source_file,
                    extraction_mode="implicit",
                    raw_metadata=block.raw_metadata,
                )
            )

        return items

    @staticmethod
    def _capture_group(match: re.Match, group_index: int | None) -> str | None:
        """安全提取捕获组"""
        if group_index is None:
            return None
        try:
            return match.group(group_index)
        except (IndexError, AttributeError):
            return None

    @staticmethod
    def _pair_items(items: list[RawItem], spec: FormatSpec) -> None:
        """根据 is_paired_with 字段配对条目

        例: solution → 最近的前一个 problem
             proof → 最近的前一个 theorem/lemma/corollary
        """
        pair_map: dict[str, str] = {}
        for bs in spec.content_blocks:
            if bs.is_paired_with:
                pair_map[bs.block_type] = bs.is_paired_with

        # 记录每种类型的最后一个 item
        last_of_type: dict[str, str] = {}

        for item in items:
            target_type = pair_map.get(item.block_type)
            if target_type and target_type in last_of_type:
                item.paired_item_id = last_of_type[target_type]

            last_of_type[item.block_type] = item.item_id

    def _fallback_extract(self, block: MdBlockV2) -> list[RawItem]:
        """当 MetaExtractor 无法提取时，回退到旧 parser 逻辑"""
        logger.warning(
            "meta_extractor_fallback",
            block_id=block.block_id,
            source_file=block.source_file,
        )
        # 将整个 block 作为一个 RawItem 返回
        return [
            RawItem(
                item_id=f"raw-{block.block_id[:8]}-fallback",
                block_type=(
                    block.block_type.value
                    if hasattr(block.block_type, "value")
                    else str(block.block_type)
                ),
                number=block.number,
                title=block.name,
                content_raw=block.content,
                content_latex=block.content,
                line_start=block.line_start,
                line_end=block.line_end,
                source_block_id=block.block_id,
                source_file=block.source_file,
                extraction_mode="implicit",
                raw_metadata=block.raw_metadata,
            )
        ]
