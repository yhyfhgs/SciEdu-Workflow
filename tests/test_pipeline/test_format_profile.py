"""v11 §E.0 format/genre profile（WP-11）——策略表、密度、problem_set、R2 行为不变。"""

from __future__ import annotations

from pathlib import Path

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline
from sciforge.pipeline.format_profile import (
    GENRE_STRATEGIES,
    MAX_BASE_WINDOW,
    MIN_BASE_WINDOW,
    compute_format_profile,
    compute_genre_profile,
    estimate_density,
)
from sciforge.pipeline.source_classifier import detect_genre, genre_label
from sciforge.pipeline.stages.stage1_extract import Stage1Extract

# ── 合成文本 ─────────────────────────────────────────────

# 习题集：编号题密集、无解答
_PROBLEM_SET_TEXT = "\n".join(f"{i}. 设 $G$ 是群，证明第 {i} 个命题。" for i in range(1, 31))

# 教材：定理/定义 + 证明，行长且稀疏
_BOOK_TEXT = (
    "第 三 章\n\n**定理 3.1** 连续函数在闭区间上有界。\n证明：略。\n\n"
    "**定义 3.2** 一致连续。\n" + "这是一段很长的正文叙述，" * 10 + "\n" * 200
)

# 试卷：分值 + 大题分组 + 选项
_EXAM_TEXT = (
    "本卷满分 150 分，考试时间 120 分钟\n一、选择题\n"
    "1.（5分）下列正确的是\nA. 甲\nB. 乙\nC. 丙\nD. 丁\n"
    "2.（5分）求极限\nA. 0\nB. 1\nC. 2\nD. 不存在\n参考答案\n"
)


def _stage1(**config_overrides: object) -> Stage1Extract:
    config = AppConfig(**config_overrides)  # type: ignore[arg-type]
    return Stage1Extract(
        llm_client=None,  # type: ignore[arg-type]
        config=config,
        checkpoint_root=Path("/tmp/nonexistent"),
        discipline=Discipline.MATH,
    )


# ── detect_genre extended（R2）────────────────────────────


def test_detect_genre_default_never_returns_problem_set() -> None:
    """R2：默认（v10）路径不产生新体裁值，REC-14 白名单行为不变。"""
    assert detect_genre(_PROBLEM_SET_TEXT) != "problem_set"
    assert detect_genre(_PROBLEM_SET_TEXT, extended=True) == "problem_set"


def test_detect_genre_extended_keeps_other_genres() -> None:
    assert detect_genre(_EXAM_TEXT, extended=True) == "exam_paper"
    assert detect_genre(_BOOK_TEXT, extended=True) == "book"


def test_problem_set_needs_scarce_solutions() -> None:
    with_solutions = "\n".join(
        f"{i}. 证明命题。\n解：显然成立。" for i in range(1, 31)
    )
    assert detect_genre(with_solutions, extended=True) != "problem_set"


def test_genre_label_covers_problem_set() -> None:
    assert genre_label("problem_set") == "习题集"


# ── 策略表与画像 ──────────────────────────────────────────


def test_strategy_table_covers_all_genres() -> None:
    assert set(GENRE_STRATEGIES) == {"book", "exam_paper", "lecture", "problem_set", "unknown"}
    for strategy in GENRE_STRATEGIES.values():
        low, high = strategy.window_range
        assert MIN_BASE_WINDOW <= low <= high <= MAX_BASE_WINDOW


def test_genre_profile_exam_disables_fast_skip() -> None:
    profile = compute_genre_profile(_EXAM_TEXT)
    assert profile.genre == "exam_paper"
    assert profile.fast_skip_recommended is False
    assert profile.carry_prior == "answer_deferred"


def test_format_profile_dense_takes_low_end() -> None:
    profile = compute_format_profile(_PROBLEM_SET_TEXT, genre="problem_set")
    assert profile.density == "dense"
    assert profile.suggested_base_window == GENRE_STRATEGIES["problem_set"].window_range[0]


def test_format_profile_sparse_takes_high_end() -> None:
    profile = compute_format_profile(_BOOK_TEXT, genre="book")
    assert profile.density == "sparse"
    assert profile.suggested_base_window == GENRE_STRATEGIES["book"].window_range[1]


def test_estimate_density_boundaries() -> None:
    assert estimate_density([]) == "normal"
    assert estimate_density(["纯叙述文字"] * 100) == "sparse"
    dense = [f"{i}. 题目" for i in range(10)] + ["正文"] * 90
    assert estimate_density(dense) == "dense"


# ── Stage1 集成（flag 双态）──────────────────────────────


def test_stage1_flag_off_keeps_rec14_behavior() -> None:
    """R2：flag 关 = v10 白名单路径；problem_set 文本不触发 fast-skip 关闭。"""
    stage = _stage1(format_profile_enabled=False)
    assert stage.profile_enabled is False
    stage._apply_genre_policy(_PROBLEM_SET_TEXT)
    assert stage._genre != "problem_set"  # 四值探测
    assert stage.fast_skip_enabled is True  # 白名单不扩
    assert stage.base_window == 500
    assert stage.format_profile is None


def test_stage1_flag_off_exam_still_disables_fast_skip() -> None:
    stage = _stage1()
    stage._apply_genre_policy(_EXAM_TEXT)
    assert stage._genre == "exam_paper"
    assert stage.fast_skip_enabled is False  # REC-14 原行为保留


def test_stage1_flag_on_applies_profile() -> None:
    stage = _stage1(format_profile_enabled=True)
    stage._apply_genre_policy(_PROBLEM_SET_TEXT)
    assert stage._genre == "problem_set"
    assert stage.fast_skip_enabled is False
    assert stage.base_window == GENRE_STRATEGIES["problem_set"].window_range[0]
    assert stage.current_window_size == stage.base_window
    assert stage.format_profile is not None
    assert stage.genre_profile is not None


def test_stage1_flag_on_respects_resumed_window() -> None:
    """resume 恢复的扩窗状态不被画像改写。"""
    stage = _stage1(format_profile_enabled=True)
    stage.current_window_size = 1000  # 模拟 resume 恢复的扩窗
    stage._apply_genre_policy(_PROBLEM_SET_TEXT)
    assert stage.current_window_size == 1000  # 不回退
    assert stage.base_window == GENRE_STRATEGIES["problem_set"].window_range[0]


def test_stage1_flag_on_never_reenables_fast_skip() -> None:
    """用户显式关掉 fast-skip 时画像不重新打开。"""
    stage = _stage1(format_profile_enabled=True, empty_window_fast_skip=False)
    stage._apply_genre_policy(_BOOK_TEXT)  # book 推荐开 fast-skip
    assert stage.fast_skip_enabled is False


def test_stage1_genre_policy_idempotent() -> None:
    stage = _stage1(format_profile_enabled=True)
    stage._apply_genre_policy(_EXAM_TEXT)
    genre_first = stage._genre
    stage._apply_genre_policy(_BOOK_TEXT)  # 第二次调用不生效
    assert stage._genre == genre_first
