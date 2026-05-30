"""极简手写 StateGraph — LangGraph 子集 API

目的：
- 提供 LangGraph 核心功能（节点、边、条件路由、编译、ainvoke）的最小实现
- API 刻意模仿 LangGraph，Post-MVP 可以无痛迁移到 `langgraph.graph.StateGraph`
- 不引入 langgraph 依赖（langgraph pulls pydantic v1 兼容层 + many sub-deps）

功能边界：
- 支持：async node function、线性边、条件边、END 终止
- 不支持：持久化、human-in-the-loop、fanout/fanin、重试 hooks
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

END = "__END__"
"""终止节点哨兵"""


class StateGraph:
    """状态图构建器（非编译状态）"""

    def __init__(self) -> None:
        self._nodes: dict[str, NodeFn] = {}
        self._edges: dict[str, str] = {}
        self._cond_edges: dict[str, tuple[Router, dict[str, str]]] = {}
        self._entry: str = ""

    def add_node(self, name: str, fn: NodeFn) -> None:
        """注册一个节点"""
        if name == END:
            raise ValueError(f"节点名不能是 {END!r}")
        if name in self._nodes:
            raise ValueError(f"节点 {name!r} 已存在")
        self._nodes[name] = fn

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
    ) -> dict[str, Any]:
        """从入口节点开始异步执行状态图，直到到达 END 或 max_steps 超限

        Args:
            state: 初始状态字典（节点会 in-place 修改）
            max_steps: 防止循环图无限运行的保险丝
        Returns:
            最终状态字典
        """
        current = self._g._entry
        steps = 0

        while current != END:
            if steps >= max_steps:
                raise RuntimeError(f"StateGraph 执行超过 max_steps={max_steps}（死循环？）")
            steps += 1

            fn = self._g._nodes.get(current)
            if fn is None:
                raise ValueError(f"未知节点 {current!r}")

            logger.debug("graph_node_enter", node=current, step=steps)
            updates = await fn(state)
            if updates:
                state.update(updates)

            # 决定下一步
            if current in self._g._cond_edges:
                router, mapping = self._g._cond_edges[current]
                key = router(state)
                next_node = mapping.get(key, END)
            else:
                next_node = self._g._edges.get(current, END)

            logger.debug("graph_node_exit", node=current, step=steps, next=next_node)
            current = next_node

        return state
