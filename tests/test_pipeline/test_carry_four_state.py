"""WP-22 carry 四态（III.2）——判别矩阵、构建/匹配、Stage1 状态机。"""

from __future__ import annotations

from pathlib import Path

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline
from sciforge.pipeline.boundary_patch import (
    build_carry,
    carry_matches_item,
    classify_tail_state,
)
from sciforge.pipeline.llm_schemas import IncompleteItemCarry, SingleItemExtractLLM
from sciforge.pipeline.stages.stage1_extract import Stage1Extract

# ── classify_tail_state 判别矩阵 ─────────────────────────


def test_open_math_env_means_truncated() -> None:
    tail = ["习题 5 证明下式：", "\\begin{align}", "a &= b + ("]
    assert classify_tail_state(tail, genre="problem_set") == "content_truncated"


def test_solution_marker_in_tail_means_truncated() -> None:
    tail = ["习题 5 求极限。", "解：由洛必达法则，"]
    # 解答写了一半被窗口切断——即便 genre 是习题集也不是 truly_no_answer
    assert classify_tail_state(tail, genre="problem_set") == "content_truncated"


def test_unterminated_stem_means_stem_unfinished() -> None:
    tail = ["习题 5 设 $f$ 是定义在闭区间上的"]
    assert classify_tail_state(tail, genre="book") == "stem_unfinished"


def test_complete_stem_exam_genre_means_answer_deferred() -> None:
    tail = ["习题 5 求下列极限的值。"]
    assert classify_tail_state(tail, genre="exam_paper") == "answer_deferred"


def test_complete_stem_problem_set_means_truly_no_answer() -> None:
    tail = ["习题 5 求下列极限的值。"]
    assert classify_tail_state(tail, genre="problem_set") == "truly_no_answer"


def test_complete_stem_unknown_genre_uses_prior() -> None:
    tail = ["定理 5.1 连续函数有界。"]
    assert classify_tail_state(tail, genre="book", carry_prior="content_truncated") == (
        "content_truncated"
    )


def test_empty_tail_uses_prior() -> None:
    assert classify_tail_state([], carry_prior="answer_deferred") == "answer_deferred"


# ── build_carry ──────────────────────────────────────────


def test_build_carry_from_tail_marker() -> None:
    tail = ["前一条目的残余正文。", "习题 3.2 设 $G$ 是群，证明"]
    carry = build_carry(tail, line_base=501, genre="book")
    assert carry is not None
    assert carry.item_type == "exercise"
    assert carry.number == "3.2"
    assert carry.category == "question"
    assert carry.original_start_line == 502
    assert carry.tail_state == "stem_unfinished"
    assert carry.head_anchor_start.startswith("习题 3.2")
    assert carry.carry_window_count == 1


def test_build_carry_takes_last_marker() -> None:
    tail = ["定理 5.1 已完整闭合。", "定理 5.2 一致连续性定理的证明如下，"]
    carry = build_carry(tail, line_base=1)
    assert carry is not None
    assert carry.original_start_line == 2
    assert carry.category == "knowledge"


def test_build_carry_none_without_marker() -> None:
    assert build_carry(["纯正文延续，没有条目起始。"], line_base=1) is None


def test_build_carry_head_content_truncated_to_2000() -> None:
    tail = ["习题 9 求证：" + "很长的题面" * 500]
    carry = build_carry(tail, line_base=1)
    assert carry is not None
    assert len(carry.head_content) <= 2000


# ── carry_matches_item ───────────────────────────────────


def _item(
    number: str = "3.2", start_line: int = 502, content: str = "设 $G$ 是群，证明中心正规。"
) -> SingleItemExtractLLM:
    return SingleItemExtractLLM(
        category="question",
        item_type="exercise",
        number=number,
        corrected_content=content,
        corrected_solution="略。",
        solution_status="complete",
        start_line=start_line,
        end_line=start_line + 5,
    )


def _carry(**overrides: object) -> IncompleteItemCarry:
    base: dict[str, object] = {
        "item_type": "exercise",
        "number": "3.2",
        "category": "question",
        "head_anchor_start": "习题 3.2 设 $G$ 是群，证明",
        "head_content": "习题 3.2 设 $G$ 是群，证明",
        "tail_state": "stem_unfinished",
        "original_start_line": 502,
    }
    base.update(overrides)
    return IncompleteItemCarry.model_validate(base)


