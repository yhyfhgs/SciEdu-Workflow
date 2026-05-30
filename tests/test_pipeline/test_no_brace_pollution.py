"""回归测试：防止 LaTeX 双大括号污染回归。

历史 bug：各 Stage 对传入 LLM 的 value 做了多余的 `.replace("{", "{{").replace("}", "}}")`，
让 LLM 看到双大括号后按样输出，污染 DB。修复后，prompt 构造函数不应对 value 再次转义。
"""

from __future__ import annotations

import json

import pytest

from sciforge.core.models import Discipline
from sciforge.pipeline.source_classifier import format_baseline_hint
from sciforge.prompts.templates import (
    get_annotate_stage2_initial_template,
    get_annotate_stage2_review_template,
    get_extract_stage1_initial_template,
    get_extract_stage1_review_template,
    get_vlm_prompt_template,
)

_SAMPLE_WINDOW = (
    "     10 | \\begin{problem}[第1题]\n"
    "     11 | 求 $\\frac{1}{x+1}$ 的积分。\n"
    "     12 | \\end{problem}\n"
)
_SAMPLE_ITEM_JSON = json.dumps(
    {
        "category": "question",
        "corrected_content": "\\begin{problem}...\\end{problem}",
        "corrected_solution": "\\begin{solution}\\frac{a}{b}\\end{solution}",
    },
    ensure_ascii=False,
)


def _assert_no_double_brace_around_keywords(prompt: str) -> None:
    """确保 prompt 里 `\\begin{xxx}` / `\\frac{a}{b}` 不会因为转义变成双大括号"""
    for forbidden in (
        "\\begin{{problem}}",
        "\\end{{problem}}",
        "\\frac{{1}}{{x+1}}",
        "\\begin{{solution}}",
        "\\frac{{a}}{{b}}",
    ):
        assert forbidden not in prompt, (
            f"prompt 含污染片段 {forbidden!r}；应为单大括号。完整 prompt 片段 = "
            f"{prompt[max(0, prompt.find(forbidden) - 50) : prompt.find(forbidden) + 80]}"
        )


class TestStage1InitialPrompt:
    def test_single_brace_preserved_in_window(self) -> None:
        template = get_extract_stage1_initial_template(Discipline.MATH)
        prompt = template.format(current_block=_SAMPLE_WINDOW, retry_feedback="")
        _assert_no_double_brace_around_keywords(prompt)
        assert "\\begin{problem}" in prompt
        assert "\\frac{1}{x+1}" in prompt


class TestStage1ReviewPrompt:
    def test_single_brace_preserved(self) -> None:
        template = get_extract_stage1_review_template(Discipline.MATH)
        prompt = template.format(
            window_text=_SAMPLE_WINDOW,
            items_json=f"[{_SAMPLE_ITEM_JSON}]",
            retry_feedback="",
        )
        _assert_no_double_brace_around_keywords(prompt)
        assert "\\begin{problem}" in prompt


class TestStage2InitialPrompt:
    def test_single_brace_preserved_in_item_json(self) -> None:
        template = get_annotate_stage2_initial_template(Discipline.MATH)
        prompt = template.format(
            item_json=_SAMPLE_ITEM_JSON,
            tag_subtree="(空)",
            taxonomy_tree="- math\n  - algebra",
            source_genre_hint="",
            retry_feedback="",
        )
        _assert_no_double_brace_around_keywords(prompt)

    def test_genre_hint_inserted(self) -> None:
        template = get_annotate_stage2_initial_template(Discipline.MATH)
        hint = format_baseline_hint("Raw Data/新领军真题整理/2021.3.tex")
        prompt = template.format(
            item_json=_SAMPLE_ITEM_JSON,
            tag_subtree="(空)",
            taxonomy_tree="- math",
            source_genre_hint=hint,
            retry_feedback="",
        )
        assert "竞赛/真题" in prompt
        assert "difficulty 区间为 **7-10**" in prompt


class TestStage2ReviewPrompt:
    def test_single_brace_preserved(self) -> None:
        template = get_annotate_stage2_review_template(Discipline.MATH)
        prompt = template.format(
            item_json=_SAMPLE_ITEM_JSON,
            annotation_json='{"subject":"algebra:代数","tags":["math:数学","algebra:代数"]}',
            tag_subtree="(空)",
            taxonomy_tree="- math\n  - algebra",
            source_genre_hint="",
            retry_feedback="",
        )
        _assert_no_double_brace_around_keywords(prompt)


class TestVlmPrompt:
    def test_single_brace_preserved_in_previous_context(self) -> None:
        template = get_vlm_prompt_template(Discipline.MATH)
        # previous_context 可能含 LaTeX 公式（跨页续接片段）
        ctx = "上一页末尾: \\frac{a}{b} \\begin{theorem} ..."
        prompt = template.format(
            page_number="第 5 页",
            total_pages=10,
            previous_context=ctx,
            figure_hint="",
        )
        assert "\\frac{a}{b}" in prompt
        assert "\\frac{{a}}{{b}}" not in prompt
        assert "\\begin{theorem}" in prompt
        assert "\\begin{{theorem}}" not in prompt


class TestStageBuildPromptIntegration:
    """集成测试：走 Stage 的 build_prompt 构造流程，不走 format() 单元"""

    def test_stage1_extract_build_prompt_no_pollution(self) -> None:
        from sciforge.config.settings import AppConfig
        from sciforge.pipeline.stages.stage1_extract import Stage1Extract

        class _FakeLLM:
            async def generate_json(self, *args: object, **kwargs: object) -> dict:
                return {}

            def enable_pipeline_logging(self, *a: object, **k: object) -> None:
                pass

        stage = Stage1Extract(
            llm_client=_FakeLLM(),  # type: ignore[arg-type]
            config=AppConfig(),
            checkpoint_root=None,
            discipline=Discipline.MATH,
        )
        import inspect

        src = inspect.getsource(stage._extract_batch)
        # 断言新的实现不调用 safe_chunk 转义
        assert "safe_chunk = " not in src

    def test_stage1_review_no_escape(self) -> None:
        import inspect

        from sciforge.pipeline.stages.stage1_review import Stage1Review

        src = inspect.getsource(Stage1Review._review_batch)
        assert "safe_window = " not in src
        assert "safe_item = " not in src

    def test_stage2_annotate_no_escape(self) -> None:
        import inspect

        from sciforge.pipeline.stages.stage2_annotate import Stage2Annotate

        src = inspect.getsource(Stage2Annotate._annotate_one)
        assert "safe_item = " not in src
        assert "safe_tags = " not in src
        assert "safe_taxo = " not in src

    def test_stage2_review_no_escape(self) -> None:
        import inspect

        from sciforge.pipeline.stages.stage2_review import Stage2Review

        src = inspect.getsource(Stage2Review._review_one)
        assert "safe_item = " not in src
        assert "safe_annot = " not in src
        assert "safe_tags = " not in src
        assert "safe_taxo = " not in src

    def test_vlm_converter_no_escape(self) -> None:
        import inspect

        from sciforge.pipeline.vlm_converter import VlmConverter

        # 整个文件源码层面确认无 safe_context
        src = inspect.getsource(VlmConverter)
        assert "safe_context = " not in src


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
