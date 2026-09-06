"""WP-14 char span resolver——降级矩阵、尾巴回退、prompt 快照、Stage1 集成双态。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline
from sciforge.pipeline.char_span import resolve_span
from sciforge.pipeline.llm_schemas import ItemAnchorsLLM, SingleItemExtractLLM
from sciforge.pipeline.stages.stage1_extract import Stage1Extract

_LINES = [
    "定理 3.1 设 f 在闭区间上连续，则 f 有界。",  # 101
    "证明：反证法。假设 f 无界。",  # 102
    "于是存在序列使得矛盾。证毕。",  # 103
    "定理 3.2 一致连续性定理。",  # 104
]


def _resolve(start_anchor: str, end_anchor: str, **kw: object) -> object:
    defaults: dict[str, object] = {"line_base": 101, "start_line": 101, "end_line": 103}
    defaults.update(kw)
    return resolve_span(start_anchor, end_anchor, _LINES, **defaults)  # type: ignore[arg-type]


# ── 降级矩阵 ─────────────────────────────────────────────


def test_exact_unique_hit() -> None:
    span = _resolve("设 f 在闭区间上连续", "矛盾。证毕。")
    assert span.confidence == "exact"
    assert span.start_line == 101
    assert span.start_char == _LINES[0].find("设")
    assert span.end_line == 103
    assert span.end_char == len(_LINES[2])
    assert span.text.startswith("设 f 在闭区间上连续")
    assert span.text.endswith("证毕。")


def test_multiple_hits_prefers_hint_line() -> None:
    lines = ["答案是 5。", "另一行。", "答案是 5。"]
    span = resolve_span("答案是 5。", "答案是 5。", lines, line_base=1, start_line=3, end_line=3)
    assert span.confidence == "exact"
    assert span.start_line == 3


def test_fuzzy_hit_on_ocr_noise() -> None:
    # 锚点与原文差一个字符（OCR 混淆）→ fuzzy
    span = _resolve("设 f 在团区间上连续", "矛盾。证毕。")
    assert span.confidence == "fuzzy"
    assert span.start_line == 101


def test_unresolved_falls_back_to_line_level() -> None:
    span = _resolve("完全不存在的锚点串", "也不存在的结尾")
    assert span.confidence == "line_fallback"
    assert span.start_line == 101
    assert span.end_line == 103
    assert "start_anchor_unresolved" in span.resolver_note


def test_end_before_start_falls_back() -> None:
    span = _resolve("矛盾。证毕。", "设 f 在闭区间上连续", start_line=103, end_line=101)
    assert span.confidence == "line_fallback"
    assert "end_before_start" in span.resolver_note


def test_trailing_marker_backoff() -> None:
    """end 锚点误落到下一条目标题行 → 确定性回退到上一行行尾。"""
    span = _resolve("设 f 在闭区间上连续", "一致连续性定理。", end_line=104)
    assert span.end_line == 103  # 回退，不吞下一条定理
    assert "trailing_marker_backoff" in span.resolver_note


def test_empty_window() -> None:
    span = resolve_span("a", "b", [], line_base=1, start_line=1, end_line=1)
    assert span.confidence == "line_fallback"


# ── prompt 快照（R3：flag 关字节级不变）─────────────────


def _stage1(**overrides: object) -> Stage1Extract:
    config = AppConfig(**overrides)  # type: ignore[arg-type]
    return Stage1Extract(
        llm_client=None,  # type: ignore[arg-type]
        config=config,
        checkpoint_root=Path("/tmp/nonexistent"),
        discipline=Discipline.MATH,
    )


def test_prompt_unchanged_when_flag_off() -> None:
    baseline = _stage1()
    off = _stage1(extract_char_span_enabled=False)
    assert off.system_prompt == baseline.system_prompt
    assert off.user_template == baseline.user_template


def test_prompt_gains_addendum_when_flag_on() -> None:
    baseline = _stage1()
    on = _stage1(extract_char_span_enabled=True)
    assert on.system_prompt is not None
    assert baseline.system_prompt is not None
    assert on.system_prompt.startswith(baseline.system_prompt)
    assert "start_anchor" in on.system_prompt
    assert on.user_template == baseline.user_template  # 动态段不受影响


def test_prompt_addendum_escaped_in_merged_mode() -> None:
    """cache 关（合并 user 模板未 format）时附加段花括号须转义。"""
    on = _stage1(extract_char_span_enabled=True, pipeline_prompt_cache_enabled=False)
    assert on.system_prompt is None
    assert "start_anchor" in on.user_template
    assert '{{"start_anchor"' in on.user_template  # 已转义，后续 .format() 安全


# ── Stage1 集成 ──────────────────────────────────────────


def _annotated(lines: list[str], base: int) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


def _item_with_anchors() -> SingleItemExtractLLM:
    return SingleItemExtractLLM(
        category="knowledge",
        item_type="theorem",
        number="3.1",
        corrected_content="设 f 在闭区间上连续，则 f 有界。",
        corrected_proof="反证法。",
        solution_status="complete",
        start_line=101,
        end_line=103,
        anchors=ItemAnchorsLLM(start_anchor="设 f 在闭区间上连续", end_anchor="矛盾。证毕。"),
    )


def test_resolve_item_spans_fills_offsets() -> None:
    stage = _stage1(extract_char_span_enabled=True)
    item = _item_with_anchors()
    stage._resolve_item_spans(_annotated(_LINES, 101), 101, SimpleNamespace(items=[item]))
    assert item.resolved_start_char == _LINES[0].find("设")
    assert item.resolved_end_char == len(_LINES[2])


def test_resolve_item_spans_flag_off_noop() -> None:
    stage = _stage1()
    item = _item_with_anchors()
    stage._resolve_item_spans(_annotated(_LINES, 101), 101, SimpleNamespace(items=[item]))
    assert item.resolved_start_char is None
    assert item.resolved_end_char is None


def test_resolve_item_spans_skips_missing_anchors() -> None:
    stage = _stage1(extract_char_span_enabled=True)
    item = _item_with_anchors()
    item.anchors = None
    stage._resolve_item_spans(_annotated(_LINES, 101), 101, SimpleNamespace(items=[item]))
    assert item.resolved_start_char is None
