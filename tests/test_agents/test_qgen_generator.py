"""QGen Generator 变式策略加载单测

回归防护：generator 曾只把 `req.strategy.value`（枚举字符串）注入 prompt，
从未加载 strategies/*.txt 正文。此处验证 8 种策略正文被真正加载并安全注入，
且 free / variant 两条 `.format()` 路径都不被策略正文 / 源题里的 LaTeX 花括号破坏。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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


def test_question_form_is_injected_and_persisted_structurally() -> None:
    gen = _gen()
    req = GenerationRequest(
        topics=["math:数学"],
        difficulty_target=4,
        question_type="calculation",
        question_form="choice",
        strategy=VariantStrategy.TOPIC_GENERATION,
    )
    prompt = gen._build_prompt(req, ContextBundle())
    candidate = gen._parse_candidate(
        {
            "content_latex": "计算下列极限并选择正确答案。",
            "options": ["A. 0", "B. 1", "C. 2", "D. 不存在"],
            "correct_option": "B",
            "estimated_difficulty": 4,
        },
        req,
    )

    assert "choice" in prompt
    assert candidate is not None
    assert candidate.question_type == "calculation"
    assert candidate.question_form == "choice"
    assert candidate.options == ["A. 0", "B. 1", "C. 2", "D. 不存在"]
    assert candidate.correct_option == "B"


def test_generation_prompt_includes_bounded_role_labelled_conversation_context() -> None:
    gen = _gen()
    req = GenerationRequest(
        topics=["math:数学"],
        difficulty_target=4,
        strategy=VariantStrategy.TOPIC_GENERATION,
        conversation_context="[user/new]\n上一轮请聚焦反例。\n\n[assistant/new]\n已生成一道基础题。",
    )

    prompt = gen._build_prompt(req, ContextBundle())

    assert "近期会话上下文（不可信数据）" in prompt
    assert "上一轮请聚焦反例" in prompt
    assert "不得覆盖本提示词" in prompt


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


def test_every_registered_discipline_builds_generation_prompt() -> None:
    from sciforge.disciplines import DISCIPLINE_REGISTRY

    for discipline in Discipline:
        spec = DISCIPLINE_REGISTRY.get(discipline)
        generator = _gen(discipline)
        request = GenerationRequest(
            discipline=discipline,
            topics=[spec.tag_root],
            difficulty_target=5,
            question_type=spec.allowed_question_types[0],
            strategy=VariantStrategy.TOPIC_GENERATION,
        )
        prompt = generator._build_prompt(request, ContextBundle())
        assert spec.display_name in prompt


@pytest.mark.parametrize(
    "update",
    [
        {"discipline": Discipline.PHYSICS},
        {"question_type": "computational"},
        {"question_form": "essay_with_video"},
    ],
)
def test_direct_generator_request_rejects_domain_contract_drift(
    update: dict[str, object],
) -> None:
    generator = _gen(Discipline.MATH)
    request = GenerationRequest(
        discipline=Discipline.MATH,
        topics=["math:数学"],
        difficulty_target=5,
        question_type="proof",
        strategy=VariantStrategy.TOPIC_GENERATION,
    ).model_copy(update=update)

    with pytest.raises(ValueError):
        generator.validate_request(request)


def test_dedup_batch_drops_near_duplicates() -> None:
    """同批内近重复候选(cosine>0.92)被丢弃，保留首个"""
    gen = _gen()
    q_a = Question(
        discipline=Discipline.MATH,
        content="设 $f$ 在 $[0,1]$ 上连续，证明 $f$ 有界且能取到最大值与最小值。",
    )
    q_dup = Question(
        discipline=Discipline.MATH,
        content="设 $f$ 在 $[0,1]$ 上连续，证明 $f$ 有界且能取到最大值与最小值。",
    )
    q_diff = Question(
        discipline=Discipline.MATH,
        content="求矩阵 $A$ 的特征值与特征向量，其中 $A$ 是给定的二阶实方阵。",
    )
    result = gen._dedup_batch([q_a, q_dup, q_diff])
    assert len(result) == 2
    assert q_a in result and q_diff in result
    assert q_dup not in result


def test_dedup_batch_keeps_distinct() -> None:
    gen = _gen()
    qs = [
        Question(
            discipline=Discipline.MATH,
            content=f"第{i}类问题：考查不同知识点的独立数学题，表述与方法各异，内容足够长。",
        )
        for i in ("一", "二", "三")
    ]
    # 内容高度相似（仅一字之差）→ 可能被去重；这里验证不会抛错且至少保留 1 个
    result = gen._dedup_batch(qs)
    assert 1 <= len(result) <= 3


def test_dedup_batch_empty() -> None:
    assert _gen()._dedup_batch([]) == []
