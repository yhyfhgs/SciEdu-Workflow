"""LLM Tool Calling 工具定义"""

from __future__ import annotations

from typing import Any

ToolSpec = dict[str, Any]

# ── Solver 可用工具 ──────────────────────────────────────

EXECUTE_PYTHON_TOOL: ToolSpec = {
    "name": "execute_python",
    "description": (
        "在受限 Python 环境中执行代码。可用库: sympy, numpy, math, fractions, decimal。"
        "用于数值计算、符号化简、矩阵运算等。代码的 print 输出会返回给你。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "要执行的 Python 代码",
            },
            "purpose": {
                "type": "string",
                "description": "简述执行目的（便于调试）",
            },
        },
        "required": ["code"],
    },
}

SIMPLIFY_EXPRESSION_TOOL: ToolSpec = {
    "name": "simplify_expression",
    "description": (
        "使用 sympy 化简数学表达式。输入 LaTeX 或 sympy 格式的表达式，返回化简后的结果。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "待化简的表达式（sympy 语法）",
            },
            "assumptions": {
                "type": "string",
                "description": "变量假设，如 'x > 0, n is integer'",
            },
        },
        "required": ["expression"],
    },
}

VERIFY_NUMERICAL_TOOL: ToolSpec = {
    "name": "verify_numerical",
    "description": (
        "对给定表达式在指定测试点进行数值求值，验证等式或不等式是否成立。返回每个测试点的计算结果。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "待验证的表达式（sympy 语法）",
            },
            "test_points": {
                "type": "array",
                "items": {"type": "object"},
                "description": "测试点列表，每个为 {变量名: 数值} 的字典",
            },
            "expected": {
                "type": "string",
                "description": "期望结果或关系（如 '== 0', '> 0', '== True'）",
            },
        },
        "required": ["expression", "test_points"],
    },
}


# ── 工具集合 ─────────────────────────────────────────────

SOLVER_TOOLS: list[ToolSpec] = [EXECUTE_PYTHON_TOOL]

SOLVER_TOOLS_FULL: list[ToolSpec] = [
    EXECUTE_PYTHON_TOOL,
    SIMPLIFY_EXPRESSION_TOOL,
    VERIFY_NUMERICAL_TOOL,
]

VERIFIER_TOOLS: list[ToolSpec] = [EXECUTE_PYTHON_TOOL]


def get_tools_for_task(task: str) -> list[ToolSpec]:
    """根据任务类型返回可用工具列表"""
    match task:
        case "solve" | "solve_deep":
            return SOLVER_TOOLS
        case "verify" | "numerical_verify":
            return VERIFIER_TOOLS
        case _:
            return []


def to_anthropic_format(tools: list[ToolSpec]) -> list[ToolSpec]:
    """转换中性工具定义为 Anthropic API 格式。

    仓库早期同时出现过 ``input_schema``（Anthropic 命名）与
    ``parameters``（OpenAI 命名）。统一入口接受两者，避免同一工具在某个
    provider 上被悄悄下发为空 schema。
    """
    result: list[ToolSpec] = []
    for tool in tools:
        converted = {key: value for key, value in tool.items() if key != "parameters"}
        converted["input_schema"] = _tool_input_schema(tool)
        result.append(converted)
    return result


def to_openai_format(tools: list[ToolSpec]) -> list[ToolSpec]:
    """转换工具定义为 OpenAI API 格式"""
    result: list[ToolSpec] = []
    for tool in tools:
        result.append(
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": _tool_input_schema(tool),
                },
            }
        )
    return result


def _tool_input_schema(tool: ToolSpec) -> dict[str, Any]:
    """读取工具参数 schema，并拒绝会在 provider 侧产生歧义的非对象值。"""
    schema = tool.get("input_schema", tool.get("parameters", {}))
    if not isinstance(schema, dict):
        raise TypeError(f"工具 {tool.get('name', '<unknown>')} 的参数 schema 必须是对象")
    return schema
