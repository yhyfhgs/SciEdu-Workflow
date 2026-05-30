"""Pipeline prompt 两阶段注入的单元测试（v6 5-stage pipeline）"""

from sciforge.core.models import Discipline
from sciforge.prompts.templates import (
    VLM_CONVERT_PROMPT,
    get_annotate_stage2_initial_template,
    get_extract_stage1_initial_template,
    get_vlm_prompt_template,
)


class TestExtractStage1InitialPrompt:
    def test_math_has_math_expert(self):
        tpl = get_extract_stage1_initial_template(Discipline.MATH)
        assert "数学" in tpl

    def test_math_has_runtime_placeholders(self):
        tpl = get_extract_stage1_initial_template(Discipline.MATH)
        assert "{current_block}" in tpl
        assert "{retry_feedback}" in tpl

    def test_physics_has_physics_content(self):
        tpl = get_extract_stage1_initial_template(Discipline.PHYSICS)
        assert "物理" in tpl
        # 物理模板里不应还有"数学文档"这种纯数学表述
        assert "数学文档结构化提取专家" not in tpl

    def test_two_stage_format_does_not_crash(self):
        tpl = get_extract_stage1_initial_template(Discipline.MATH)
        filled = tpl.format(current_block="DUMMY_BLOCK", retry_feedback="")
        assert "DUMMY_BLOCK" in filled


class TestAnnotateStage2InitialPrompt:
    def test_math_has_runtime_placeholders(self):
        tpl = get_annotate_stage2_initial_template(Discipline.MATH)
        assert "{item_json}" in tpl
        assert "{tag_subtree}" in tpl
        assert "{taxonomy_tree}" in tpl
        assert "{retry_feedback}" in tpl

    def test_two_stage_format_does_not_crash(self):
        tpl = get_annotate_stage2_initial_template(Discipline.MATH)
        filled = tpl.format(
            item_json="{}",
            tag_subtree="(空)",
            taxonomy_tree="math.analysis",
            source_genre_hint="",
            retry_feedback="",
        )
        assert "math.analysis" in filled


class TestVlmPrompt:
    def test_math_has_runtime_placeholders(self):
        tpl = get_vlm_prompt_template(Discipline.MATH)
        assert "{page_number}" in tpl
        assert "{total_pages}" in tpl

    def test_physics_distinct_from_math(self):
        mtp = get_vlm_prompt_template(Discipline.MATH)
        ptp = get_vlm_prompt_template(Discipline.PHYSICS)
        assert mtp != ptp

    def test_default_constant_is_math(self):
        assert "{{FIGURE:" in VLM_CONVERT_PROMPT or "FIGURE" in VLM_CONVERT_PROMPT


class TestVlmConverterSystemPrompt:
    def test_vlm_converter_builds_system_prompt(self):
        from unittest.mock import MagicMock

        from sciforge.pipeline.vlm_converter import VlmConverter

        llm = MagicMock()
        conv_math = VlmConverter(llm_client=llm, discipline=Discipline.MATH)
        conv_phys = VlmConverter(llm_client=llm, discipline=Discipline.PHYSICS)
        assert conv_math._system_prompt != conv_phys._system_prompt
