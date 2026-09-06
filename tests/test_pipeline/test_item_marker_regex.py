"""_ITEM_MARKER_RE — Phase 2.5 fast-skip 触发器的 regex 覆盖测试

重要：宁可 false-positive（浪费一次 LLM 调用，无害），
也不能 false-negative（漏条目，上游滑窗过去永久丢失）。
"""

from __future__ import annotations

import pytest

from sciforge.pipeline.stages.stage1_extract import _ITEM_MARKER_RE

# ── 必须匹配的条目格式（true positive）───────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # 中文 OCR 教材：全角句号 + 空格
        "定义 1．2．2 设 $f: X \\to Y$",
        "定理 3．1．1 对任一分划",
        # 中文 OCR 教材：全角句号连写（无空格）
        "定理1．1．2．[戴德金分割定理]",
        "例1．2．7 求 $y=f(x)$",
        # 中文 OCR 教材：半角句号
        "定义 1.1.1 ｜分划｜设 $S$ 是",
        "定理 1.2.3 存在性",
        # 中文 markdown 粗体
        "**定义 3.1** 若 $f$ 在",
        "**定理 3.2** 设 $f$ 连续",
        "**例 3.3** 计算",
        "**习题 1** 证明",
        # 例题多种形式
        "例 1．2．8 设 $y=f(x)$",
        "例题 3.5 计算",
        "例1",  # 最简
        # 其他关键字
        "引理 2.1 对任意",
        "推论 1.3 由定理",
        "命题 4.2 若",
        "性质 1.4 函数 $f$",
        "公式 5.1",
        "注 2.1 以上定义",
        "注记 3.2",
        # LaTeX 环境
        r"\begin{theorem}",
        r"\begin{definition}",
        r"\begin{example}",
        r"\begin{lemma}",
        r"\begin{corollary}",
        r"\begin{problem}",
        r"\begin{exercise}",
        # 英文编号
        "Theorem 3.1 Let $f$",
        "Definition 2.1 A map is called",
        "Lemma 1.3",
        "Example 5.2",
        "Exercise 1.1",
    ],
)
def test_must_match(text: str) -> None:
    """这些文本必须被 regex 匹配到，否则 fast-skip 会漏条目"""
    assert _ITEM_MARKER_RE.search(text) is not None, (
        f"未匹配实际条目格式，fast-skip 会漏过：{text!r}"
    )


# ── 不应匹配的普通文本（true negative）───────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # 章节编号（不是条目）
        "1.1.1 函数的定义",
        "1.2 连续性",
        "§2．1 序列极限的定义",
        "第一章 极限",
        # Markdown heading
        "## 1.1 预备知识",
        "### 1.2 反函数",
        # 普通正文
        "本书是综合性大学的教材",
        "这里 $f(x)$ 满足单调性",
        "反函数 $x=f^{-1}(y)$ 的定义域",
        "在中学数学中，我们曾经学习过",
        # 非数字后缀（关键字后无数字）
        "习题一",
        "定义 A",  # A 不匹配 \d|\uff10-\uff19|X，但我们规定也接受 X
        # 误看起来像条目但无后续编号
        "例如函数 $y=\\sin x$",
        "比例关系",
        "定性分析",
    ],
)
def test_must_not_match(text: str) -> None:
    """这些不应被 regex 匹配（对应 fast-skip 会跳过，节省 LLM 调用）"""
    # 注意：我们的策略是宁可 false-positive，所以这些 test 较保守
    # （只检查明确的非条目文本）
    assert _ITEM_MARKER_RE.search(text) is None, f"误匹配了非条目文本，会浪费 LLM 调用：{text!r}"


# ── 整段文本测试：确保能从 500 行窗口中找到标记 ──────────────


def test_real_ocr_sample_detects_items() -> None:
    """模拟实际 OCR 后的数学教材窗口文本"""
    sample = """
和 $x_{2}$ ，在 $\\mathbb{R}$ 中与之对应的两个数 $f\\left(x_{1}\\right)$

定义 1．2．2 设 $f: X \\rightarrow Y$ 是一个一一对应。定义函数 $g: Y \\rightarrow X$
如下：对任意的 $y \\in Y$ ，函数值 $g(y)$ 规定为

反函数 $x=f^{-1}(y)$ 的定义域和值域恰为

在中学数学中，我们曾经学习过反三角函数。

例1．2．7 求 $y=f(x)=\\frac{\\mathrm{e}^{x}-\\mathrm{e}^{-x}}{2}$ 的反函数

本题中的函数 $f(x)$ 称为双曲正弦函数

例 1．2．8 设 $y=f(x)$ 为定义在 $X$ 上的一个函数
"""
    matches = _ITEM_MARKER_RE.findall(sample)
    # 应该至少匹配：定义 1、例1、例 1
    assert len(matches) >= 3, f"样本漏了条目标记，匹配数 {len(matches)}"


def test_pure_preface_no_match() -> None:
    """纯前言无条目，应返回 None（允许 fast-skip）"""
    preface = """
本书是综合性大学和高等师范院校数学系本科生数学分析课程的教材。

全书共分三册。第一册共六章，内容为函数、序列的极限、函数的极限与连续性。

作者多年来在北京大学为本科生讲授数学分析课程。

感谢各位老师和同学的帮助与支持。
"""
    assert _ITEM_MARKER_RE.search(preface) is None, "纯前言不应匹配到条目"
