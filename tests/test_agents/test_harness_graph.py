"""HarnessGraph 升级测试 —— trace 钩子 / 节点级 retry / checkpoint 钩子

与 test_graph.py（基础 StateGraph 测试）互补：本文件只覆盖新增的三项生产能力，
并锁定"不传任何新参数时行为完全不变"的向后兼容契约。
"""

from __future__ import annotations

import asyncio

import pytest

from sciforge.agents import END, StateGraph
from sciforge.agents.graph import CompiledGraph


def _linear_graph() -> CompiledGraph:
    """a → b → END 的最小线性图，两节点都对 value 做确定性更新"""

    async def node_a(state: dict) -> dict:
        return {"value": state.get("value", 0) + 1}

    async def node_b(state: dict) -> dict:
        return {"value": state["value"] * 10}

    graph = StateGraph()
    graph.add_node("a", node_a)
    graph.add_node("b", node_b)
    graph.set_entry_point("a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    return graph.compile()


# --------------------------------------------------------------------------- #
# 1. trace 钩子：on_node_enter / on_node_exit
# --------------------------------------------------------------------------- #


async def test_node_enter_exit_hooks_called_in_order():
    """on_node_enter 在节点前、on_node_exit 在节点后，按执行顺序各调用一次"""
    events: list[tuple[str, str]] = []

    def on_enter(node: str, state: dict) -> None:
        events.append(("enter", node))

    def on_exit(node: str, state: dict, updates: dict | None) -> None:
        events.append(("exit", node))

    compiled = _linear_graph()
    await compiled.ainvoke({}, on_node_enter=on_enter, on_node_exit=on_exit)

    # 每个节点严格 enter 后立刻 exit，再进入下一个节点
    assert events == [
        ("enter", "a"),
        ("exit", "a"),
        ("enter", "b"),
        ("exit", "b"),
    ]


async def test_node_exit_receives_updates_and_merged_state():
    """on_node_exit 收到的 updates 是节点返回值，且此时 state 已 merge 该更新"""
    seen: list[tuple[str, dict | None, int]] = []

    def on_exit(node: str, state: dict, updates: dict | None) -> None:
        # 记录 (节点, 该节点返回的 updates, 当前 state 里的 value)
        seen.append((node, updates, state.get("value")))

    compiled = _linear_graph()
    final = await compiled.ainvoke({"value": 0}, on_node_exit=on_exit)

    # a: 返回 {value:1}，merge 后 state.value==1
    # b: 返回 {value:10}，merge 后 state.value==10
    assert seen == [
        ("a", {"value": 1}, 1),
        ("b", {"value": 10}, 10),
    ]
    assert final["value"] == 10


async def test_node_enter_sees_pre_update_state():
    """on_node_enter 在节点执行前调用，看到的是该节点尚未 merge 时的 state"""
    enter_values: list[int] = []

    def on_enter(node: str, state: dict) -> None:
        enter_values.append(state.get("value", -1))

    compiled = _linear_graph()
    await compiled.ainvoke({"value": 0}, on_node_enter=on_enter)

    # 进入 a 时 value 还是 0；进入 b 时 a 已把 value 改成 1
    assert enter_values == [0, 1]


async def test_node_exit_updates_none_when_node_returns_none():
    """节点返回 None 时，on_node_exit 的 updates 参数为 None"""
    captured: list[dict | None] = []

    async def noop(state: dict) -> None:
        return None

    def on_exit(node: str, state: dict, updates: dict | None) -> None:
        captured.append(updates)

    graph = StateGraph()
    graph.add_node("noop", noop)
    graph.set_entry_point("noop")
    graph.add_edge("noop", END)

    compiled = graph.compile()
    await compiled.ainvoke({"x": 1}, on_node_exit=on_exit)

    assert captured == [None]


# --------------------------------------------------------------------------- #
# 2. 节点级 retry
# --------------------------------------------------------------------------- #


async def test_retry_eventually_succeeds(monkeypatch):
    """节点前 N 次抛异常、第 N+1 次成功 → retries 足够时整体成功"""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("sciforge.agents.graph.asyncio.sleep", fake_sleep)

    calls = {"n": 0}

    async def flaky(state: dict) -> dict:
        calls["n"] += 1
        if calls["n"] < 3:  # 前两次失败，第三次成功
            raise RuntimeError("transient")
        return {"ok": True, "tries": calls["n"]}

    graph = StateGraph()
    graph.add_node("flaky", flaky, retries=5)
    graph.set_entry_point("flaky")
    graph.add_edge("flaky", END)

    compiled = graph.compile()
    result = await compiled.ainvoke({})

    assert result == {"ok": True, "tries": 3}
    assert calls["n"] == 3  # 1 次初始 + 2 次重试
    assert len(sleeps) == 2  # 每次重试前退避一次
    # 指数退避：第 1 次退避 < 第 2 次退避
    assert sleeps[0] < sleeps[1]


async def test_retry_exhausted_reraises(monkeypatch):
    """重试耗尽后把节点的异常原样向上抛"""

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("sciforge.agents.graph.asyncio.sleep", fake_sleep)

    calls = {"n": 0}

    async def always_fail(state: dict) -> dict:
        calls["n"] += 1
        raise ValueError("boom")

    graph = StateGraph()
    graph.add_node("bad", always_fail, retries=2)
    graph.set_entry_point("bad")
    graph.add_edge("bad", END)

    compiled = graph.compile()
    with pytest.raises(ValueError, match="boom"):
        await compiled.ainvoke({})

    # retries=2 → 共执行 1 + 2 = 3 次
    assert calls["n"] == 3


async def test_no_retry_by_default_propagates_immediately():
    """默认 retries=0：节点首个异常立即向上抛，不重试"""
    calls = {"n": 0}

    async def boom(state: dict) -> dict:
        calls["n"] += 1
        raise RuntimeError("first-and-only")

    graph = StateGraph()
    graph.add_node("boom", boom)  # 不传 retries
    graph.set_entry_point("boom")
    graph.add_edge("boom", END)

    compiled = graph.compile()
    with pytest.raises(RuntimeError, match="first-and-only"):
        await compiled.ainvoke({})

    assert calls["n"] == 1


async def test_negative_retries_rejected():
    """add_node 拒绝负的 retries"""
    graph = StateGraph()

    async def _n(state: dict) -> None:
        return None

    with pytest.raises(ValueError, match="retries"):
        graph.add_node("n", _n, retries=-1)


async def test_retry_then_exit_hook_called_once(monkeypatch):
    """节点经重试最终成功时，on_node_exit 只在成功后调用一次（不为每次失败调用）"""

    async def fake_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr("sciforge.agents.graph.asyncio.sleep", fake_sleep)

    calls = {"n": 0}

    async def flaky(state: dict) -> dict:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return {"done": True}

    exits: list[str] = []

    def on_exit(node: str, state: dict, updates: dict | None) -> None:
        exits.append(node)

    graph = StateGraph()
    graph.add_node("flaky", flaky, retries=3)
    graph.set_entry_point("flaky")
    graph.add_edge("flaky", END)

    compiled = graph.compile()
    await compiled.ainvoke({}, on_node_exit=on_exit)

    assert exits == ["flaky"]  # 仅一次


# --------------------------------------------------------------------------- #
# 3. checkpoint 钩子
# --------------------------------------------------------------------------- #


async def test_checkpoint_called_per_step():
    """on_checkpoint 每步状态更新后调用一次，step 从 1 递增，state 为最新值"""
    checkpoints: list[tuple[int, int]] = []

    def on_ckpt(state: dict, step: int) -> None:
        checkpoints.append((step, state["value"]))

    compiled = _linear_graph()
    await compiled.ainvoke({"value": 0}, on_checkpoint=on_ckpt)

    # step1 (节点 a 后): value=1；step2 (节点 b 后): value=10
    assert checkpoints == [(1, 1), (2, 10)]


async def test_checkpoint_step_count_in_loop():
    """带条件回环的图：on_checkpoint 的 step 反映真实执行步数（含重复节点）"""
    steps_seen: list[int] = []

    async def gen(state: dict) -> dict:
        return {"attempts": state.get("attempts", 0) + 1}

    async def verify(state: dict) -> dict:
        return {"passed": state["attempts"] >= 2}

    def route(state: dict) -> str:
        return "pass" if state["passed"] else "retry"

    graph = StateGraph()
    graph.add_node("gen", gen)
    graph.add_node("verify", verify)
    graph.set_entry_point("gen")
    graph.add_edge("gen", "verify")
    graph.add_conditional_edges("verify", route, {"pass": END, "retry": "gen"})

    def on_ckpt(state: dict, step: int) -> None:
        steps_seen.append(step)

    compiled = graph.compile()
    result = await compiled.ainvoke({}, on_checkpoint=on_ckpt)

    # gen, verify(retry), gen, verify(pass) = 4 步
    assert result["attempts"] == 2
    assert steps_seen == [1, 2, 3, 4]


async def test_all_hooks_together():
    """三类钩子同时提供时互不干扰，各自被正确触发"""
    log: list[str] = []

    def on_enter(node: str, state: dict) -> None:
        log.append(f"enter:{node}")

    def on_exit(node: str, state: dict, updates: dict | None) -> None:
        log.append(f"exit:{node}")

    def on_ckpt(state: dict, step: int) -> None:
        log.append(f"ckpt:{step}")

    compiled = _linear_graph()
    await compiled.ainvoke(
        {},
        on_node_enter=on_enter,
        on_node_exit=on_exit,
        on_checkpoint=on_ckpt,
    )

    assert log == [
        "enter:a",
        "exit:a",
        "ckpt:1",
        "enter:b",
        "exit:b",
        "ckpt:2",
    ]


# --------------------------------------------------------------------------- #
# 4. 向后兼容：不传任何新参数行为不变
# --------------------------------------------------------------------------- #


async def test_legacy_ainvoke_unchanged():
    """旧用法（仅传 state，可选 max_steps）行为与升级前一致"""
    compiled = _linear_graph()

    result = await compiled.ainvoke({"value": 5})
    assert result["value"] == (5 + 1) * 10

    # max_steps 仍是位置参数，死循环保险丝不变
    async def forever(state: dict) -> dict:
        return {}

    g = StateGraph()
    g.add_node("forever", forever)
    g.set_entry_point("forever")
    g.add_edge("forever", "forever")
    looped = g.compile()
    with pytest.raises(RuntimeError, match="max_steps"):
        await looped.ainvoke({}, 5)


async def test_legacy_add_node_signature_unchanged():
    """add_node 不传 retries 仍可用，且默认不重试（retries 默认 0）"""
    graph = StateGraph()

    async def _n(state: dict) -> dict:
        return {"hit": True}

    graph.add_node("n", _n)  # 二参调用，向后兼容
    graph.set_entry_point("n")
    graph.add_edge("n", END)

    compiled = graph.compile()
    result = await compiled.ainvoke({})
    assert result == {"hit": True}


def test_backoff_base_is_small():
    """退避基数足够小，保证生产瞬时重试快、单测不拖慢"""
    from sciforge.agents.graph import _RETRY_BACKOFF_BASE_SECONDS

    assert 0 < _RETRY_BACKOFF_BASE_SECONDS <= 0.01


def test_real_asyncio_sleep_used_for_backoff():
    """退避确实走 asyncio.sleep（用真实 sleep，验证不会卡死且时长可忽略）

    不打桩，直接跑一次真实退避；基数极小（毫秒级）所以测试依旧快。
    """

    calls = {"n": 0}

    async def flaky(state: dict) -> dict:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient")
        return {"ok": True}

    graph = StateGraph()
    graph.add_node("flaky", flaky, retries=1)
    graph.set_entry_point("flaky")
    graph.add_edge("flaky", END)
    compiled = graph.compile()

    result = asyncio.run(compiled.ainvoke({}))
    assert result == {"ok": True}
    assert calls["n"] == 2
