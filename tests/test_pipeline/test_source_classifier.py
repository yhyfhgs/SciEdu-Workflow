"""Source classifier 测试：genre 推断与 baseline hint 生成"""

from __future__ import annotations

import pytest

from sciforge.pipeline.source_classifier import format_baseline_hint, infer_genre


class TestInferGenre:
    @pytest.mark.parametrize(
        "path,expected_genre,low,high",
        [
            ("Raw Data/新领军真题整理/2021.3.tex", "competition", 7, 10),
            ("raw/cmo_2020_solutions.md", "competition", 7, 10),
            ("docs/IMO_archive/imo2019.pdf", "competition", 7, 10),
            ("高联训练题集.pdf", "competition", 7, 10),
            ("考研真题数学一_2020.pdf", "graduate_entrance", 6, 9),
            ("数学分析习题集.pdf", "exercise_book", 4, 7),
            ("group_theory_problems.md", "exercise_book", 4, 7),
            ("高等代数教材.pdf", "textbook", 2, 6),
            ("lecture_notes_calculus.pdf", "textbook", 2, 6),
            ("analysis_textbook_ch3.tex", "textbook", 2, 6),
            ("misc/some_paper.pdf", "general", 3, 8),
            ("", "general", 3, 8),
        ],
    )
    def test_patterns(self, path: str, expected_genre: str, low: int, high: int) -> None:
        g, _label, lo, hi = infer_genre(path)
        assert g == expected_genre
        assert lo == low
        assert hi == high


class TestFormatBaselineHint:
    def test_competition_produces_hint(self) -> None:
        h = format_baseline_hint("Raw Data/新领军真题整理/2021.3.tex")
        assert "竞赛/真题" in h
        assert "7-10" in h
        assert "文档类型 baseline" in h

    def test_textbook_produces_hint(self) -> None:
        h = format_baseline_hint("数学教材/ch1.md")
        assert "教材" in h
        assert "2-6" in h

    def test_general_returns_empty(self) -> None:
        """未识别类型不注入（避免误导 LLM）"""
        assert format_baseline_hint("random_file.pdf") == ""
        assert format_baseline_hint("") == ""

    def test_no_brace_pollution_safe(self) -> None:
        """baseline hint 里不含无转义的 `{`（防止后续 .format 出错）"""
        h = format_baseline_hint("competition.md")
        # hint 本身不应含单大括号占位符；允许无
        assert "{" not in h
        assert "}" not in h
