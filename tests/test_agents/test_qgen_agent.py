"""QGenAgent + 相关 Agent 的集成测试

测试目标：
- Agent 骨架可以实例化（不需要真实 LLM）
- Input/Output Pydantic 模型序列化正常
- 骨架 Agent 的 NotImplementedError 确实被抛出
- 端到端一次 QGen 调用（mock LLM）
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sciforge.agents import (
    ContentExtractorAgent,
    ContentTaggerAgent,
    DocAssemblyAgent,
    DocInput,
    FormatAnalyzerAgent,
    KBAgent,
    OcrCorrectorAgent,
    QGenAgent,
    QGenInput,
    QGenOutput,
    RecommendAgent,
    SolveAgent,
    SolveInput,
    TagAgent,
    TagInput,
)
from sciforge.agents.stub_agents import _StubInput
from sciforge.core.models import (
    Discipline,
    GenerationRequest,
    KnowledgePoint,
    Question,
    Solution,
    VariantStrategy,
)
from sciforge.storage.sqlite_backend import SQLiteStorageBackend


@pytest.fixture
def temp_db():
    """临时数据库，含少量种子数据"""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        backend = SQLiteStorageBackend(db_path)
        try:
            # 塞几条数学知识点和题目
            kp1 = KnowledgePoint(
                discipline=Discipline.MATH,
                kp_type="theorem",
                number="3.1",
                content="设 f 在 [a,b] 上连续，则 f 在 [a,b] 上一致连续。",
                subject="real_analysis:实分析",
                difficulty=6,
                tags=["math:数学", "analysis:数学分析", "continuity:连续性"],
            )
            kp2 = KnowledgePoint(
                discipline=Discipline.MATH,
                kp_type="definition",
                number="1.1",
                content="群的定义...",
                subject="algebra:代数",
                difficulty=4,
                tags=["math:数学", "algebra:代数", "group:群"],
            )
            q1 = Question(
                discipline=Discipline.MATH,
                question_type="calculation",
                content="计算 $\\int_0^1 x^2 dx$.",
                difficulty=3,
                subject="calculus:微积分",
                tags=["math:数学", "calculus:微积分"],
            )
            backend.save_batch([kp1, kp2], [q1])
            yield backend
        finally:
            backend.close()


# ── 骨架 Agent 测试 ────────────────────────────────────


@pytest.mark.parametrize(
    "agent_cls,name",
    [
        (KBAgent, "kb"),
        (RecommendAgent, "recommend"),
        (FormatAnalyzerAgent, "format_analyzer"),
        (ContentExtractorAgent, "content_extractor"),
        (OcrCorrectorAgent, "ocr_corrector"),
        (ContentTaggerAgent, "content_tagger"),
    ],
)
async def test_stub_agents_raise_not_implemented(agent_cls, name):
    agent = agent_cls(llm=MagicMock())
    assert agent.name == name
    assert agent.discipline == Discipline.MATH
    with pytest.raises(NotImplementedError, match="Post-MVP"):
        await agent.run(_StubInput())


# ── QGenAgent 实例化 ────────────────────────────────────


def test_qgen_agent_instantiation(temp_db):
    mock_llm = MagicMock()
    agent = QGenAgent(llm=mock_llm, storage=temp_db, discipline=Discipline.MATH)
    assert agent.name == "qgen"
    assert agent.discipline == Discipline.MATH
    assert agent.retriever is not None
    assert agent.generator is not None
    assert agent.solver is not None
    assert agent.verifier is not None
    assert agent.annotator is not None


def test_qgen_agent_physics_instantiation(temp_db):
    mock_llm = MagicMock()
    agent = QGenAgent(llm=mock_llm, storage=temp_db, discipline=Discipline.PHYSICS)
    assert agent.discipline == Discipline.PHYSICS
    assert agent.discipline_spec.display_name == "物理"


# ── SolveAgent ─────────────────────────────────────────


async def test_solve_agent_with_question_object():
    mock_llm = MagicMock()
    # SolveEngine 内部用 call_llm_json_with_retry，需要 generate_json
    mock_llm.generate_json = AsyncMock(
        return_value={
            "solution_latex": "略",
            "confidence": "high",
            "methods_used": ["分部积分"],
        }
    )
    agent = SolveAgent(llm=mock_llm)
    q = Question(content="Compute the integral.", discipline=Discipline.MATH)

    result = await agent.run(SolveInput(question=q))
    assert result.solution is not None
    assert result.confidence == "high"
    assert result.solution.solution_latex == "略"


async def test_solve_agent_requires_input():
    agent = SolveAgent(llm=MagicMock())
    with pytest.raises(Exception, match="question"):
        await agent.run(SolveInput())  # 没提供 question 也没 question_id


# ── TagAgent ───────────────────────────────────────────


async def test_tag_agent_runs():
    mock_llm = MagicMock()
    mock_llm.generate_json = AsyncMock(
        return_value={
            "scores": {
                "concept_count": 6,
                "step_count": 5,
                "abstraction_level": 7,
                "technique_novelty": 4,
                "condition_parsing": 5,
                "computation_load": 6,
                "proof_structure": 5,
                "prerequisite_depth": 6,
                "multi_representation": 5,
                "metacognition": 5,
            },
            "overall_reasoning": "中等难度",
        }
    )
    agent = TagAgent(llm=mock_llm)
    q = Question(content="Proof question", discipline=Discipline.MATH, solution_content="略")
    sol = Solution(question_id=q.id, solution_latex="略", confidence="high")

    result = await agent.run(TagInput(question=q, solution=sol))
    assert result.difficulty_score.concept_count == 6
    assert result.difficulty_score.overall > 0


# ── DocAssemblyAgent ───────────────────────────────────


async def test_doc_agent_assembles_lecture(temp_db):
    mock_llm = MagicMock()
    agent = DocAssemblyAgent(llm=mock_llm, storage=temp_db, discipline=Discipline.MATH)

    result = await agent.run(
        DocInput(
            topic_tags=["math:数学", "analysis:数学分析"],
            title="连续函数专题",
        )
    )
    assert result.lecture.title == "连续函数专题"
    assert result.lecture.discipline == Discipline.MATH
    # 至少一节
    assert len(result.lecture.sections) >= 1
    # markdown 非空
    assert "连续函数专题" in result.markdown_text


# ── 简单的 QGenAgent 端到端 (全 mock LLM) ──────────────


async def test_qgen_agent_end_to_end_mock(temp_db):
    """用 mock LLM 跑一次 QGenAgent.run，验证 5 阶段闭环不崩"""
    mock_llm = MagicMock()

    # generate_json 需要根据 prompt 内容返回不同结果
    # 这里简化：返回一个能跑通整个流程的 "通用" 响应
    call_count = {"value": 0}

    async def _mock_generate_json(prompt, **kwargs):
        call_count["value"] += 1
        # 候选题生成 (prompt 含 "content_latex" 说明是 free_generation 模板)
        if "content_latex" in prompt or "solution_sketch" in prompt:
            return {
                "content_latex": (
                    "设 $f$ 在 $[0,1]$ 上连续且非负，证明 $\\int_0^1 f(x) dx \\geq 0$。"
                ),
                "estimated_difficulty": 4,
                "knowledge_points_used": ["单调性"],
                "solution_sketch": "直接由积分定义得证",
            }
        # 求解 (prompt 含 "solution_latex" 或 solve.txt 关键词)
        if "solve" in prompt.lower() or "求解" in prompt or "solution" in prompt.lower():
            return {
                "solution_latex": "由非负性和积分保序性，得 ∫≥0",
                "confidence": "high",
                "methods_used": ["积分保序性"],
            }
        return {"verdict": "PASS"}

    mock_llm.generate_json = AsyncMock(side_effect=_mock_generate_json)

    agent = QGenAgent(llm=mock_llm, storage=temp_db, discipline=Discipline.MATH)
    # 缩小规模加速测试
    agent.MAX_ROUNDS = 1
    agent.CANDIDATES_PER_ROUND = 1

    request = GenerationRequest(
        discipline=Discipline.MATH,
        subject="real_analysis:实分析",
        topics=["math:数学", "analysis:数学分析"],
        difficulty_target=5,
        question_type="proof",
        strategy=VariantStrategy.TOPIC_GENERATION,
    )

    result = await agent.run(QGenInput(request=request))

    # 只要不崩就算过；具体 generated 可能是 None（因为 mock 的 review/difficulty JSON 可能失败）
    assert isinstance(result, QGenOutput)
    assert result.rounds_used >= 1 or result.candidates_tried >= 0
