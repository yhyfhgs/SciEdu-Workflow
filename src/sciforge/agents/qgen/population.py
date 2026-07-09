"""QGen 候选池的确定性多目标评分与选择。"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from sciforge.core.models import ContextBundle, GeneratedQuestion, GenerationRequest

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*|[\u4e00-\u9fff]|\d+(?:\.\d+)?")


class CandidateObjective(BaseModel):
    candidate_id: str
    correctness: float = Field(ge=0.0, le=1.0)
    difficulty_fit: float = Field(ge=0.0, le=1.0)
    novelty: float = Field(ge=0.0, le=1.0)
    pedagogical_value: float = Field(ge=0.0, le=1.0)
    verification_strength: float = Field(ge=0.0, le=1.0)
    overall: float = Field(ge=0.0, le=1.0)


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text)}


def _similarity(left: str, right: str) -> float:
    lhs = _tokens(left)
    rhs = _tokens(right)
    if not lhs or not rhs:
        return 0.0
    return len(lhs & rhs) / len(lhs | rhs)


def score_candidate(
    generated: GeneratedQuestion,
    request: GenerationRequest,
    context: ContextBundle | None,
) -> CandidateObjective:
    verification = generated.verification
    correctness = 1.0 if verification.passed else 0.0
    difficulty_fit = max(
        0.0,
        1.0 - abs(float(generated.difficulty_score.overall) - request.difficulty_target) / 9.0,
    )

    references = (
        []
        if context is None
        else [
            question.content
            for question in [*context.reference_questions, *context.negative_examples]
        ]
    )
    max_similarity = max(
        (_similarity(generated.question.content, reference) for reference in references),
        default=0.0,
    )
    novelty = max(0.0, 1.0 - max_similarity)

    solution_length = len(generated.solution.solution_latex.strip())
    has_methods = bool(generated.solution.methods_used)
    pedagogical = min(1.0, solution_length / 800.0) * 0.75 + 0.25 * has_methods

    required = [evidence for evidence in verification.evidence if evidence.required]
    passed_required = sum(evidence.status.value == "passed" for evidence in required)
    verification_strength = passed_required / len(required) if required else correctness

    overall = correctness * (
        0.35 * difficulty_fit + 0.25 * novelty + 0.2 * pedagogical + 0.2 * verification_strength
    )
    return CandidateObjective(
        candidate_id=generated.question.id,
        correctness=round(correctness, 4),
        difficulty_fit=round(difficulty_fit, 4),
        novelty=round(novelty, 4),
        pedagogical_value=round(pedagogical, 4),
        verification_strength=round(verification_strength, 4),
        overall=round(overall, 4),
    )


def select_candidate(
    candidates: list[GeneratedQuestion],
    request: GenerationRequest,
    context: ContextBundle | None,
) -> tuple[GeneratedQuestion, list[CandidateObjective]]:
    if not candidates:
        raise ValueError("candidate population must not be empty")
    objectives = [score_candidate(candidate, request, context) for candidate in candidates]
    paired = list(zip(candidates, objectives, strict=True))
    # 稳定 tie-break：目标分 → 验证强度 → 难度贴合 → candidate id。
    selected, _objective = max(
        paired,
        key=lambda pair: (
            pair[1].overall,
            pair[1].verification_strength,
            pair[1].difficulty_fit,
            pair[0].question.id,
        ),
    )
    return selected, objectives


__all__ = ["CandidateObjective", "score_candidate", "select_candidate"]
