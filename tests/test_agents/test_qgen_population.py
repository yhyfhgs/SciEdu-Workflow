from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sciforge.agents.qgen.agent import QGenAgent, QGenInput
from sciforge.agents.qgen.population import score_candidate, select_candidate
from sciforge.core.models import (
    ContextBundle,
    DifficultyScore,
    Discipline,
    GeneratedQuestion,
    GenerationRequest,
    Question,
    Solution,
    VariantStrategy,
    VerificationEvidence,
    VerificationEvidenceStatus,
    VerificationResult,
)


def _generated(
    candidate_id: str,
    content: str,
    *,
    difficulty: float,
    solution: str,
) -> GeneratedQuestion:
    score = DifficultyScore(overall=difficulty)
    return GeneratedQuestion(
        question=Question(id=candidate_id, content=content, discipline=Discipline.MATH),
        solution=Solution(
            question_id=candidate_id,
            solution_latex=solution,
            methods_used=["method"],
            confidence="high",
        ),
        difficulty_score=score,
        verification=VerificationResult(
            passed=True,
            checks={"review": True},
            evidence=[
                VerificationEvidence(
                    kind="review",
                    status=VerificationEvidenceStatus.PASSED,
                    required=True,
                )
            ],
        ),
    )


def test_population_selection_balances_target_difficulty_novelty_and_teaching_value() -> None:
    request = GenerationRequest(
        discipline=Discipline.MATH,
        subject="analysis",
        topics=["continuity"],
        difficulty_target=7,
        strategy=VariantStrategy.TOPIC_GENERATION,
    )
    context = ContextBundle(
        reference_questions=[
            Question(id="ref", content="Prove the standard epsilon delta definition verbatim.")
        ]
    )
    copied = _generated(
        "copied",
        "Prove the standard epsilon delta definition verbatim.",
        difficulty=7,
        solution="short",
    )
    novel = _generated(
        "novel",
        "Construct a continuous map with a prescribed modulus and justify the sharp bound.",
        difficulty=7,
        solution="Detailed argument. " * 35,
    )

    selected, objectives = select_candidate([copied, novel], request, context)

    by_id = {objective.candidate_id: objective for objective in objectives}
    assert by_id["novel"].novelty > by_id["copied"].novelty
    assert by_id["novel"].pedagogical_value > by_id["copied"].pedagogical_value
    assert selected.question.id == "novel"


def test_failed_candidate_gets_zero_overall_even_if_other_dimensions_are_high() -> None:
    request = GenerationRequest(
        discipline=Discipline.MATH,
        topics=["algebra"],
        difficulty_target=5,
        strategy=VariantStrategy.TOPIC_GENERATION,
    )
    candidate = _generated(
        "bad",
        "A long and novel but invalid candidate question with many conditions.",
        difficulty=5,
        solution="Detailed invalid proof. " * 30,
    )
    candidate.verification.passed = False

    objective = score_candidate(candidate, request, ContextBundle())

    assert objective.correctness == 0.0
    assert objective.overall == 0.0


@pytest.mark.asyncio
async def test_population_mode_evaluates_current_population_before_selecting() -> None:
    llm = MagicMock()
    agent = QGenAgent(
        llm=llm,
        storage=MagicMock(),
        candidates_per_round=2,
        max_rounds=1,
        lightweight_prefilter=False,
        population_selection_enabled=True,
    )
    request = GenerationRequest(
        discipline=Discipline.MATH,
        subject="analysis",
        topics=["continuity"],
        difficulty_target=7,
        strategy=VariantStrategy.TOPIC_GENERATION,
    )
    first = Question(
        id="first",
        content="Prove a standard continuity statement with every condition explicitly stated.",
    )
    second = Question(
        id="second",
        content="Construct a sharp modulus of continuity and prove the resulting optimal estimate.",
    )
    agent.retriever.build_context = MagicMock(return_value=ContextBundle())
    agent.generator.generate_candidates = AsyncMock(return_value=[first, second])
    agent.solver.solve = AsyncMock(
        side_effect=[
            Solution(solution_latex="Brief proof.", methods_used=["direct"], confidence="high"),
            Solution(
                solution_latex="Detailed constructive proof. " * 30,
                methods_used=["construction"],
                confidence="high",
            ),
        ]
    )
    verification = VerificationResult(
        passed=True,
        checks={"review": True},
        evidence=[
            VerificationEvidence(
                kind="review",
                status=VerificationEvidenceStatus.PASSED,
                required=True,
            )
        ],
    )
    agent.verifier.verify = AsyncMock(return_value=verification)
    agent.annotator.annotate = AsyncMock(
        side_effect=[DifficultyScore(overall=4.0), DifficultyScore(overall=7.0)]
    )

    result = await agent.run(QGenInput(request=request))

    assert agent.solver.solve.await_count == 2
    assert agent.verifier.verify.await_count == 2
    assert result.generated is not None
    assert result.generated.question.id == "second"
    assert result.generated.generation_meta["population_size"] == 2
    assert len(result.generated.generation_meta["population_objectives"]) == 2
    assert result.trace is not None
    assert [node.node for node in result.trace.nodes].count("annotate") == 2
    assert result.trace.nodes[-1].node == "select"
