"""极简手写 StateGraph — LangGraph 子集 API

目的：
- 提供 LangGraph 核心功能（节点、边、条件路由、编译、ainvoke）的最小实现
- API 刻意模仿 LangGraph，Post-MVP 可以无痛迁移到 `langgraph.graph.StateGraph`
- 不引入 langgraph 依赖（langgraph pulls pydantic v1 兼容层 + many sub-deps）

功能边界：
- 支持：async node function、线性边、条件边、END 终止
- 支持（HarnessGraph 升级，全部 opt-in，默认行为不变）：
    * trace 钩子 on_node_enter / on_node_exit —— 每个节点执行前/后回调
    * 节点级 retry —— add_node(..., retries=N) 在节点抛异常时最多重试 N 次（指数退避）
    * checkpoint 钩子 on_checkpoint —— 每步状态更新后回调，便于持久化/恢复
- 不支持：human-in-the-loop、fanout/fanin
  （这些都是 Post-MVP 需要时迁移到真 LangGraph 时补的）

示例：
    graph = StateGraph()
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)
    graph.add_node("verify", verify_node)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "verify")
    graph.add_conditional_edges(
        "verify",
        lambda state: "pass" if state["verification"].passed else "retry",
        {"pass": END, "retry": "generate"},
    )

    compiled = graph.compile()
    final_state = await compiled.ainvoke({"request": req})
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

logger = structlog.get_logger()

NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]
"""Node 函数签名: async (state) -> updates_dict | None

返回 dict 会 merge 进 state；返回 None 表示无更新。
"""

Router = Callable[[dict[str, Any]], str]
"""条件路由函数: (state) -> next_node_name"""

NodeEnterHook = Callable[[str, dict[str, Any]], None]
"""trace 钩子：节点执行前回调 (node_name, state) -> None"""

NodeExitHook = Callable[[str, dict[str, Any], dict[str, Any] | None], None]
"""trace 钩子：节点执行后回调 (node_name, state, updates) -> None

`updates` 为该节点返回的更新 dict（已 merge 进 state），None 表示无更新。
"""

CheckpointHook = Callable[[dict[str, Any], int], None]
"""checkpoint 钩子：每步状态更新后回调 (state, step) -> None

`step` 为 1-based 的已执行步数，便于持久化进度并在恢复时定位。
"""

TransitionHook = Callable[[str, str, dict[str, Any], int], None]
"""可恢复转移钩子：(node, next_node, latest_state, completed_steps) -> None。

与旧 ``on_checkpoint(state, step)`` 相比，它显式暴露下一节点，因此持久化
harness 能在节点边界恢复，不会重放已完成的 LLM 调用。
"""

END = "__END__"
"""终止节点哨兵"""

_RETRY_BACKOFF_BASE_SECONDS = 0.001
"""节点 retry 的退避基数（秒）。

