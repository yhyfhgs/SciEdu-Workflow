"""REC-14 — 内容体裁探测 detect_genre（书本/试卷/讲义）。"""

from __future__ import annotations

from sciforge.pipeline.source_classifier import detect_genre, genre_label


def test_detect_exam_paper() -> None:
    md = (
        "一、选择题（每题5分）\n"
        "1. 下列哪个是素数？\nA. 4\nB. 6\nC. 7\nD. 9\n"
        "2. 计算 1+1=? （3分）\n"
        "二、填空题\n参考答案\n"
    )
    assert detect_genre(md) == "exam_paper"


def test_detect_book() -> None:
    md = (
        "第一章 极限\n"
        "**定义 1.1** 设 f 在 x0 连续……\n"
        "**定理 1.2** 介值定理……\n"
        "**例 1.3** 求极限……\n"
    )
    assert detect_genre(md) == "book"


def test_detect_lecture() -> None:
    md = "第 3 讲 连续性\n本讲我们讨论……\n课件 slide 2：直观理解\n本节课的重点是……\n讲义补充：\n"
    assert detect_genre(md) == "lecture"


def test_detect_unknown_on_weak_signal() -> None:
    assert detect_genre("一些没有结构信号的纯文本段落。") == "unknown"
    assert detect_genre("") == "unknown"


def test_genre_label() -> None:
    assert genre_label("exam_paper") == "试卷"
    assert genre_label("book") == "书本/教材"
    assert genre_label("lecture") == "讲义"
    assert genre_label("unknown") == "未知"
