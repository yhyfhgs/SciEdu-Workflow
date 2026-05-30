"""Source genre 推断 — 根据 source_file 名启发式判定文档类型与典型难度区间。

用于在 Stage2Annotate 的 prompt 中注入 difficulty baseline hint，解决"首条条目
无 tag_tree 锚点导致 difficulty 偏软"的冷启动问题。

返回四元组 `(genre, label, low, high)`：
- genre: competition / exercise_book / textbook / general
- label: 中文标签（用于 prompt 提示）
- low, high: 典型 difficulty 区间（1-10）
"""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[str, str, str, int, int], ...] = (
    # (genre, label_cn, regex, low, high)
    # 顺序敏感：graduate_entrance 须在 competition 前面，因为"考研真题"同时含"真题"
    (
        "graduate_entrance",
        "考研真题",
        r"(考研|硕士|博士|研究生入学|postgraduate|graduate_entrance)",
        6,
        9,
    ),
    (
        "competition",
        "竞赛/真题",
        r"(真题|竞赛|奥赛|奥林匹克|imo|cmo|putnam|新领军|丘成桐|中学生|高联|华罗庚|袁氏)",
        7,
        10,
    ),
    (
        "exercise_book",
        "习题集",
        r"(习题|题集|exercise|problem_set|solutions?|problems?)",
        4,
        7,
    ),
    (
        "textbook",
        "教材",
        r"(教材|textbook|讲义|lecture_notes|notes|chapter|course)",
        2,
        6,
    ),
)

_DEFAULT: tuple[str, str, int, int] = ("general", "未知", 3, 8)


def infer_genre(source_file: str) -> tuple[str, str, int, int]:
    """从 source_file 路径推断文档 genre 与典型 difficulty 区间。

    Args:
        source_file: 源文件路径或名称

    Returns:
        (genre, label_cn, low, high)
    """
    if not source_file:
        return _DEFAULT
    lower = source_file.lower()
    for genre, label, pattern, low, high in _PATTERNS:
        if re.search(pattern, lower, flags=re.IGNORECASE) or re.search(pattern, source_file):
            return genre, label, low, high
    return _DEFAULT


def format_baseline_hint(source_file: str) -> str:
    """生成一段可插入 prompt 的 baseline 提示文本。

    空字符串 source_file 或无法识别 → 返回空串（不注入）。
    """
    genre, label, low, high = infer_genre(source_file)
    if genre == "general":
        return ""
    return (
        f"## 文档类型 baseline\n"
        f"本 source 文件路径推断为 **{label}**（genre={genre}），"
        f"典型 difficulty 区间为 **{low}-{high}**；"
        f"除非条目明显超出该档位（如教材里嵌入的竞赛题），否则 difficulty 应落在此区间内。\n"
    )


__all__ = ["infer_genre", "format_baseline_hint"]
