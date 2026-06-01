"""文档级 reconciliation 复核（#7）回归测试"""

from __future__ import annotations

from sciforge.pipeline.reconciliation import reconcile


def _row(id_, kind, type_, head, nc="", subj="real_analysis:实分析", ch=3, sec=2):
    return {
        "id": id_, "kind": kind, "type": type_, "number_canonical": nc,
        "subject": subj, "seq_chapter": ch, "seq_section": sec, "content_head": head,
    }


def test_detects_duplicate_by_number() -> None:
    rows = [
        _row("a", "kp", "theorem", "**定理 3.2** ...", nc="3.2"),
        _row("b", "kp", "theorem", "**定理 3.2** ...", nc="3.2"),  # 同节同号 → 重复
        _row("c", "kp", "definition", "**定义 3.3** ...", nc="3.3"),
    ]
    rep = reconcile(rows)
    assert rep["summary"]["duplicate_groups"] == 1
    dg = rep["duplicate_groups"][0]
    assert dg["number_canonical"] == "3.2"
    assert set(dg["ids"]) == {"a", "b"}


def test_no_dup_when_number_empty() -> None:
    rows = [
        _row("a", "kp", "theorem", "定理...", nc=""),
        _row("b", "kp", "theorem", "定理...", nc=""),  # 无规范号 → 不误报
    ]
    assert reconcile(rows)["summary"]["duplicate_groups"] == 0


def test_category_mismatch_kp_marker_stored_as_question() -> None:
    # 正文是"定义"（应为 knowledge），却被存成 question
    rows = [_row("x", "question", "calculation", "**定义 1.1** 设...")]
    rep = reconcile(rows)
    mm = rep["type_mismatches"]
    assert len(mm) == 1 and mm[0]["issue"] == "category_mismatch"
    assert mm[0]["expected"] == "knowledge" and mm[0]["actual"] == "question"


def test_kp_type_mismatch() -> None:
    # 正文"定义"开头却被存成 theorem
    rows = [_row("x", "kp", "theorem", "定义 2.1 称...")]
    rep = reconcile(rows)
    mm = rep["type_mismatches"]
    assert len(mm) == 1 and mm[0]["issue"] == "kp_type_mismatch"
    assert mm[0]["expected"] == "definition" and mm[0]["actual"] == "theorem"


def test_exercise_marker_expects_question() -> None:
    # "习题"开头被存成 knowledge → category mismatch
    rows = [_row("x", "kp", "definition", "**习题 5** 求...")]
    rep = reconcile(rows)
    assert rep["type_mismatches"][0]["issue"] == "category_mismatch"
    assert rep["type_mismatches"][0]["expected"] == "question"


def test_correct_items_no_mismatch() -> None:
    rows = [
        _row("a", "kp", "definition", "**定义 1.1** ...", nc="1.1"),
        _row("b", "kp", "theorem", "**定理 1.2** ...", nc="1.2"),
        _row("c", "question", "calculation", "**例 1.3** 计算 ...", nc="1.3"),
        _row("d", "question", "proof", "**习题 1** 证明 ...", nc="1"),
    ]
    rep = reconcile(rows)
    assert rep["summary"]["type_mismatches"] == 0
    assert rep["summary"]["duplicate_groups"] == 0
