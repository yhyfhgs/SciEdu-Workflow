"""AdaptiveSampler — 自适应文档采样

替代硬编码采样策略，根据文档长度和结构特征自动选择采样点：
- 头部固定采样
- 检测结构转折点（空行密度突变、标题/块标记密度变化）
- 每个转折点周围采样
- 尾部固定采样（捕获答案区、附录等）
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mathforge.core.models import DocumentFormat

# 常见数学文档结构标记
_STRUCTURAL_PATTERN = re.compile(
    r"^\s*(?:"
    r"\\(?:begin|end|section|subsection|chapter|paragraph|item|newpage|pagebreak)"
    r"|#{1,4}\s"
    r"|(?:定义|定理|引理|推论|命题|例|证明|解|习题|练习)\s*[\d.]*"
    r"|(?:Definition|Theorem|Lemma|Corollary|Proposition|Example|Proof|Exercise)"
    r"|\d+[\.\)]\s"
    r")",
    re.IGNORECASE,
)

# 采样预算常量
_HEAD_LINES = 100
_TAIL_LINES = 40
_CONTEXT_RADIUS = 20  # 每个转折点上下各取 20 行
_MAX_TRANSITIONS = 3
_MAX_TOTAL_LINES = 400
_WINDOW_SIZE = 30  # 密度计算窗口


@dataclass(frozen=True)
class SampleRegion:
    """一个采样区间"""

    start: int
    end: int
    label: str


class AdaptiveSampler:
    """自适应文档采样器"""

    def sample(
        self,
        lines: list[str],
        source_format: DocumentFormat = DocumentFormat.MARKDOWN,
    ) -> str:
        """从文档行列表中自适应采样，返回拼接后的文本"""
        total = len(lines)
        if total == 0:
            return ""

        # 短文档直接返回全部
        if total <= _MAX_TOTAL_LINES:
            return "".join(lines)

        regions = self._plan_regions(lines, total)
        return self._assemble(lines, regions, total)

    def _plan_regions(self, lines: list[str], total: int) -> list[SampleRegion]:
        """规划采样区间"""
        regions: list[SampleRegion] = []

        # 1. 头部
        head_end = min(_HEAD_LINES, total)
        regions.append(SampleRegion(0, head_end, "head"))

        # 2. 检测转折点并采样
        transitions = self._detect_transitions(lines, total)
        # 排除已在头部/尾部范围内的转折点
        tail_start = max(total - _TAIL_LINES, head_end)
        valid_transitions = [
            t
            for t in transitions
            if t > head_end + _CONTEXT_RADIUS and t < tail_start - _CONTEXT_RADIUS
        ][:_MAX_TRANSITIONS]

        for tp in valid_transitions:
            r_start = max(tp - _CONTEXT_RADIUS, head_end)
            r_end = min(tp + _CONTEXT_RADIUS, tail_start)
            regions.append(SampleRegion(r_start, r_end, f"transition@{tp}"))

        # 3. 尾部
        if tail_start > head_end:
            regions.append(SampleRegion(tail_start, total, "tail"))

        return regions

    def _detect_transitions(self, lines: list[str], total: int) -> list[int]:
        """检测结构转折点

        通过滑动窗口计算结构标记密度，找到密度变化最大的位置。
        """
        if total < _WINDOW_SIZE * 3:
            # 文档太短，取中点
            return [total // 2]

        # 计算每个窗口的结构标记密度
        densities: list[float] = []
        for i in range(0, total - _WINDOW_SIZE + 1, _WINDOW_SIZE // 2):
            window = lines[i : i + _WINDOW_SIZE]
            markers = sum(1 for line in window if _STRUCTURAL_PATTERN.match(line))
            blanks = sum(1 for line in window if not line.strip())
            # 密度 = 结构标记占比 + 空行占比变化权重
            densities.append((markers + blanks * 0.3) / _WINDOW_SIZE)

        if len(densities) < 2:
            return [total // 2]

        # 计算密度变化量（一阶差分绝对值）
        diffs: list[tuple[float, int]] = []
        step = _WINDOW_SIZE // 2
        for i in range(1, len(densities)):
            diff = abs(densities[i] - densities[i - 1])
            line_pos = i * step + _WINDOW_SIZE // 2
            diffs.append((diff, line_pos))

        # 按变化量排序，取 top-N
        diffs.sort(key=lambda x: x[0], reverse=True)

        # 确保转折点间有足够间距（至少 2 * CONTEXT_RADIUS）
        result: list[int] = []
        min_gap = _CONTEXT_RADIUS * 2
        for _, pos in diffs:
            if pos >= total:
                continue
            if all(abs(pos - existing) >= min_gap for existing in result):
                result.append(pos)
            if len(result) >= _MAX_TRANSITIONS:
                break

        result.sort()
        return result

    def _assemble(
        self,
        lines: list[str],
        regions: list[SampleRegion],
        total: int,
    ) -> str:
        """将采样区间拼接为文本，插入省略标记"""
        # 合并重叠区间
        merged = self._merge_regions(regions)

        # 截断到预算
        merged = self._trim_to_budget(merged, total)

        parts: list[str] = []
        prev_end = 0

        for region in merged:
            if region.start > prev_end:
                parts.append(f"\n... (省略第 {prev_end + 1}~{region.start} 行) ...\n\n")
            parts.append("".join(lines[region.start : region.end]))
            prev_end = region.end

        if prev_end < total:
            parts.append(f"\n... (省略第 {prev_end + 1}~{total} 行) ...\n")

        return "".join(parts)

    @staticmethod
    def _merge_regions(regions: list[SampleRegion]) -> list[SampleRegion]:
        """合并重叠/相邻的采样区间"""
        if not regions:
            return []
        sorted_regions = sorted(regions, key=lambda r: r.start)
        merged = [sorted_regions[0]]
        for region in sorted_regions[1:]:
            last = merged[-1]
            if region.start <= last.end:
                merged[-1] = SampleRegion(
                    last.start,
                    max(last.end, region.end),
                    f"{last.label}+{region.label}",
                )
            else:
                merged.append(region)
        return merged

    @staticmethod
    def _trim_to_budget(regions: list[SampleRegion], total: int) -> list[SampleRegion]:
        """确保总采样行数不超过预算"""
        current_total = sum(r.end - r.start for r in regions)
        if current_total <= _MAX_TOTAL_LINES:
            return regions

        # 优先保留头尾，缩减转折点区间
        result: list[SampleRegion] = []
        budget = _MAX_TOTAL_LINES
        for region in regions:
            size = region.end - region.start
            if budget <= 0:
                break
            if size > budget:
                result.append(SampleRegion(region.start, region.start + budget, region.label))
                budget = 0
            else:
                result.append(region)
                budget -= size
        return result
