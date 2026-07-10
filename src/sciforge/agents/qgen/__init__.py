"""QGenAgent — 题目专用状态图 Harness

结构:
- agent.py: retrieve/generate/prefilter/solve/verify/annotate/diagnose/evolve/select 状态图
- retriever.py / generator.py / solver.py / deep_solver.py: 质量检索、候选与独立求解
- verifier.py / verification_sandbox.py: 独立审核与数值/Lean 四态证据
- evolver.py / population.py: 有界失败修正、lineage 与确定性候选选择
- durable_harness.py: run step/checkpoint/event/budget/cancel/resume 适配
- annotator.py: DifficultyAnnotator（TagAgent 也复用它）

基于 Docs/Ref/generation_engine.py (511 行参考实现) 重建，字段访问已完全
适配新 models (content 替代 content_latex，discipline + subject 替代
Subject enum，SQLiteStorageBackend 替代内存 list)。
"""

from sciforge.agents.qgen.agent import QGenAgent, QGenInput, QGenOutput
from sciforge.agents.qgen.durable_harness import DurableQGenHarness, QGenGraphSnapshot

__all__ = [
    "DurableQGenHarness",
    "QGenAgent",
    "QGenGraphSnapshot",
    "QGenInput",
    "QGenOutput",
]
