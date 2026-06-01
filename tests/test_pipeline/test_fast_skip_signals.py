"""结构感知 fast-skip 弱信号（Phase 1 #2/#8）回归测试

strict 模式：无强标记时还须无弱信号才跳窗。弱信号宁可 false-positive
（多调一次 LLM），也不能漏（漏 = 静默丢条目）。
"""

from __future__ import annotations

import pytest

from sciforge.pipeline.stages.stage1_extract import (
    _ITEM_MARKER_RE,
    _WEAK_SIGNAL_RE,
    Stage1Extract,
)


def _window_empty_strict(text: str) -> bool:
    """复刻 stage1 strict 判定：无强标记且无弱信号 → 空（可跳）。"""
    if _ITEM_MARKER_RE.search(text):
        return False
    return _WEAK_SIGNAL_RE.search(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "定 义 3.1 设 f 连续",  # OCR 切开关键字 → 弱信号 3.1
        "xx义 3.1 设 f 连续",  # 关键字糊掉 → 编号 3.1
        "12. 求下列极限",  # 裸编号习题
        "3、计算定积分",  # 裸编号（顿号）
        "下列正确的是\n  A. x>0\n  B. x<0",  # 选择项
        "习题 3.2 解答：由定理...",  # 解答标记
        "## 3.2 一致连续性",  # 章节标题
        "函数 f 在 x=0 处\\underline{\\quad}连续",  # 填空
        "第三章 极限理论",  # 中文章节
        r"\begin{align} x &= 1 \end{align}",  # LaTeX 环境
    ],
)
def test_signals_block_skip(text: str) -> None:
    """这些含条目迹象的窗口绝不能被 strict fast-skip 跳过。"""
    assert _window_empty_strict(text) is False, f"会被静默跳过（漏条目风险）：{text!r}"


@pytest.mark.parametrize(
    "text",
    [
        "本章我们讨论连续函数的性质，它在分析学中非常重要。",
        "作者多年来在大学讲授数学分析课程，感谢同学支持。",
        "这是一段没有任何编号、选项或公式的纯过渡文字。",
    ],
)
def test_pure_prose_skippable(text: str) -> None:
    """纯过渡散文（无任何信号）可被 fast-skip，节省 LLM 调用。"""
    assert _window_empty_strict(text) is True


def test_clean_window_strips_prefix() -> None:
    win = ["     12 | **定义 3.1** 设...", "     13 |   A. 选项"]
    clean = Stage1Extract._clean_window_text(win)
    assert "12 |" not in clean
    # 去前缀后选择项 ^A 锚点可命中
    assert _WEAK_SIGNAL_RE.search(clean) is not None
