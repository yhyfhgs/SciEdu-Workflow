"""SciForge 多 Agent 协作系统

基础设施 (Task 8 起):
- BaseAgent: 所有 Agent 的抽象基类，统一 async run / prompt 加载 / LLM 调用
- AgentState: LangGraph 风格的状态字典（TypedDict）
- StateGraph: 极简手写状态图（API 仿 LangGraph 子集，便于后续无痛迁移）

十大 Agent（按 Docs/09_Agent编排系统.md）：
- QGen / Solve / Tag / DocAssembly-lite → Task 9 完整实现
- KB / Recommend / FormatAnalyzer / ContentExtractor / OcrCorrector /
  ContentTagger → Task 9 骨架（raise NotImplementedError）

设计原则：
- 不引入 langgraph 依赖（避免 MVP 阶段的重依赖）
- Agent 可单独运行也可通过 StateGraph 编排
- 每个 Agent 都带 discipline 参数，借用 DisciplineRegistry 参数化 prompt
"""

from sciforge.agents.base import AgentError, BaseAgent
from sciforge.agents.doc_agent import DocAssemblyAgent, DocInput, DocOutput
from sciforge.agents.graph import END, CompiledGraph, StateGraph
from sciforge.agents.qgen import (
    DurableQGenHarness,
    QGenAgent,
    QGenGraphSnapshot,
    QGenInput,
    QGenOutput,
)
from sciforge.agents.solve_agent import SolveAgent, SolveInput, SolveOutput
from sciforge.agents.state import AgentState
from sciforge.agents.stub_agents import (
    ContentExtractorAgent,
    ContentTaggerAgent,
    FormatAnalyzerAgent,
    KBAgent,
    OcrCorrectorAgent,
    RecommendAgent,
)
from sciforge.agents.tag_agent import TagAgent, TagInput, TagOutput

__all__ = [
    # base
    "AgentError",
    "AgentState",
    "BaseAgent",
    "CompiledGraph",
    "END",
    "StateGraph",
    # Agent 2: QGen (complete)
    "QGenAgent",
    "DurableQGenHarness",
    "QGenGraphSnapshot",
    "QGenInput",
    "QGenOutput",
    # Agent 3: Solve (complete)
    "SolveAgent",
    "SolveInput",
    "SolveOutput",
    # Agent 4: Tag (complete)
    "TagAgent",
    "TagInput",
    "TagOutput",
    # Agent 5: DocAssembly (lite)
    "DocAssemblyAgent",
    "DocInput",
    "DocOutput",
    # Agent 1, 6, 7, 8, 9, 10: stubs
    "KBAgent",
    "RecommendAgent",
    "FormatAnalyzerAgent",
    "ContentExtractorAgent",
    "OcrCorrectorAgent",
    "ContentTaggerAgent",
]
