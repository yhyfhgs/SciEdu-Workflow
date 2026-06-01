"""文档分段器 + 保序重排（Phase 6 #1 单文档内并行提取的可测核心）

把一个文档的全量行按 **section 边界** 切成互不重叠、覆盖全部行的独立段，
每段可交给独立 worker 并行提取。提取后用 **(段序, 段内序)** 重排回文档顺序，
赋全局 ``order_index``。

设计要点（保证你的最高优先级"不漏提取"）：
- **覆盖不变量**：所有段的行范围拼接 == 整个文档行集（无遗漏、无重叠、连续）。
  ``segment_document`` 末尾 ``assert`` 强制此性质。
- **段是 item 的天然容器**：在 section 边界切分，条目几乎不会跨段（章节标题分隔条目），
  因此段内沿用现有滑窗提取逻辑即可，段间相互独立 → 可并行。
- **保序重排**：``assign_order_index`` 按 (段序, 段内序) 展平赋号，并 ``assert`` 输出条目数
  == 输入条目总数（不漏、不重、不乱序）。

纯函数、无 LLM、无 IO，完全可测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DocSegment:
    """文档的一个独立段（并行提取单元）"""

    ordinal: int  # 段序：0-based，文档顺序（保序基准）
    start_idx: int  # 在 lines 列表中的起始下标（0-based, inclusive）
    end_idx: int  # 结束下标（0-based, exclusive）
    title: str = ""  # 段标题（来自 section 边界，可空）
    lines: list[str] = field(default_factory=list)

    @property
    def line_count(self) -> int:
        return self.end_idx - self.start_idx


def segment_document(
    lines: list[str],
    boundary_indices: list[int],
    *,
    min_lines: int = 40,
    max_lines: int = 400,
) -> list[DocSegment]:
    """把 ``lines`` 按 section 边界切成覆盖全部行的独立段。

    Args:
        lines: 文档全量行（任意格式；分段器不解析内容）。
        boundary_indices: section 起始行的 0-based 下标列表（升序，文档全局）。
        min_lines: 过小段合并阈值（< 此值并入前一段，避免碎片化）。
        max_lines: 过大段硬切阈值（> 此值按 max_lines 切分，控制单 worker 负载）。

    Returns:
        段列表（按 ordinal 升序）。**保证连续覆盖 ``[0, len(lines))``、无重叠。**
        空文档返回 ``[]``。

    Raises:
        AssertionError: 覆盖不变量被破坏（不应发生；用于防御"漏行"）。
    """
    n = len(lines)
    if n == 0:
        return []

    # 1. 归一化切分点：去重、排序、丢弃越界/0（0 是隐含起点）
    cuts = sorted({i for i in boundary_indices if 0 < i < n})
    starts = [0, *cuts]

    # 2. 原始段 [start, next_start)
    raw: list[tuple[int, int]] = []
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else n
        if e > s:
            raw.append((s, e))

    # 3. 合并过小段：**前向累积**——连续累积直到 ≥ min_lines 才闭合，
    #    这样开头的小段（无前段可并）也会向后累积，不会卡成碎片。
    merged: list[tuple[int, int]] = []
    cur_s: int | None = None
    cur_e: int = 0
    for s, e in raw:
        if cur_s is None:
            cur_s, cur_e = s, e
        else:
            cur_e = e  # 向后扩展
        if (cur_e - cur_s) >= min_lines:
            merged.append((cur_s, cur_e))
            cur_s = None
    if cur_s is not None:  # 剩余尾段
        if merged and (cur_e - cur_s) < min_lines:
            ps, _ = merged[-1]
            merged[-1] = (ps, cur_e)  # 不足阈值 → 并入前一段
        else:
            merged.append((cur_s, cur_e))

    # 4. 拆分过大段（按 max_lines 硬切）
    split: list[tuple[int, int]] = []
    for s, e in merged:
        if (e - s) > max_lines:
            cur = s
            while cur < e:
                nxt = min(cur + max_lines, e)
                split.append((cur, nxt))
                cur = nxt
        else:
            split.append((s, e))

    # 5. 构建 DocSegment + 标题映射
    boundary_set = set(cuts)
    segments: list[DocSegment] = []
    for ordinal, (s, e) in enumerate(split):
        title = ""
        if s in boundary_set or s == 0:
            # 取该段首行作为标题线索（去掉行号前缀）
            head = lines[s]
            title = head.split("|", 1)[-1].strip()[:60] if "|" in head else head.strip()[:60]
        segments.append(
            DocSegment(
                ordinal=ordinal,
                start_idx=s,
                end_idx=e,
                title=title,
                lines=lines[s:e],
            )
        )

    # 6. 覆盖不变量断言（防"漏行"——你的最高优先级）
    assert segments, "分段结果不应为空（n>0）"
    assert segments[0].start_idx == 0, "首段必须从第 0 行起"
    assert segments[-1].end_idx == n, f"末段必须到第 {n} 行止，实际 {segments[-1].end_idx}"
    total = 0
    for i, seg in enumerate(segments):
        total += seg.line_count
        if i + 1 < len(segments):
            nxt = segments[i + 1].start_idx
            assert seg.end_idx == nxt, f"段{i}末{seg.end_idx}≠段{i + 1}首{nxt} → 漏行"
    assert total == n, f"段覆盖行数 {total} != 文档行数 {n} → 漏行/重叠"
    return segments


def assign_order_index(
    per_segment_items: list[list[Any]],
    *,
    start: int = 0,
) -> list[tuple[int, Any]]:
    """按 (段序, 段内序) 把并行提取的各段条目重排回文档顺序并赋全局 order_index。

    Args:
        per_segment_items: 外层按**段序**（与 DocSegment.ordinal 对齐），
            内层按**段内提取顺序**的条目列表。
        start: 全局 order_index 起点（目录并发时可传偏移）。

    Returns:
        ``[(order_index, item), ...]``，order_index 从 start 连续递增。

    Raises:
        AssertionError: 输出条目数 != 输入总数（防"漏/重"）。
    """
    out: list[tuple[int, Any]] = []
    oi = start
    for seg_items in per_segment_items:  # 已按段序
        for item in seg_items:  # 已按段内序
            out.append((oi, item))
            oi += 1

    expected = sum(len(s) for s in per_segment_items)
    assert len(out) == expected, f"重排后条目数 {len(out)} != 输入总数 {expected} → 漏/重"
    # order_index 严格连续递增
    assert all(out[i][0] + 1 == out[i + 1][0] for i in range(len(out) - 1)), (
        "order_index 必须严格连续递增"
    )
    return out
