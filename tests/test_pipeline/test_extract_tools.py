"""WP-42 extract_tools ToolSpec 注册——strict schema 约束 + dispatcher。"""

from __future__ import annotations

import pytest

from sciforge.llm.tool_definitions import to_anthropic_format, to_openai_format
from sciforge.pipeline.extract_tools import (
    EVALUATOR_TOOLS,
    execute_extract_tool,
)

_FORBIDDEN_KEYS = ("minItems", "maxItems", "minLength", "maxLength")


def _walk(node: object) -> list[dict]:
    """递归收集 schema 中所有 dict 节点（含嵌套 items/properties）。"""
    found: list[dict] = []
    if isinstance(node, dict):
        found.append(node)
        for v in node.values():
            found.extend(_walk(v))
    elif isinstance(node, list):
        for item in node:
            found.extend(_walk(item))
    return found


# ── strict schema 约束（C2）───────────────────────────────


@pytest.mark.parametrize("tool", EVALUATOR_TOOLS, ids=lambda t: t["name"])
def test_no_forbidden_constraints(tool: dict) -> None:
    for node in _walk(tool["input_schema"]):
        for key in _FORBIDDEN_KEYS:
            assert key not in node, f"{tool['name']} 含被禁用约束 {key}"


@pytest.mark.parametrize("tool", EVALUATOR_TOOLS, ids=lambda t: t["name"])
def test_all_object_nodes_fully_required_and_closed(tool: dict) -> None:
    for node in _walk(tool["input_schema"]):
        if node.get("type") == "object" and "properties" in node:
            assert set(node["required"]) == set(node["properties"].keys()), (
                f"{tool['name']}: 存在未进 required 的属性"
            )
            assert node.get("additionalProperties") is False


def test_all_tools_have_name_and_description() -> None:
    for tool in EVALUATOR_TOOLS:
        assert tool["name"]
        assert tool["description"]


# ── 格式转换复用（不新建格式层）────────────────────────────


def test_to_anthropic_format_passthrough() -> None:
    assert to_anthropic_format(EVALUATOR_TOOLS) == EVALUATOR_TOOLS


def test_to_openai_format_wraps_function() -> None:
    openai_tools = to_openai_format(EVALUATOR_TOOLS)
    assert all(t["type"] == "function" for t in openai_tools)
    assert openai_tools[0]["function"]["name"] == "find_item_markers"


# ── dispatcher ────────────────────────────────────────────


def test_dispatch_find_item_markers() -> None:
    result = execute_extract_tool(
        "find_item_markers", {"text": "定理 3.1 内容说明。", "line_base": 101}
    )
    assert result["markers"][0]["kind"] == "theorem"
    assert result["markers"][0]["global_line"] == 101


def test_dispatch_audit_coverage_detects_missed() -> None:
    text = "定理 3.1 内容。\n习题 3.1 求极限。"
    result = execute_extract_tool(
        "audit_coverage",
        {
            "text": text,
            "line_base": 101,
            "item_ranges": [{"item_ref": "p1", "start_line": 101, "end_line": 101}],
        },
    )
    assert result["clean"] is False
    assert len(result["missed_markers"]) == 1
    assert result["missed_markers"][0]["global_line"] == 102


def test_dispatch_check_numbering_continuity_finds_gap() -> None:
    result = execute_extract_tool("check_numbering_continuity", {"numbers": ["3.1", "3.3"]})
    assert result["ok"] is False
    assert result["issues"][0]["kind"] == "gap"


def test_dispatch_check_pairing_finds_issue() -> None:
    result = execute_extract_tool(
        "check_pairing",
        {
            "items": [
                {
                    "item_ref": "p1",
                    "category": "question",
                    "item_type": "exercise",
                    "solution_status": "complete",
                    "has_payload": False,
                    "start_line": 1,
                    "end_line": 5,
                }
            ],
            "window_last_line": 100,
        },
    )
    assert result["ok"] is False


def test_dispatch_unknown_tool_raises() -> None:
    with pytest.raises(ValueError, match="未知工具"):
        execute_extract_tool("not_a_tool", {})
