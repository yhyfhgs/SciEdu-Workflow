"""Pipeline prompt 两阶段注入的单元测试

验证关键不变式：
1. 第一阶段 str.replace 正确注入学科变量
2. 第二阶段 .format() 不会与 JSON 示例的 {{}} 转义冲突
3. 物理与数学模板内容正确隔离
4. VlmConverter 的 system prompt 和 user prompt 双路径都被参数化
"""

from sciforge.core.models import Discipline
from sciforge.prompts.templates import (
    EXTRACT_AND_CLASSIFY_PROMPT,
    VLM_CONVERT_PROMPT,
    get_extract_prompt_template,
    get_vlm_prompt_template,
)


class TestExtractPrompt:
    def test_math_has_math_expert(self):
        tpl = get_extract_prompt_template(Discipline.MATH)
        assert "数学文档结构化提取专家" in tpl

    def test_math_has_runtime_placeholders(self):
        """注入学科变量后，runtime 占位符必须保留"""
        tpl = get_extract_prompt_template(Discipline.MATH)
        assert "{current_block}" in tpl
        assert "{tag_subtree}" in tpl

    def test_math_has_math_example_subject(self):
        tpl = get_extract_prompt_template(Discipline.MATH)
        assert "real_analysis:实分析" in tpl

    def test_math_has_theorem_type(self):
        tpl = get_extract_prompt_template(Discipline.MATH)
        assert "theorem" in tpl
        assert "引理" in tpl or "lemma" in tpl

    def test_physics_has_physics_expert(self):
        tpl = get_extract_prompt_template(Discipline.PHYSICS)
        assert "物理文档结构化提取专家" in tpl
        # 确保没有数学特化
        assert "数学文档结构化提取专家" not in tpl

    def test_physics_has_law_type(self):
        tpl = get_extract_prompt_template(Discipline.PHYSICS)
        assert "law" in tpl  # 定律
        assert "principle" in tpl  # 原理

    def test_physics_has_physics_example(self):
        tpl = get_extract_prompt_template(Discipline.PHYSICS)
        assert "classical_mechanics:经典力学" in tpl

    def test_two_stage_format_does_not_crash(self):
        """第二阶段 .format() 能正确消费 JSON 示例的 {{}} 转义"""
        tpl = get_extract_prompt_template(Discipline.MATH)
        # 第二阶段注入
        filled = tpl.format(current_block="DUMMY_BLOCK", tag_subtree="DUMMY_TREE")
        assert "DUMMY_BLOCK" in filled
        assert "DUMMY_TREE" in filled
        # JSON 示例的 {{ 应变回单 {
        assert '"items":' in filled or '"items"' in filled
        assert '"tags":' in filled or '"tags"' in filled

    def test_two_stage_format_physics(self):
        tpl = get_extract_prompt_template(Discipline.PHYSICS)
        filled = tpl.format(current_block="BLK", tag_subtree="TREE")
        assert "BLK" in filled
        assert "TREE" in filled
        # 物理的示例题目内容应出现（未被 format 误解）
        assert "kg" in filled  # "$m=2\\,\\mathrm{kg}$" → "$m=2\\,\\mathrm{kg}$"

    def test_default_constant_is_math(self):
        """EXTRACT_AND_CLASSIFY_PROMPT 默认是数学注入版"""
        assert "数学文档结构化提取专家" in EXTRACT_AND_CLASSIFY_PROMPT
        assert "{current_block}" in EXTRACT_AND_CLASSIFY_PROMPT


class TestVlmPrompt:
    def test_math_has_math_vlm_expert(self):
        tpl = get_vlm_prompt_template(Discipline.MATH)
        assert "高精度数学文档转写专家" in tpl

    def test_math_has_runtime_placeholders(self):
        tpl = get_vlm_prompt_template(Discipline.MATH)
        assert "{page_number}" in tpl
        assert "{total_pages}" in tpl
        assert "{previous_context}" in tpl
        assert "{figure_hint}" in tpl

    def test_physics_has_physics_vlm_expert(self):
        tpl = get_vlm_prompt_template(Discipline.PHYSICS)
        assert "高精度物理文档转写专家" in tpl
        assert "高精度数学文档转写专家" not in tpl

    def test_physics_figure_caption(self):
        tpl = get_vlm_prompt_template(Discipline.PHYSICS)
        assert "斜面受力分析" in tpl

    def test_math_figure_caption(self):
        tpl = get_vlm_prompt_template(Discipline.MATH)
        assert "Cayley 图" in tpl

    def test_two_stage_format_does_not_crash(self):
        tpl = get_vlm_prompt_template(Discipline.MATH)
        filled = tpl.format(
            page_number="第 3 页",
            total_pages=10,
            previous_context="",
            figure_hint="",
        )
        assert "第 3 页" in filled
        # FIGURE 占位符应被消费为单括号
        assert "{{FIGURE:" in filled  # 原文 4 层 → 消费后 2 层（LLM 实际看到的）

    def test_default_constant_is_math(self):
        assert "高精度数学文档转写专家" in VLM_CONVERT_PROMPT


class TestVlmConverterSystemPrompt:
    """vlm_converter.py 的 _SYSTEM_PROMPT 也要按学科参数化"""

    def test_system_prompt_template_has_placeholder(self):
        from sciforge.pipeline.vlm_converter import _SYSTEM_PROMPT_TEMPLATE

        assert "{discipline_vlm_expert}" in _SYSTEM_PROMPT_TEMPLATE

    def test_vlm_converter_builds_system_prompt(self):
        """VlmConverter 构造时应按 discipline 生成实例级 system prompt"""
        from unittest.mock import MagicMock

        from sciforge.pipeline.vlm_converter import VlmConverter

        llm = MagicMock()
        # Math
        conv_math = VlmConverter(llm_client=llm, discipline=Discipline.MATH)
        assert "高精度数学文档转写专家" in conv_math._system_prompt
        assert "{discipline_vlm_expert}" not in conv_math._system_prompt

        # Physics
        conv_phys = VlmConverter(llm_client=llm, discipline=Discipline.PHYSICS)
        assert "高精度物理文档转写专家" in conv_phys._system_prompt
        assert "高精度数学文档转写专家" not in conv_phys._system_prompt

    def test_vlm_converter_user_prompt_template(self):
        from unittest.mock import MagicMock

        from sciforge.pipeline.vlm_converter import VlmConverter

        llm = MagicMock()
        conv = VlmConverter(llm_client=llm, discipline=Discipline.PHYSICS)
        # 实例级 user prompt 模板按学科注入
        assert "高精度物理文档转写专家" in conv._prompt_template
        assert "{page_number}" in conv._prompt_template  # runtime placeholder 保留
