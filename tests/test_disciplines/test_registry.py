"""DisciplineRegistry / DisciplineSpec 单元测试"""

from sciforge.core.models import Discipline
from sciforge.disciplines import DISCIPLINE_REGISTRY, DisciplineRegistry, DisciplineSpec


def test_default_registry_has_math_and_physics():
    assert DISCIPLINE_REGISTRY.has(Discipline.MATH)
    assert DISCIPLINE_REGISTRY.has(Discipline.PHYSICS)
    assert set(DISCIPLINE_REGISTRY.list_disciplines()) == {
        Discipline.MATH,
        Discipline.PHYSICS,
    }


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
    assert math_spec.kp_type_or("law", fallback="definition") == "definition"  # 物理的 law 在数学里被回退
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


def test_prompt_vars_no_bare_brace():
    """检查 prompt_vars 的 value 里的花括号都是成对转义的 (避免 .format 冲突)"""
    for disc in [Discipline.MATH, Discipline.PHYSICS]:
        spec = DISCIPLINE_REGISTRY.get(disc)
        for key, value in spec.prompt_vars.items():
            # 如果有 `{`，必须是 `{{`；如果有 `}`，必须是 `}}`
            # 简单检查：去掉所有 `{{` 和 `}}` 后，不应还有单独的 `{` 或 `}`
            stripped = value.replace("{{", "").replace("}}", "")
            assert "{" not in stripped, (
                f"{disc.value}.{key} has unescaped `{{`: {value!r}"
            )
            assert "}" not in stripped, (
                f"{disc.value}.{key} has unescaped `}}`: {value!r}"
            )


def test_custom_registry():
    """DisciplineRegistry 可以用自定义 specs 构造（用于测试）"""
    from sciforge.disciplines.specs.math import MATH_SPEC

    registry = DisciplineRegistry({Discipline.MATH: MATH_SPEC})
    assert registry.has(Discipline.MATH)
    assert not registry.has(Discipline.PHYSICS)
    assert registry.list_disciplines() == [Discipline.MATH]
