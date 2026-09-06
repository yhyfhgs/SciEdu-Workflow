"""生成路径 prompt 按学科参数化的测试

验证 `get_generation_prompt_template` 按 Discipline 做 str.replace 替换。
"""

from __future__ import annotations

from sciforge.core.models import Discipline
from sciforge.prompts.templates import get_generation_prompt_template


class TestFreeGenerationPrompt:
    def test_math_default(self):
        tpl = get_generation_prompt_template("generation/free_generation.txt", Discipline.MATH)
        assert "资深大学数学教育专家" in tpl
        assert "物理" not in tpl  # math 版本不应含其他学科

    def test_physics(self):
        tpl = get_generation_prompt_template("generation/free_generation.txt", Discipline.PHYSICS)
        assert "资深大学物理教育专家" in tpl
        assert "原创物理题" in tpl
        # 最后的兜底替换应把剩余"数学"改成"物理"
        assert "资深大学数学教育专家" not in tpl
        # 数学学科特化词也被替换
        assert "数学语言" not in tpl

    def test_variant_physics(self):
        tpl = get_generation_prompt_template(
            "generation/variant_generation.txt", Discipline.PHYSICS
        )
        assert "物理严谨性" in tpl
        assert "物理教育专家" in tpl
        assert "数学严谨性" not in tpl

    def test_chemistry(self):
        tpl = get_generation_prompt_template("generation/free_generation.txt", Discipline.CHEMISTRY)
        assert "化学" in tpl
        assert "资深大学数学教育专家" not in tpl


class TestSolvePrompt:
    def test_math_default(self):
        tpl = get_generation_prompt_template("solving/solve.txt", Discipline.MATH)
        assert "数学家" in tpl

    def test_physics(self):
        tpl = get_generation_prompt_template("solving/solve.txt", Discipline.PHYSICS)
        assert "物理学家" in tpl
        assert "数学家" not in tpl


class TestReviewPrompt:
    def test_physics_review(self):
        tpl = get_generation_prompt_template("verification/review.txt", Discipline.PHYSICS)
        assert "物理审稿人" in tpl or "物理" in tpl
        # 核心占位符不应被替换坏
        assert "{question_latex}" in tpl


class TestDifficultyScoringPrompt:
    def test_physics_scoring(self):
        tpl = get_generation_prompt_template(
            "verification/difficulty_scoring.txt", Discipline.PHYSICS
        )
        assert "物理教育评估专家" in tpl
        # 多维评分字段应保留
        assert "concept_count" in tpl
        assert "{question_latex}" in tpl
        assert "{solution_sketch}" in tpl


class TestQGenerationComponentsUsePhysicsPrompts:
    """验证 QGen 子组件初始化时按学科加载的 prompt 确实是参数化过的"""

    def test_question_generator_physics(self):
        from unittest.mock import MagicMock

        from sciforge.agents.qgen.generator import QuestionGenerator

        gen = QuestionGenerator(llm=MagicMock(), discipline=Discipline.PHYSICS)
        assert "资深大学物理教育专家" in gen._free_template
        assert "资深大学物理教育专家" in gen._variant_template

    def test_solve_engine_physics(self):
        from unittest.mock import MagicMock

        from sciforge.agents.qgen.solver import SolveEngine

        engine = SolveEngine(llm=MagicMock(), discipline=Discipline.PHYSICS)
        assert "物理学家" in engine._solve_template

    def test_verification_pipeline_physics(self):
        from unittest.mock import MagicMock

        from sciforge.agents.qgen.verifier import VerificationPipeline

        vp = VerificationPipeline(review_llm=MagicMock(), discipline=Discipline.PHYSICS)
        # 至少包含一个物理相关词
        assert "物理" in vp._review_template

    def test_annotator_physics(self):
        from unittest.mock import MagicMock

        from sciforge.agents.qgen.annotator import DifficultyAnnotator

        ann = DifficultyAnnotator(llm=MagicMock(), discipline=Discipline.PHYSICS)
        assert "物理" in ann._scoring_template
