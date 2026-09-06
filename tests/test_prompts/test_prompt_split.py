"""Prompt 静态/动态拆分 + caching 投递（Phase 3 #9）回归测试"""

from __future__ import annotations

import pytest

from sciforge.core.models import Discipline
from sciforge.prompts.templates import (
    build_pipeline_prompt_io,
    get_annotate_stage2_initial_parts,
    get_annotate_stage2_initial_template,
    get_annotate_stage2_review_parts,
    get_extract_stage1_initial_parts,
    get_extract_stage1_review_parts,
    split_static_dynamic,
)

_PARTS_GETTERS = [
    (get_extract_stage1_initial_parts, ["{current_block}", "{retry_feedback}"]),
    (get_extract_stage1_review_parts, ["{window_text}", "{items_json}"]),
    (get_annotate_stage2_initial_parts, ["{item_json}", "{tag_subtree}", "{taxonomy_tree}"]),
    (get_annotate_stage2_review_parts, ["{item_json}", "{annotation_json}"]),
]

_ALL_RUNTIME_PH = [
    "{current_block}",
    "{window_text}",
    "{items_json}",
    "{item_json}",
    "{tag_subtree}",
    "{taxonomy_tree}",
    "{annotation_json}",
    "{source_genre_hint}",
    "{retry_feedback}",
]


@pytest.mark.parametrize(("getter", "user_phs"), _PARTS_GETTERS)
def test_split_produces_static_system_and_dynamic_user(getter, user_phs) -> None:
    static, dynamic = getter(Discipline.MATH)
    assert dynamic, "应成功拆出动态输入段"
    # system 可安全 .format()（无运行时占位符，仅 {{ }} 转义）
    system = static.format()
    for ph in _ALL_RUNTIME_PH:
        assert ph not in system, f"system 残留运行时占位符 {ph}"
    # 动态段含该 stage 的输入占位符
    for ph in user_phs:
        assert ph in dynamic, f"动态段缺占位符 {ph}"


def test_split_preserves_all_sections() -> None:
    """拆分不丢内容：原模板所有小节标题在 system+user 合并后仍在。"""
    import re

    full = get_annotate_stage2_initial_template(Discipline.MATH)
    titles = re.findall(r"#\s*\d+\.\s*([^\n]*)", full)
    static, dynamic = get_annotate_stage2_initial_parts(Discipline.MATH)
    combined = static + "\n" + dynamic
    for t in titles:
        assert t in combined


def test_build_pipeline_prompt_io_enabled() -> None:
    parts = get_annotate_stage2_initial_parts(Discipline.MATH)
    full = get_annotate_stage2_initial_template(Discipline.MATH)
    system, user_tmpl = build_pipeline_prompt_io(parts, full, cache_enabled=True)
    assert system is not None and len(system) > 500  # 静态指令进 system
    assert "{item_json}" in user_tmpl  # 动态留 user
    assert "{item_json}" not in system


def test_build_pipeline_prompt_io_disabled_falls_back() -> None:
    parts = get_annotate_stage2_initial_parts(Discipline.MATH)
    full = get_annotate_stage2_initial_template(Discipline.MATH)
    system, user_tmpl = build_pipeline_prompt_io(parts, full, cache_enabled=False)
    assert system is None  # 回退合并路径
    assert user_tmpl == full


def test_unsplittable_template_returns_empty_dynamic() -> None:
    system, dynamic = split_static_dynamic("没有小节分隔符的纯文本模板")
    assert dynamic == ""
    assert system == "没有小节分隔符的纯文本模板"


def test_stage2_annotate_uses_system_prompt() -> None:
    """Stage2Annotate 默认启用 caching → system_prompt 非空、user_template 含占位符。"""
    from sciforge.config.settings import AppConfig
    from sciforge.pipeline.stages.stage2_annotate import Stage2Annotate

    class _FakeLLM:
        def enable_pipeline_logging(self, *a: object, **k: object) -> None:
            pass

    stage = Stage2Annotate(
        llm_client=_FakeLLM(),  # type: ignore[arg-type]
        config=AppConfig(),
        checkpoint_root=None,
        discipline=Discipline.MATH,
    )
    assert stage.system_prompt is not None
    assert "difficulty" in stage.system_prompt  # 静态指令在 system
    assert "{item_json}" in stage.user_template  # 动态占位符在 user
