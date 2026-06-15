"""GenerationTrace 可观测模型测试。"""

from __future__ import annotations

from sciforge.core.trace import GenerationTrace, NodeTrace, estimate_tokens


def test_node_trace_defaults() -> None:
    nt = NodeTrace(node="retriever")
    assert nt.node == "retriever"
    assert nt.duration_ms == 0
    assert nt.llm_model == ""
    assert nt.prompt_tokens_est == 0
    assert nt.output_tokens_est == 0
    assert nt.gate == ""
    assert nt.passed is None
    assert nt.note == ""


def test_generation_trace_defaults() -> None:
    tr = GenerationTrace()
    # trace_id 为 uuid4 字符串，每次实例不同
    assert isinstance(tr.trace_id, str)
    assert len(tr.trace_id) == 36
    assert GenerationTrace().trace_id != tr.trace_id
    assert tr.tenant_id == ""
    assert tr.user_id == ""
    assert tr.request_summary == {}
    assert tr.nodes == []
    assert tr.rounds_used == 0
    assert tr.improve_rounds_used == 0
    assert tr.total_tokens_est == 0
    assert tr.outcome == "fail"
    assert tr.final_question_id == ""


def test_request_summary_independent_per_instance() -> None:
    # default_factory 确保两个实例不共享同一个 dict/list
    a = GenerationTrace()
    b = GenerationTrace()
    a.request_summary["difficulty"] = 6
    a.nodes.append(NodeTrace(node="x"))
    assert b.request_summary == {}
    assert b.nodes == []


def test_add_node_accumulates_tokens() -> None:
    tr = GenerationTrace()
    tr.add_node(NodeTrace(node="generator", prompt_tokens_est=100, output_tokens_est=40))
    assert tr.total_tokens_est == 140
    assert len(tr.nodes) == 1

    tr.add_node(NodeTrace(node="verifier", prompt_tokens_est=10, output_tokens_est=5))
    assert tr.total_tokens_est == 155
    assert len(tr.nodes) == 2


def test_recompute_totals() -> None:
    tr = GenerationTrace()
    # 直接塞入 nodes 绕过 add_node，total 仍为 0
    tr.nodes.append(NodeTrace(node="a", prompt_tokens_est=7, output_tokens_est=3))
    tr.nodes.append(NodeTrace(node="b", prompt_tokens_est=20, output_tokens_est=0))
    assert tr.total_tokens_est == 0

    tr.recompute_totals()
    assert tr.total_tokens_est == 30


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 0  # 3 // 4
    assert estimate_tokens("abcd") == 1  # 4 // 4
    assert estimate_tokens("a" * 400) == 100


def test_model_dump_json_roundtrip() -> None:
    tr = GenerationTrace(
        tenant_id="t1",
        user_id="u1",
        request_summary={"difficulty": 6, "type": "proof"},
        rounds_used=2,
        improve_rounds_used=1,
        outcome="success",
        final_question_id="q-123",
    )
    tr.add_node(
        NodeTrace(
            node="solver",
            duration_ms=1200,
            llm_model="claude-x",
            prompt_tokens_est=50,
            output_tokens_est=80,
            gate="self_consistency",
            passed=True,
            note="ok",
        )
    )

    payload = tr.model_dump_json()
    restored = GenerationTrace.model_validate_json(payload)

    assert restored.trace_id == tr.trace_id
    assert restored.tenant_id == "t1"
    assert restored.user_id == "u1"
    assert restored.request_summary == {"difficulty": 6, "type": "proof"}
    assert restored.rounds_used == 2
    assert restored.improve_rounds_used == 1
    assert restored.outcome == "success"
    assert restored.final_question_id == "q-123"
    assert restored.total_tokens_est == 130
    assert restored.created_at == tr.created_at

    assert len(restored.nodes) == 1
    node = restored.nodes[0]
    assert node.node == "solver"
    assert node.duration_ms == 1200
    assert node.llm_model == "claude-x"
    assert node.prompt_tokens_est == 50
    assert node.output_tokens_est == 80
    assert node.gate == "self_consistency"
    assert node.passed is True
    assert node.note == "ok"