def test_match_by_number() -> None:
    assert carry_matches_item(_carry(), _item(number="3.2", start_line=999))
    assert not carry_matches_item(_carry(), _item(number="3.3", start_line=999))


def test_match_by_start_line_when_no_number() -> None:
    carry = _carry(number="")
    assert carry_matches_item(carry, _item(number="", start_line=503))
    assert not carry_matches_item(carry, _item(number="", start_line=520, content="别的条目"))


def test_match_by_anchor_fallback() -> None:
    carry = _carry(number="", original_start_line=502)
    item = _item(number="", start_line=880, content="习题 3.2 设 $G$ 是群，证明中心正规。")
    assert carry_matches_item(carry, item)


# ── Stage1 状态机 ─────────────────────────────────────────


def _stage1(**overrides: object) -> Stage1Extract:
    config = AppConfig(**overrides)  # type: ignore[arg-type]
    return Stage1Extract(
        llm_client=None,  # type: ignore[arg-type]
        config=config,
        checkpoint_root=Path("/tmp/nonexistent"),
        discipline=Discipline.MATH,
    )


def _annotated(lines: list[str], base: int) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


_WINDOW_LINES = [
    "定理 5.1 连续函数有界。",  # 501（完整条目）
    "证明：显然。",  # 502
    "习题 3.2 设 $G$ 是群，证明",  # 503（尾部未闭合）
]


def test_flag_off_never_builds_carry() -> None:
    stage = _stage1()
    stage._update_carry_after_window(
        [], "incomplete_at_tail", _annotated(_WINDOW_LINES, 501), 501, 2, 3
    )
    assert stage._carry is None
    assert stage._carry_prompt_section() is None


def test_carry_built_from_unconsumed_tail() -> None:
    stage = _stage1(extract_active_carry_enabled=True)
    stage._update_carry_after_window(
        [], "incomplete_at_tail", _annotated(_WINDOW_LINES, 501), 501, 2, 3
    )
    assert stage._carry is not None
    assert stage._carry.number == "3.2"
    assert stage._carry.original_start_line == 503
    section = stage._carry_prompt_section()
    assert section is not None
    assert "跨窗交接" in section
    assert "stem_unfinished" in section


def test_carry_resolved_by_matching_item() -> None:
    stage = _stage1(extract_active_carry_enabled=True)
    stage._carry = _carry(original_start_line=503)
    stage._update_carry_after_window(
        [_item(number="3.2", start_line=503)],
        "all_complete",
        _annotated(_WINDOW_LINES, 501),
        501,
        3,
        3,
    )
    assert stage._carry is None


def test_carry_count_inherited_and_abandoned_after_force() -> None:
    stage = _stage1(extract_active_carry_enabled=True)
    window = _annotated(_WINDOW_LINES, 501)
    # 同一条目连续未闭合：count 1 → 2 → 3（到顶，下一窗 force）→ 4 超限放弃
    for expected in (1, 2, 3):
        stage._update_carry_after_window([], "incomplete_at_tail", window, 501, 2, 3)
        assert stage._carry is not None
        assert stage._carry.carry_window_count == expected
    assert stage._carry.carry_window_count >= stage.carry_max_windows  # 触发 force 条件
    stage._update_carry_after_window([], "incomplete_at_tail", window, 501, 2, 3)
    assert stage._carry is not None and stage._carry.carry_window_count == 4  # force 窗仍未闭合
    stage._update_carry_after_window([], "incomplete_at_tail", window, 501, 2, 3)
    assert stage._carry is None  # 超 max+1 → 放弃，退回 v10 被动重扫


def test_carry_dropped_when_window_fully_closed_without_match() -> None:
    stage = _stage1(extract_active_carry_enabled=True)
    stage._carry = _carry()
    stage._update_carry_after_window(
        [_item(number="9.9", start_line=900, content="无关条目")],
        "all_complete",
        _annotated(_WINDOW_LINES, 501),
        501,
        3,
        3,
    )
    assert stage._carry is None
