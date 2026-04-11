"""QGenAgent — 题目生成的 5 阶段闭环

结构:
- agent.py: QGenAgent + QGenInput/QGenOutput，组合 5 个子组件
- retriever.py: ContextRetriever，基于 SQLiteStorageBackend 检索
- generator.py: QuestionGenerator，LLM 生成候选
- solver.py: SolveEngine，LLM 求解（SolveAgent 也复用它）
- verifier.py: VerificationPipeline，独立审核 LLM + 检查清单
- annotator.py: DifficultyAnnotator (TagAgent 也复用它)

基于 Docs/Ref/generation_engine.py (511 行参考实现) 重建，字段访问已完全
适配新 models (content 替代 content_latex，discipline + subject 替代
Subject enum，SQLiteStorageBackend 替代内存 list)。
"""

from sciforge.agents.qgen.agent import QGenAgent, QGenInput, QGenOutput

__all__ = ["QGenAgent", "QGenInput", "QGenOutput"]
