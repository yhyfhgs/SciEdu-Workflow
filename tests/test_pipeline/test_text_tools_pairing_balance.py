"""T8 check_pairing + T12 check_math_balance（WP-21）。"""

from __future__ import annotations

from sciforge.pipeline.text_tools import (
    PairingItem,
    check_math_balance,
    check_pairing,
    find_item_markers,
)

# ── T12 配平 ─────────────────────────────────────────────


def test_balanced_document() -> None:
    report = check_math_balance(
        [
            "设 $f(x)$ 连续，则",
            "$$\\int_0^1 f(x)\\,dx$$ 存在。",
            "\\begin{align}",
            "a &= b \\\\",
            "\\end{align}",
        ]
    )
    assert report.balanced
    assert not report.open_env_at_tail


def test_unclosed_env_at_tail_signals_truncation() -> None:
    report = check_math_balance(["证明如下：", "\\begin{align}", "a &= (b + c"], line_base=101)
    assert report.open_env_at_tail
    assert "\\begin{align}" in report.open_tail_tokens
    unclosed = [i for i in report.issues if i.kind == "unclosed_open"]
    assert any(i.token == "\\begin{align}" and i.line == 102 for i in unclosed)
    assert any(i.token == "(" for i in unclosed)  # 数学模式内括号被跟踪


def test_unopened_close_detected() -> None:
    report = check_math_balance(["\\end{align} 突兀的结束"])
    assert [i.kind for i in report.issues] == ["unopened_close"]


def test_unclosed_inline_dollar() -> None:
    report = check_math_balance(["设 $x + y"])
    assert report.open_env_at_tail
    assert "$" in report.open_tail_tokens


def test_display_dollars_not_split_into_inline() -> None:
    assert check_math_balance(["$$x+y$$"]).balanced


def test_prose_brackets_ignored_outside_math() -> None:
    # 正文枚举 "1)" 与中文括号不应报 unopened_close
    assert check_math_balance(["1) 第一问；2) 第二问。", "（注：见附录）"]).balanced


def test_left_right_pairing() -> None:
    assert check_math_balance(["$\\left( \\frac{a}{b} \\right)$"]).balanced
    report = check_math_balance(["$\\left( \\frac{a}{b} $"])
    assert report.open_env_at_tail


def test_escaped_dollar_ignored() -> None:
    assert check_math_balance(["价格是 \\$5 一斤。"]).balanced


# ── T8 配对 ──────────────────────────────────────────────


def _pairing_item(ref: str, **overrides: object) -> PairingItem:
    base: dict[str, object] = {
        "item_ref": ref,
        "category": "question",
        "item_type": "exercise",
        "solution_status": "complete",
        "has_payload": True,
        "start_line": 1,
        "end_line": 10,
    }
    base.update(overrides)
    return PairingItem.model_validate(base)


def test_pairing_clean() -> None:
    report = check_pairing([_pairing_item("a")], window_last_line=100)
    assert report.ok
    assert report.checked == 1


def test_complete_without_payload() -> None:
    report = check_pairing([_pairing_item("a", has_payload=False)], window_last_line=100)
    assert [i.kind for i in report.issues] == ["complete_without_payload"]


def test_payload_without_complete() -> None:
    report = check_pairing(
        [_pairing_item("a", solution_status="no_solution_in_source")],
        window_last_line=100,
    )
    assert [i.kind for i in report.issues] == ["payload_without_complete"]


def test_near_tail_no_solution_flagged() -> None:
    report = check_pairing(
        [
            _pairing_item(
                "a", solution_status="no_solution_in_source", has_payload=False, end_line=95
            )
        ],
        window_last_line=100,
    )
    assert [i.kind for i in report.issues] == ["near_tail_no_solution"]
    # 同状态但远离末尾 → 不报
    far = check_pairing(
        [
            _pairing_item(
                "a", solution_status="no_solution_in_source", has_payload=False, end_line=50
            )
        ],
        window_last_line=100,
    )
    assert far.ok


def test_near_tail_only_for_exercise_like() -> None:
    report = check_pairing(
        [
            _pairing_item(
                "a",
                category="knowledge",
                item_type="definition",
                solution_status="no_solution_in_source",
                has_payload=False,
                end_line=99,
            )
        ],
        window_last_line=100,
    )
    assert report.ok  # 定义天然无解答，near-tail 不适用


def test_solution_marker_inside_with_ledger() -> None:
    markers = find_item_markers(["习题 1 求极限。", "解：直接计算。"], line_base=1)
    report = check_pairing(
        [
            _pairing_item(
                "a", solution_status="no_solution_in_source", has_payload=False, end_line=2
            )
        ],
        window_last_line=100,
        markers=markers,
    )
    assert "solution_marker_inside" in [i.kind for i in report.issues]
