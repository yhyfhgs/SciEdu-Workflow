"""WP-32 per-item 四态 + provisional id 出口 remap（I.3）。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline
from sciforge.pipeline.boundary_patch import carry_match_confidence
from sciforge.pipeline.llm_schemas import IncompleteItemCarry, SingleItemExtractLLM
from sciforge.pipeline.stages.stage1_extract import Stage1Extract


def _stage1(**overrides: object) -> Stage1Extract:
    config = AppConfig(**overrides)  # type: ignore[arg-type]
    return Stage1Extract(
        llm_client=None,  # type: ignore[arg-type]
        config=config,
        checkpoint_root=Path("/tmp/nonexistent"),
        discipline=Discipline.MATH,
    )


def _item(number: str, start: int, end: int | None = None) -> SingleItemExtractLLM:
    return SingleItemExtractLLM(
        category="knowledge",
        item_type="theorem",
        number=number,
        corrected_content=f"内容 {number}",
        corrected_proof="证明。",
        solution_status="complete",
        start_line=start,
        end_line=end or start + 2,
    )


# ── ItemStatus / provisional_id 字段 ──────────────────────


def test_item_status_defaults_to_accepted() -> None:
    item = _item("1.1", 10)
    assert item.status == "accepted"
    assert item.provisional_id == ""


def test_item_status_assignable() -> None:
    item = _item("1.1", 10)
    item.status = "uncertain"
    assert item.status == "uncertain"


# ── carry_match_confidence 分级 ───────────────────────────


def _carry(**overrides: object) -> IncompleteItemCarry:
    base: dict[str, object] = {
        "item_type": "exercise",
        "number": "3.2",
        "category": "question",
        "head_anchor_start": "习题 3.2 设 $G$ 是群",
        "head_content": "习题 3.2 设 $G$ 是群",
        "tail_state": "stem_unfinished",
        "original_start_line": 502,
    }
    base.update(overrides)
    return IncompleteItemCarry.model_validate(base)


def test_confidence_exact_on_number_match() -> None:
    assert carry_match_confidence(_carry(), _item("3.2", 999)) == "exact"


def test_confidence_none_on_number_mismatch() -> None:
    assert carry_match_confidence(_carry(), _item("3.3", 999)) == "none"


def test_confidence_uncertain_on_line_proximity_without_number() -> None:
    carry = _carry(number="")
    assert carry_match_confidence(carry, _item("", 503)) == "uncertain"


def test_confidence_uncertain_on_anchor_fallback() -> None:
    carry = _carry(number="", original_start_line=502)
    item = SimpleNamespace(
        number="", start_line=880, corrected_content="习题 3.2 设 $G$ 是群，证明中心正规。"
    )
    assert carry_match_confidence(carry, item) == "uncertain"


def test_confidence_none_when_nothing_matches() -> None:
    carry = _carry(number="", head_anchor_start="", original_start_line=502)
    assert carry_match_confidence(carry, _item("", 900)) == "none"


# ── _finalize_item_ids ────────────────────────────────────


async def test_finalize_disabled_uses_extraction_order() -> None:
    stage = _stage1()  # remap 关闭（默认）
    items = [_item("2", 20), _item("1", 10)]  # 提取顺序：先2后1（未排序）
    ids = await stage._finalize_item_ids(items)
    assert ids == [0, 1]  # 现状：直接按提取顺序连续分配


async def test_finalize_enabled_sorts_by_start_line() -> None:
    stage = _stage1(provisional_id_remap_enabled=True)
    items = [_item("2", 20), _item("1", 10), _item("3", 30)]  # 提取顺序乱序
    ids = await stage._finalize_item_ids(items)
    assert [item.number for item in items] == ["1", "2", "3"]
    assert ids == [0, 1, 2]


async def test_finalize_enabled_ids_strictly_increasing_overall() -> None:
    stage = _stage1(provisional_id_remap_enabled=True)
    items = [_item("3", 30), _item("1", 10), _item("2", 20)]
    ids = await stage._finalize_item_ids(items)
    assert ids == list(range(min(ids), min(ids) + len(ids)))
    assert all(ids[index] < ids[index + 1] for index in range(len(ids) - 1))


async def test_finalize_ties_use_end_line_then_original_order() -> None:
    stage = _stage1(provisional_id_remap_enabled=True)
    first_same_span = _item("same-a", 10, 14)
    second_same_span = _item("same-b", 10, 14)
    shorter = _item("short", 10, 12)
    items = [first_same_span, second_same_span, shorter]

    ids = await stage._finalize_item_ids(items)

    assert [item.number for item in items] == ["short", "same-a", "same-b"]
    assert ids == [0, 1, 2]


async def test_finalize_single_item_no_sort_needed() -> None:
    stage = _stage1(provisional_id_remap_enabled=True)
    ids = await stage._finalize_item_ids([_item("1", 10)])
    assert ids == [0]


async def test_finalize_empty_items() -> None:
    stage = _stage1(provisional_id_remap_enabled=True)
    assert await stage._finalize_item_ids([]) == []


async def test_finalize_already_sorted_is_identity() -> None:
    """已按 start_line 顺序提取（正常情形）→ remap 开关不改变结果。"""
    items = [_item("1", 10), _item("2", 20), _item("3", 30)]
    off = await _stage1()._finalize_item_ids(list(items))
    on = await _stage1(provisional_id_remap_enabled=True)._finalize_item_ids(list(items))
    assert off == on == [0, 1, 2]


async def test_finalize_resume_next_item_id_advances_consistently() -> None:
    """resume 兼容：无论 remap 开关，next_item_id 都推进到 count（不倒退/不跳号）。"""
    for enabled in (False, True):
        stage = _stage1(provisional_id_remap_enabled=enabled)
        items = [_item("2", 20), _item("1", 10)]
        await stage._finalize_item_ids(items)
        assert stage.next_item_id == 2


# ── carry 试探合并回补（M2 裁剪 R5，本包补上）─────────────


def _annotated(lines: list[str], base: int) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


def test_carry_uncertain_match_tags_item_status_when_remap_enabled() -> None:
    stage = _stage1(extract_active_carry_enabled=True, provisional_id_remap_enabled=True)
    stage._carry = _carry(number="", original_start_line=503)
    item = _item("", 503)  # 无编号，靠行号邻近匹配 → uncertain
    window = _annotated(["定理 5.1 内容。", "证明：略。", "习题 3.2 续写"], 501)
    stage._update_carry_after_window([item], "all_complete", window, 501, 3, 3)
    assert stage._carry is None  # 仍然定案清除
    assert item.status == "uncertain"  # 但标记为试探合并


def test_carry_exact_match_keeps_accepted_status() -> None:
    stage = _stage1(extract_active_carry_enabled=True, provisional_id_remap_enabled=True)
    stage._carry = _carry(number="3.2", original_start_line=503)
    item = _item("3.2", 999)  # 编号精确匹配 → exact
    window = _annotated(["占位"], 501)
    stage._update_carry_after_window([item], "all_complete", window, 501, 1, 1)
    assert item.status == "accepted"


def test_carry_uncertain_not_tagged_when_remap_disabled() -> None:
    """remap 关闭时不改动 item.status（即便 carry 开启）——现状兼容。"""
    stage = _stage1(extract_active_carry_enabled=True, provisional_id_remap_enabled=False)
    stage._carry = _carry(number="", original_start_line=503)
    item = _item("", 503)
    window = _annotated(["占位"], 501)
    stage._update_carry_after_window([item], "all_complete", window, 501, 1, 1)
    assert item.status == "accepted"  # 未被标记 uncertain
