"""跨页知识点检测器 — 基于有限状态机"""

from __future__ import annotations

import re
from enum import Enum

from mathforge.core.models import MdBlock, MdBlockType, SpanningBlock


class BlockState(str, Enum):
    IDLE = "idle"
    IN_DEFINITION = "definition"
    IN_THEOREM = "theorem"
    IN_PROOF = "proof"
    IN_EXAMPLE = "example"
    IN_SOLUTION = "solution"
    IN_EXERCISE = "exercise"


# 开启标记
BLOCK_OPENERS: dict[BlockState, re.Pattern[str]] = {
    BlockState.IN_DEFINITION: re.compile(r"定义\s*(\d[\d.]*)"),
    BlockState.IN_THEOREM: re.compile(r"(?:定理|命题|引理|推论)\s*(\d[\d.]*)"),
    BlockState.IN_PROOF: re.compile(r"^(?:证明|证)\s", re.MULTILINE),
    BlockState.IN_EXAMPLE: re.compile(r"例\s*(\d[\d.]*)"),
    BlockState.IN_SOLUTION: re.compile(r"^(?:解|解答)\s", re.MULTILINE),
    BlockState.IN_EXERCISE: re.compile(r"^习题", re.MULTILINE),
}

# 关闭标记
BLOCK_CLOSERS: dict[BlockState, re.Pattern[str]] = {
    BlockState.IN_PROOF: re.compile(r"[□■▪]|得证|证毕"),
    BlockState.IN_SOLUTION: re.compile(r"[□■▪]"),
}

# 隐式关闭: 任何新块开启会关闭当前块
IMPLICIT_CLOSER = re.compile(
    r"(?:定义|定理|命题|引理|推论|例|习题|§\d|第.{1,3}章)"
)

STATE_TO_BLOCK_TYPE: dict[BlockState, MdBlockType] = {
    BlockState.IN_DEFINITION: MdBlockType.DEFINITION,
    BlockState.IN_THEOREM: MdBlockType.THEOREM,
    BlockState.IN_PROOF: MdBlockType.PROOF,
    BlockState.IN_EXAMPLE: MdBlockType.EXAMPLE,
    BlockState.IN_SOLUTION: MdBlockType.EXERCISE,
    BlockState.IN_EXERCISE: MdBlockType.EXERCISE,
}


class CrossPageDetector:
    """跨页知识点 FSM 检测器"""

    MAX_SPAN_PAGES = 5

    def __init__(self, max_span_pages: int = 5):
        self.MAX_SPAN_PAGES = max_span_pages
        self.state = BlockState.IDLE
        self.current_block: SpanningBlock | None = None
        self.completed_blocks: list[SpanningBlock] = []

    def reset(self) -> None:
        self.state = BlockState.IDLE
        self.current_block = None
        self.completed_blocks = []

    def process_page(self, text: str, page_num: int) -> list[MdBlock]:
        """处理单页文本，返回在此页完成的块"""
        completed_on_this_page: list[MdBlock] = []
        lines = text.split("\n")

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                if self.current_block:
                    self.current_block.content += "\n"
                continue

            # 检查显式关闭标记
            if self.state != BlockState.IDLE and self.state in BLOCK_CLOSERS:
                closer = BLOCK_CLOSERS[self.state]
                if closer.search(line_stripped):
                    if self.current_block:
                        self.current_block.content += line + "\n"
                        self.current_block.page_end = page_num
                        self.current_block.is_complete = True
                        self.current_block.close_marker = line_stripped
                        completed_on_this_page.append(self._finalize_block())
                    self.state = BlockState.IDLE
                    continue

            # 检查新块开启（隐式关闭当前块）
            new_state = self._detect_opener(line_stripped)
            if new_state:
                # 关闭当前块
                if self.current_block:
                    self.current_block.page_end = page_num
                    self.current_block.is_complete = True
                    completed_on_this_page.append(self._finalize_block())

                # 开启新块
                self.state = new_state
                number = self._extract_number(new_state, line_stripped)
                self.current_block = SpanningBlock(
                    block_type=STATE_TO_BLOCK_TYPE.get(new_state, MdBlockType.TEXT),
                    number=number,
                    content=line + "\n",
                    page_start=page_num,
                    page_end=page_num,
                    open_marker=line_stripped[:50],
                )
                continue

            # 在块内：追加内容
            if self.current_block:
                self.current_block.content += line + "\n"
                self.current_block.page_end = page_num

                # 检查是否超过最大跨页数
                span = self.current_block.page_end - self.current_block.page_start + 1
                if span > self.MAX_SPAN_PAGES:
                    self.current_block.is_complete = False
                    completed_on_this_page.append(self._finalize_block())
                    self.state = BlockState.IDLE

        return completed_on_this_page

    def flush(self) -> list[MdBlock]:
        """处理结束时，刷出未关闭的块"""
        result: list[MdBlock] = []
        if self.current_block:
            self.current_block.is_complete = self.state == BlockState.IDLE
            result.append(self._finalize_block())
            self.state = BlockState.IDLE
        return result

    def _detect_opener(self, line: str) -> BlockState | None:
        for state, pattern in BLOCK_OPENERS.items():
            if pattern.search(line):
                return state
        return None

    def _extract_number(self, state: BlockState, line: str) -> str | None:
        pattern = BLOCK_OPENERS.get(state)
        if pattern:
            m = pattern.search(line)
            if m and m.groups():
                return m.group(1)
        return None

    def _finalize_block(self) -> MdBlock:
        """将 SpanningBlock 转为 MdBlock"""
        sb = self.current_block
        assert sb is not None
        block = MdBlock(
            block_type=sb.block_type,
            number=sb.number,
            name=sb.name,
            content=sb.content.strip(),
            source_file="",
            page_start=sb.page_start,
            page_end=sb.page_end,
        )
        self.current_block = None
        return block
