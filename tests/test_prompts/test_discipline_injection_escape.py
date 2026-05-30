"""_inject_discipline_vars 对含花括号的 prompt_vars value 应自动转义

覆盖修复 H4：即使 DisciplineSpec.prompt_vars 里的 value 含裸 `{` `}`，
注入后下游 .format() 不应抛 KeyError。
"""

from __future__ import annotations

from sciforge.core.models import Discipline
from sciforge.disciplines.registry import DisciplineRegistry, DisciplineSpec
from sciforge.prompts.templates import _inject_discipline_vars


def test_inject_preserves_bare_latex_braces(monkeypatch) -> None:
    """prompt_vars 中含 {kg} 等裸 LaTeX，注入+format 后应还原"""
    fake_spec = DisciplineSpec(
        discipline=Discipline.OTHER,
        display_name="测试",
        allowed_kp_types=("theorem",),
        allowed_question_types=("calculation",),
        tag_root="test:测试",
        prompt_vars={
            "discipline_latex_example": "$m=2\\,\\mathrm{kg}$ 和 $h=5\\,\\mathrm{m}$",
        },
    )
    fake_registry = DisciplineRegistry({Discipline.OTHER: fake_spec})

    # 替换全局 registry 以便 _inject_discipline_vars 读到 fake_spec
    monkeypatch.setattr("sciforge.prompts.templates.DISCIPLINE_REGISTRY", fake_registry)

    template = "EXAMPLE: {discipline_latex_example}\nRUNTIME: {current_block}"
    injected = _inject_discipline_vars(template, Discipline.OTHER)

    # 下游 .format() 消费 {current_block} 占位符，不应因 {kg}/{m} 抛 KeyError
    filled = injected.format(current_block="BLK")

    assert "\\mathrm{kg}" in filled
    assert "\\mathrm{m}" in filled
    assert "BLK" in filled


def test_inject_with_json_example_value(monkeypatch) -> None:
    """prompt_vars 中含完整 JSON 片段（大量 { }），注入后应安全"""
    fake_spec = DisciplineSpec(
        discipline=Discipline.OTHER,
        display_name="测试",
        allowed_kp_types=("theorem",),
        allowed_question_types=("calculation",),
        tag_root="test:测试",
        prompt_vars={
            "discipline_json_example": '{"tags": ["math:数学"], "difficulty": 5}',
        },
    )
    fake_registry = DisciplineRegistry({Discipline.OTHER: fake_spec})
    monkeypatch.setattr("sciforge.prompts.templates.DISCIPLINE_REGISTRY", fake_registry)

    template = "EX: {discipline_json_example}\nRT: {current_block}"
    injected = _inject_discipline_vars(template, Discipline.OTHER)
    filled = injected.format(current_block="BLOCK")

    assert '"tags"' in filled
    assert '"difficulty": 5' in filled
    assert "BLOCK" in filled


def test_no_double_escape_for_pure_text(monkeypatch) -> None:
    """不含 { } 的纯文本 value 注入应保持原样"""
    fake_spec = DisciplineSpec(
        discipline=Discipline.OTHER,
        display_name="测试",
        allowed_kp_types=("theorem",),
        allowed_question_types=("calculation",),
        tag_root="test:测试",
        prompt_vars={
            "discipline_role": "专家",
        },
    )
    fake_registry = DisciplineRegistry({Discipline.OTHER: fake_spec})
    monkeypatch.setattr("sciforge.prompts.templates.DISCIPLINE_REGISTRY", fake_registry)

    template = "ROLE: {discipline_role}\nBLK: {current_block}"
    injected = _inject_discipline_vars(template, Discipline.OTHER)
    assert "ROLE: 专家" in injected
    filled = injected.format(current_block="BLOCK")
    assert filled == "ROLE: 专家\nBLK: BLOCK"
