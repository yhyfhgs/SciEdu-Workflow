"""编号连续性核对（Phase 1 #2/#8）回归测试"""

from __future__ import annotations

from sciforge.pipeline.numbering_audit import audit_numbering


def test_detects_gap() -> None:
    rows = [
        ("analysis:分析", 3, None, 1, "3.1", "kp", "a"),
        ("analysis:分析", 3, None, 3, "3.3", "kp", "b"),  # 缺 3.2
        ("analysis:分析", 3, None, 4, "3.4", "question", "c"),
    ]
    rep = audit_numbering(rows)
    assert rep["groups"] == 1
    assert len(rep["suspected_gaps"]) == 1
    assert rep["suspected_gaps"][0]["missing"] == [2]


def test_no_false_gap_when_continuous() -> None:
    rows = [
        ("algebra:代数", 1, None, 1, "1.1", "kp", "d"),
        ("algebra:代数", 1, None, 2, "1.2", "kp", "e"),
    ]
    rep = audit_numbering(rows)
    assert rep["suspected_gaps"] == []


def test_different_groups_isolated() -> None:
    """不同 (subject, chapter) 分组独立，不会跨组误报。"""
    rows = [
        ("analysis:分析", 1, None, 1, "1.1", "kp", "a"),
        ("algebra:代数", 1, None, 5, "1.5", "kp", "b"),  # 不同 subject，单元素组不报
    ]
    rep = audit_numbering(rows)
    assert rep["groups"] == 2
    assert rep["suspected_gaps"] == []


def test_large_span_skipped() -> None:
    """跨度过大（疑似编号体系切换）不报 gap。"""
    rows = [
        ("analysis:分析", 1, None, 1, "1.1", "kp", "a"),
        ("analysis:分析", 1, None, 99, "1.99", "kp", "b"),
    ]
    rep = audit_numbering(rows)
    assert rep["suspected_gaps"] == []


def test_none_index_ignored() -> None:
    rows = [
        ("analysis:分析", None, None, None, "", "kp", "a"),
        ("analysis:分析", None, None, None, "", "kp", "b"),
    ]
    rep = audit_numbering(rows)
    assert rep["suspected_gaps"] == []
