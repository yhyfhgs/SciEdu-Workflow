"""AgentState — LangGraph 风格的 Agent 间共享状态

所有字段都是 TypedDict total=False（可选），Agent 只读自己关心的键、
只写自己产出的键，避免全状态对象的紧耦合。

下一步升级时，只要字段 schema 保持向后兼容，就能直接迁移到 LangGraph 的
原生 StateGraph 而不需要改 Agent 代码。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from sciforge.core.models import (
        ContextBundle,
        DifficultyScore,
        GeneratedQuestion,
        GenerationRequest,
        Lecture,
        Question,
        Solution,
        VerificationResult,
    )


class AgentState(TypedDict, total=False):
    """多 Agent 编排的共享状态字典"""

    # ── 路由 ─────────────────────────────────────────────
    task_type: str  # "generate" | "solve" | "doc" | "pipeline"
    discipline: str  # Discipline 枚举的 value（"math" / "physics" / ...）

    # ── 生成流 (QGen Agent) ──────────────────────────────
    request: GenerationRequest | None
    context: ContextBundle | None
    candidates: list[Question]
    solution: Solution | None
    verification: VerificationResult | None
    difficulty_score: DifficultyScore | None

    # ── 文档流 (DocAssembly Agent) ───────────────────────
    lecture: Lecture | None

    # ── 通用输出 ─────────────────────────────────────────
    output: Any
    error: str | None

    # ── 迭代控制 ─────────────────────────────────────────
    round_num: int
    max_rounds: int