刻意取很小的值：生产场景下节点失败通常是瞬时的（LLM 限流/网络抖动），
小退避足够；同时保证单元测试快速。退避时长 = base * 2 ** (attempt - 1)。
"""


class StateGraph:
    """状态图构建器（非编译状态）"""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeFn] = {}
        self._retries: dict[str, int] = {}
        self._edges: dict[str, str] = {}
        self._cond_edges: dict[str, tuple[Router, dict[str, str]]] = {}
        self._entry: str = ""

    def add_node(self, name: str, fn: NodeFn, retries: int = 0) -> None:
        """注册一个节点

        Args:
            name: 节点名（不能是 END，不能重复）
            fn: 节点协程函数 async (state) -> updates | None
            retries: 节点抛异常时的最大重试次数（默认 0 = 不重试）。
                每次重试前用指数退避 `await asyncio.sleep`；重试耗尽则原样向上抛。
        """
        if name == END:
            raise ValueError(f"节点名不能是 {END!r}")
        if name in self._nodes:
            raise ValueError(f"节点 {name!r} 已存在")
        if retries < 0:
            raise ValueError(f"retries 不能为负: {retries}")
        self._nodes[name] = fn
        self._retries[name] = retries

    def add_edge(self, src: str, dst: str) -> None:
        """添加线性边 src → dst"""
        self._edges[src] = dst

    def add_conditional_edges(
        self,
        src: str,
        router: Router,
        mapping: dict[str, str],
    ) -> None:
        """添加条件边：router(state) 返回 key，按 mapping 跳转"""
        self._cond_edges[src] = (router, mapping)

    def set_entry_point(self, name: str) -> None:
        """设置入口节点"""
        self._entry = name

    def compile(self) -> CompiledGraph:
        """构建并返回可执行的编译图"""
        if not self._entry:
            raise ValueError("必须调用 set_entry_point() 设置入口节点")
        if self._entry not in self._nodes:
            raise ValueError(f"入口节点 {self._entry!r} 未注册")
        return CompiledGraph(self)


class CompiledGraph:
    """编译后的图，可通过 ainvoke 异步执行"""

    def __init__(self, graph: StateGraph) -> None:
        self._g = graph

    async def ainvoke(
        self,
        state: dict[str, Any],
        max_steps: int = 100,
        *,
        on_node_enter: NodeEnterHook | None = None,
        on_node_exit: NodeExitHook | None = None,
        on_checkpoint: CheckpointHook | None = None,
        on_transition: TransitionHook | None = None,
        start_at: str | None = None,
        completed_steps: int = 0,
    ) -> dict[str, Any]:
        """从入口节点开始异步执行状态图，直到到达 END 或 max_steps 超限

        Args:
            state: 初始状态字典（节点会 in-place 修改）
            max_steps: 防止循环图无限运行的保险丝
            on_node_enter: 可选 trace 钩子，每个节点执行前调用 (node, state)
            on_node_exit: 可选 trace 钩子，每个节点执行后调用 (node, state, updates)；
                节点 retry 成功时仅在最终成功后调用一次。
            on_checkpoint: 可选 checkpoint 钩子，每步状态更新后调用 (state, step)
            on_transition: 可选持久化钩子，路由确定后调用
                (node, next_node, state, completed_steps)。
            start_at: 从某个已注册节点恢复；None 使用 entry point。
            completed_steps: 恢复前已完成步数；与 max_steps 共用总预算。
        Returns:
            最终状态字典
        """
        if completed_steps < 0:
            raise ValueError("completed_steps 不能为负")
        current = start_at or self._g._entry
        if current != END and current not in self._g._nodes:
            raise ValueError(f"未知恢复节点 {current!r}")
        steps = completed_steps

        while current != END:
            if steps >= max_steps:
                raise RuntimeError(f"StateGraph 执行超过 max_steps={max_steps}（死循环？）")
            steps += 1

            fn = self._g._nodes.get(current)
            if fn is None:
                raise ValueError(f"未知节点 {current!r}")

            if on_node_enter is not None:
                on_node_enter(current, state)
            logger.debug("graph_node_enter", node=current, step=steps)

            updates = await self._run_node(current, fn, state)
            if updates:
                state.update(updates)

            if on_node_exit is not None:
                on_node_exit(current, state, updates)
            if on_checkpoint is not None:
                on_checkpoint(state, steps)

            # 决定下一步
            if current in self._g._cond_edges:
                router, mapping = self._g._cond_edges[current]
                key = router(state)
                next_node = mapping.get(key, END)
            else:
                next_node = self._g._edges.get(current, END)

            if on_transition is not None:
                on_transition(current, next_node, state, steps)

            logger.debug("graph_node_exit", node=current, step=steps, next=next_node)
            current = next_node

        return state

    async def _run_node(
        self,
        name: str,
        fn: NodeFn,
        state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """执行单个节点，按 add_node(retries=N) 做带退避的重试

        节点抛异常时最多重试 N 次（共执行 N+1 次）；重试耗尽则把最后一次
        异常原样向上抛。退避时长 = `_RETRY_BACKOFF_BASE_SECONDS * 2 ** attempt`。
        """
        retries = self._g._retries.get(name, 0)
        attempt = 0
        while True:
            try:
                return await fn(state)
            except Exception as exc:
                if attempt >= retries:
                    raise
                logger.warning(
                    "graph_node_retry",
                    node=name,
                    attempt=attempt + 1,
                    max_retries=retries,
                    error=str(exc),
                )
                await asyncio.sleep(_RETRY_BACKOFF_BASE_SECONDS * 2**attempt)
                attempt += 1
