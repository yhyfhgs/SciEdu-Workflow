"""QGen Generator 变式策略加载单测

回归防护：generator 曾只把 `req.strategy.value`（枚举字符串）注入 prompt，
从未加载 strategies/*.txt 正文。此处验证 8 种策略正文被真正加载并安全注入，
且 free / variant 两条 `.format()` 路径都不被策略正文 / 源题里的 LaTeX 花括号破坏。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sciforge.agents.qgen.generator import QuestionGenerator
from sciforge.core.models import (
    ContextBundle,
    Discipline,
    GenerationRequest,
    Question,
    VariantStrategy,
)


def _gen(discipline: Discipline = Discipline.MATH) -> QuestionGenerator:
    return QuestionGenerator(llm=MagicMock(), discipline=discipline)


def test_all_strategy_templates_loaded() -> None:
    """8 种变式策略都加载到非空正文（而非枚举字符串本身）"""
    gen = _gen()
    assert set(gen._strategy_templates) == set(VariantStrategy)
    for s, body in gen._strategy_templates.items():
        assert body.strip(), f"{s.value} 正文为空"
        assert body.strip() != s.value, f"{s.value} 仍是枚举字符串而非正文"
        assert len(body) > 50, f"{s.value} 正文过短，疑似未加载文件"


def test_build_prompt_free_path_injects_strategy_body() -> None:
    """free 路径：每个策略的 _build_prompt 能成功 format，且策略正文被整段注入"""
    gen = _gen()
    ctx = ContextBundle()
    for s in VariantStrategy:
        req = GenerationRequest(
            topics=["math:数学", "analysis:数学分析"],
            difficulty_target=5,
            strategy=s,
        )
        prompt = gen._build_prompt(req, ctx)  # 不应抛 KeyError/IndexError/ValueError
        assert gen._strategy_templates[s] in prompt, f"{s.value} 正文未注入 free prompt"
        assert s.value not in prompt.replace(gen._strategy_templates[s], ""), (
            f"{s.value} 枚举字符串仍残留"
        )


def test_build_prompt_variant_path_preserves_latex_braces() -> None:
    """variant 路径 + 源题含 LaTeX 花括号：format 不二次解析、不报错，花括号原样保留"""
    gen = _gen()
    ctx = ContextBundle()
    src = Question(
        discipline=Discipline.MATH,
        question_type="proof",
        content="设 $A \\in \\mathbb{R}^{n\\times n}$，证明 $\\det(A) \\neq 0$ 时 $A$ 可逆。",
    )
    for s in VariantStrategy:
        req = GenerationRequest(
            topics=["math:数学"],
            difficulty_target=6,
            strategy=s,
            source_question=src,
        )
        prompt = gen._build_prompt(req, ctx)
        assert gen._strategy_templates[s] in prompt, f"{s.value} 正文未注入 variant prompt"
        assert "\\mathbb{R}" in prompt, "源题 LaTeX 花括号被 .format() 破坏"


def test_physics_strategy_templates_loaded() -> None:
    """物理学科同样能加载策略正文（走学科词替换路径）"""
    gen = _gen(Discipline.PHYSICS)
    assert set(gen._strategy_templates) == set(VariantStrategy)
    for body in gen._strategy_templates.values():
        assert body.strip()
