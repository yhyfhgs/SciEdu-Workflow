"""极简 StateGraph 单元测试"""

from __future__ import annotations

import pytest

from sciforge.agents import END, StateGraph


async def test_linear_graph():
    """三节点线性图：a → b → c → END"""
    visited: list[str] = []

    async def node_a(state):
        visited.append("a")
        return {"value": state.get("value", 0) + 1}

    async def node_b(state):
        visited.append("b")
        return {"value": state["value"] * 2}

    async def node_c(state):
        visited.append("c")
        return {"value": state["value"] + 10}

    graph = StateGraph()
    graph.add_node("a", node_a)
    graph.add_node("b", node_b)
    graph.add_node("c", node_c)
    graph.set_entry_point("a")
    graph.add_edge("a", "b")
    graph.add_edge("b", "c")
    graph.add_edge("c", END)

    compiled = graph.compile()
    result = await compiled.ainvoke({"value": 5})

    assert visited == ["a", "b", "c"]
    assert result["value"] == (5 + 1) * 2 + 10


async def test_conditional_graph():
    """条件路由：verify 返回 pass 或 retry"""
    visited: list[str] = []

    async def retrieve(state):
        visited.append("retrieve")
        return {"context": "some_context"}

    async def generate(state):
        visited.append("generate")
        state.setdefault("attempts", 0)
        return {"attempts": state["attempts"] + 1}

    async def verify(state):
        visited.append("verify")
        passed = state["attempts"] >= 2
        return {"passed": passed}

    def route(state):
        return "pass" if state["passed"] else "retry"

    graph = StateGraph()
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.add_node("verify", verify)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", "verify")
    graph.add_conditional_edges(
        "verify",
        route,
        {"pass": END, "retry": "generate"},
    )

    compiled = graph.compile()
    result = await compiled.ainvoke({})

    assert result["attempts"] == 2
    assert result["passed"] is True
    # 第一次 retrieve, generate, verify → retry；第二次 generate, verify → pass
    assert visited == ["retrieve", "generate", "verify", "generate", "verify"]


async def test_max_steps_prevents_infinite_loop():
    """max_steps 保险丝应在循环图失控时抛错"""

    async def forever(state):
        return {}

    graph = StateGraph()
    graph.add_node("forever", forever)
    graph.set_entry_point("forever")
    graph.add_edge("forever", "forever")  # 自环

    compiled = graph.compile()
    with pytest.raises(RuntimeError, match="max_steps"):
        await compiled.ainvoke({}, max_steps=5)


async def test_node_update_none():
    """节点返回 None 表示不更新 state"""

    async def noop(state):
        return None

    graph = StateGraph()
    graph.add_node("noop", noop)
    graph.set_entry_point("noop")
    graph.add_edge("noop", END)

    compiled = graph.compile()
    result = await compiled.ainvoke({"x": 1})
    assert result == {"x": 1}


def test_entry_point_required():
    graph = StateGraph()

    async def _n(state):
        return None

    graph.add_node("n", _n)
    with pytest.raises(ValueError, match="entry_point"):
        graph.compile()


def test_duplicate_node_name_rejected():
    graph = StateGraph()

    async def _n(state):
        return None

    graph.add_node("n", _n)
    with pytest.raises(ValueError, match="已存在"):
        graph.add_node("n", _n)


def test_end_not_allowed_as_node_name():
    graph = StateGraph()

    async def _n(state):
        return None

    with pytest.raises(ValueError):
        graph.add_node(END, _n)


async def test_unknown_node_raises():
    """跳转到未注册的节点应抛 ValueError"""

    async def node_a(state):
        return None

    graph = StateGraph()
    graph.add_node("a", node_a)
    graph.set_entry_point("a")
    graph.add_edge("a", "ghost")  # 'ghost' 未注册

    compiled = graph.compile()
    with pytest.raises(ValueError, match="ghost"):
        await compiled.ainvoke({})


async def test_fallthrough_to_end_without_edge():
    """没定义出边的节点默认跳转到 END"""

    async def node_a(state):
        return {"reached": True}

    graph = StateGraph()
    graph.add_node("a", node_a)
    graph.set_entry_point("a")
    # 故意不加 add_edge("a", END)

    compiled = graph.compile()
    result = await compiled.ainvoke({})
    assert result == {"reached": True}


def test_lecture_model_import():
    """Lecture/LectureSection 可以从 core.models 导入"""
    from sciforge.core.models import Discipline, Lecture, LectureSection

    section = LectureSection(title="群论基础", intro="本节介绍群")
    lecture = Lecture(
        title="代数结构导论",
        discipline=Discipline.MATH,
        topic_tags=["math:数学", "algebra:代数"],
        sections=[section],
    )
    assert lecture.discipline == Discipline.MATH
    assert lecture.sections[0].title == "群论基础"
    assert lecture.estimated_duration_min == 60
