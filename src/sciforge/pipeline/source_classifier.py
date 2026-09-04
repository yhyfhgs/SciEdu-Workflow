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


# ── 内容体裁探测（REC-14）：书本 / 试卷 / 讲义 ──
# 文件名启发式（infer_genre）只在路径含关键词时有效；本函数从**正文结构**判别体裁，
# 任意文件内容驱动，供下游差异化处理（试卷放宽 near-tail/卷末密集短题、讲义放宽 fast-skip）。

_EXAM_SIGNAL_RE = re.compile(
    r"[（(]\s*\d+\s*分\s*[)）]"  # 分值标注 "（5分）"
    r"|参考答案|答案与解析|评分标准|本卷|满分\s*\d+|考试时间"
    r"|一[、.]\s*(选择题|填空题|解答题|判断题)"  # 大题分组
)
_EXAM_CHOICE_RE = re.compile(r"^\s*[A-D][.、)]\s+", re.MULTILINE)  # A. B. C. D. 选项行
_BOOK_SIGNAL_RE = re.compile(
    r"\*\*\s*(定义|定理|引理|推论|命题|例)\s*[\d.]+\s*\*\*"  # **定义 1.1**
    r"|第\s*[一二三四五六七八九十百\d]+\s*章"
    r"|\\begin\{(definition|theorem|lemma|example|proof)\}"
)
_LECTURE_SIGNAL_RE = re.compile(r"讲义|lecture\s*notes?|slide|课件|第\s*\d+\s*讲|本讲|本节课|课堂")

# §E.0（v11）：习题集信号——密集编号习题/练习标记；与解答稀缺度联合判定（见 detect_genre）。
_PROBLEM_SET_MARKER_RE = re.compile(
    r"^\s*(?:习题|练习|问题)\s*[\d一二三四五六七八九十]*[.、．：:]?\s*$"  # "习题 3.1" 小节行
    r"|^\s*\d+\s*[.、．]\s+\S",  # 行首裸编号题 "12. 设 ..."
    re.MULTILINE,
)
_SOLUTION_MARKER_RE = re.compile(
    r"(?:^|\n)\s*(?:解|证明?|答案?|Solution|Proof)\s*[:：.]" r"|\\begin\{(?:solution|proof)\}"
)


def detect_genre(md_text: str, *, extended: bool = False) -> str:
    """从正文结构判别文档体裁：``exam_paper`` / ``book`` / ``lecture`` / ``unknown``。

    纯启发式、确定性、零依赖。计数各体裁信号取最高者；信号过弱返回 ``unknown``。

    ``extended=True``（v11 §E.0，format_profile 路径专用）时额外判别 ``problem_set``：
    编号习题密集且解答标记稀缺（< 20% 题数）的文档。默认 ``False`` 保持
    v10 四值返回不变——REC-14 的 fast-skip 白名单分支不受新体裁影响（R2）。
    """
    if not md_text or not md_text.strip():
        return "unknown"
    exam = len(_EXAM_SIGNAL_RE.findall(md_text)) + len(_EXAM_CHOICE_RE.findall(md_text))
    book = len(_BOOK_SIGNAL_RE.findall(md_text))
    lecture = len(_LECTURE_SIGNAL_RE.findall(md_text))
    if extended:
        problems = len(_PROBLEM_SET_MARKER_RE.findall(md_text))
        solutions = len(_SOLUTION_MARKER_RE.findall(md_text))
        # 题多解少（≥8 题且解答 < 20% 题数）且题目信号强于书本/讲义 → 习题集
        if problems >= 8 and solutions < problems * 0.2 and problems > max(book, lecture):
            return "problem_set"
    best = max(exam, book, lecture)
    if best < 2:  # 信号太弱，不妄断
        return "unknown"
    if exam == best:
        return "exam_paper"
    if book == best:
        return "book"
    return "lecture"


_GENRE_LABELS = {
    "exam_paper": "试卷",
    "book": "书本/教材",
    "lecture": "讲义",
    "problem_set": "习题集",
}


def genre_label(genre: str) -> str:
    """体裁英文 key → 中文标签。"""
    return _GENRE_LABELS.get(genre, "未知")


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


__all__ = ["detect_genre", "format_baseline_hint", "genre_label", "infer_genre"]
