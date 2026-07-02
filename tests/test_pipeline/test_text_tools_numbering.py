"""T7 check_numbering_continuity（WP-12）——跳号/重号/乱序。"""

from __future__ import annotations

from sciforge.pipeline.text_tools import check_numbering_continuity


def test_continuous_sequence_ok() -> None:
    report = check_numbering_continuity(["3.1", "3.2", "3.3"])
    assert report.ok
    assert report.checked == 3


def test_gap_detected_with_missing_numbers() -> None:
    report = check_numbering_continuity(["3.1", "3.4"])
    assert not report.ok
    assert report.issues[0].kind == "gap"
    assert "3.2" in report.issues[0].detail
    assert "3.3" in report.issues[0].detail


def test_duplicate_and_out_of_order() -> None:
    report = check_numbering_continuity(["2.5", "2.5", "2.3"])
    kinds = [i.kind for i in report.issues]
    assert kinds == ["duplicate", "out_of_order"]


def test_prefix_switch_starts_new_sequence() -> None:
    # 3.2.9 → 3.3.1 是章节切换，不是缺口
    assert check_numbering_continuity(["3.2.8", "3.2.9", "3.3.1", "3.3.2"]).ok


def test_fullwidth_and_prefixed_numbers_normalized() -> None:
    # 全角 + "第X题" 前缀 → canonicalize 后仍可比较
    report = check_numbering_continuity(["第１题", "第２题", "第４题"])
    assert [i.kind for i in report.issues] == ["gap"]


def test_unparseable_numbers_skipped() -> None:
    report = check_numbering_continuity(["", "甲", "1", "2"])
    assert report.ok
    assert report.checked == 2


def test_top_level_numbers() -> None:
    report = check_numbering_continuity(["1", "2", "5"])
    assert [i.kind for i in report.issues] == ["gap"]
