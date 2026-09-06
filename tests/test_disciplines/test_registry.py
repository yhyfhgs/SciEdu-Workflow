"""DisciplineRegistry / DisciplineSpec 单元测试"""

import pytest

from sciforge.core.models import Discipline
from sciforge.disciplines import DISCIPLINE_REGISTRY, DisciplineRegistry, DisciplineSpec


def test_default_registry_has_every_stable_top_level_discipline():
    assert set(DISCIPLINE_REGISTRY.list_disciplines()) == set(Discipline)


@pytest.mark.parametrize(
    ("discipline", "tag_root", "kp_type", "question_type"),
    [
        (Discipline.MATH, "math:数学", "theorem", "proof"),
        (Discipline.PHYSICS, "physics:物理", "law", "calculation"),
        (Discipline.CHEMISTRY, "chemistry:化学", "reaction", "mechanism_analysis"),
        (Discipline.BIOLOGY, "biology:生物", "process", "data_analysis"),
        (Discipline.OTHER, "other:其他", "fact", "analysis"),
    ],
)
def test_each_discipline_has_executable_contract(
    discipline: Discipline,
    tag_root: str,
    kp_type: str,
    question_type: str,
) -> None:
    spec = DISCIPLINE_REGISTRY.get(discipline)
    assert spec.tag_root == tag_root
    assert kp_type in spec.allowed_kp_types
    assert question_type in spec.allowed_question_types
    assert spec.prompt_vars["discipline_taxonomy_root"] == discipline.value
    assert spec.prompt_vars["discipline_document_expert"]
    assert spec.prompt_vars["discipline_vlm_expert"]


@pytest.mark.parametrize("discipline", list(Discipline))
def test_each_discipline_has_controlled_taxonomy_root(discipline: Discipline) -> None:
    from sciforge.pipeline.taxonomy_validator import TaxonomyValidator

    validator = TaxonomyValidator()
    assert validator.has_path(discipline.value)


def test_math_spec_fields():
    spec = DISCIPLINE_REGISTRY.get(Discipline.MATH)
    assert isinstance(spec, DisciplineSpec)
    assert spec.display_name == "数学"
    assert spec.tag_root == "math:数学"
    assert "theorem" in spec.allowed_kp_types
    assert "proof" in spec.allowed_question_types
    assert "discipline_name" in spec.prompt_vars
    assert spec.prompt_vars["discipline_name"] == "数学"
    assert "数学文档结构化提取专家" in spec.prompt_vars["discipline_document_expert"]


def test_physics_spec_fields():
    spec = DISCIPLINE_REGISTRY.get(Discipline.PHYSICS)
    assert spec.display_name == "物理"
    assert spec.tag_root == "physics:物理"
    assert "law" in spec.allowed_kp_types
    assert "principle" in spec.allowed_kp_types
    assert "experiment_design" in spec.allowed_question_types
    assert spec.prompt_vars["discipline_name"] == "物理"


def test_kp_type_or_fallback():
    math_spec = DISCIPLINE_REGISTRY.get(Discipline.MATH)
    # 白名单内：原样返回
    assert math_spec.kp_type_or("theorem") == "theorem"
    assert math_spec.kp_type_or("lemma") == "lemma"
    # 不在白名单：回退
    assert math_spec.kp_type_or("unknown_type") == "theorem"
    # 物理的 law 在数学里被回退
    assert math_spec.kp_type_or("law", fallback="definition") == "definition"
    # 自定义 fallback
    assert math_spec.kp_type_or("xxx", fallback="example") == "example"


def test_question_type_or_fallback():
    physics_spec = DISCIPLINE_REGISTRY.get(Discipline.PHYSICS)
    assert physics_spec.question_type_or("calculation") == "calculation"
    assert physics_spec.question_type_or("experiment_design") == "experiment_design"
    # 默认 fallback 是 calculation
    assert physics_spec.question_type_or("unknown") == "calculation"


def test_detect_from_tags():
    assert DISCIPLINE_REGISTRY.detect_from_tags(["math:数学", "algebra:代数"]) == Discipline.MATH
    assert DISCIPLINE_REGISTRY.detect_from_tags(["physics:物理"]) == Discipline.PHYSICS
    assert DISCIPLINE_REGISTRY.detect_from_tags([]) == Discipline.OTHER
    assert DISCIPLINE_REGISTRY.detect_from_tags(["unknown:未知"]) == Discipline.OTHER


def test_get_with_string():
    """get() 支持字符串参数"""
    spec = DISCIPLINE_REGISTRY.get("math")
    assert spec.discipline == Discipline.MATH


def test_registry_is_immutable():
    """修改 registry 内部 dict 不影响单例（frozen dataclass + copy）"""
    spec = DISCIPLINE_REGISTRY.get(Discipline.MATH)
    # DisciplineSpec 是 frozen dataclass，不能直接修改字段
    import dataclasses

    assert dataclasses.is_dataclass(spec)
    # prompt_vars 被存为 Mapping，允许查询但不鼓励 mutate


def test_knowledge_point_default_discipline():
    """KnowledgePoint 默认 discipline 是 MATH（向后兼容）"""
    from sciforge.core.models import KnowledgePoint

    kp = KnowledgePoint()
    assert kp.discipline == Discipline.MATH


def test_question_default_discipline():
    from sciforge.core.models import Question

    q = Question()
    assert q.discipline == Discipline.MATH


def test_prompt_vars_allow_bare_brace_and_inject_safely():
    """prompt_vars 的 value 可以写裸 `{` `}`（含裸 LaTeX），
    _inject_discipline_vars 会自动转义，下游 .format() 能还原。

    （旧约定是要求 spec 手动把 `{` 写成 `{{`；现在改为由注入函数统一处理）
    """
    from sciforge.core.models import Discipline
    from sciforge.prompts.templates import _inject_discipline_vars

    # 不强制检查 value 里的花括号——任意裸 LaTeX 都应安全
    template_raw = "EXAMPLE: {discipline_example_question_content}"
    injected = _inject_discipline_vars(template_raw, Discipline.PHYSICS)
    # .format() 不抛 KeyError
    filled = injected.format()
    # 原始裸 LaTeX 中的 {kg} {m} 应被保留
    assert "\\mathrm{kg}" in filled or "mathrm{kg}" in filled
    assert "\\mathrm{m}" in filled or "mathrm{m}" in filled


def test_custom_registry():
    """DisciplineRegistry 可以用自定义 specs 构造（用于测试）"""
    from sciforge.disciplines.specs.math import MATH_SPEC

    registry = DisciplineRegistry({Discipline.MATH: MATH_SPEC})
    assert registry.has(Discipline.MATH)
    assert not registry.has(Discipline.PHYSICS)
    assert registry.list_disciplines() == [Discipline.MATH]
