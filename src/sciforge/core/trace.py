"""GenerationTrace 可观测模型 (Pydantic v2)

为生成引擎（Agent 系统）提供节点级可观测追踪：记录每个 graph 节点的耗时、
LLM 模型、token 估算、gate 判定，以及整次生成请求的轮次与最终结果。

与 ``GenerationRequest`` 故意保持解耦：``request_summary`` 用 ``dict`` 摘要而非
直接引用请求模型，避免循环依赖。token 估算口径与
``pipeline/retry.py`` 的 ``llm_call_metrics`` 一致（``len(text) // 4``）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数：字符数整除 4。

    与 ``pipeline/retry.py`` 的 ``llm_call_metrics`` 口径一致，仅用于
    可观测指标，非计费精确值。
    """
    return len(text) // 4


class NodeTrace(BaseModel):
    """单个 graph 节点的执行追踪。"""

    model_config = ConfigDict(strict=True)

    node: str
    duration_ms: int = 0
    llm_model: str = ""
    prompt_tokens_est: int = 0
    output_tokens_est: int = 0
    gate: str = ""
    passed: bool | None = None
    note: str = ""


class GenerationTrace(BaseModel):
    """一次生成请求的完整追踪：节点序列 + 轮次 + 结果摘要。"""

    model_config = ConfigDict(strict=True)

    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tenant_id: str = ""
    user_id: str = ""
    request_summary: dict[str, object] = Field(default_factory=dict)
    nodes: list[NodeTrace] = Field(default_factory=list)
    rounds_used: int = 0
    improve_rounds_used: int = 0
    total_tokens_est: int = 0
    outcome: Literal["success", "fail"] = "fail"
    final_question_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)

    def add_node(self, nt: NodeTrace) -> None:
        """追加一个节点追踪，并累加其 token 估算到总量。"""
        self.nodes.append(nt)
        self.total_tokens_est += nt.prompt_tokens_est + nt.output_tokens_est

    def recompute_totals(self) -> None:
        """根据当前节点列表重新计算 ``total_tokens_est``。"""
        self.total_tokens_est = sum(
            nt.prompt_tokens_est + nt.output_tokens_est for nt in self.nodes
        )
