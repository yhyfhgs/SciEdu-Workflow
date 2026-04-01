"""LLM Tool Calling 工具定义"""

from __future__ import annotations

# ── Solver 可用工具 ──────────────────────────────────────

EXECUTE_PYTHON_TOOL = {
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

SIMPLIFY_EXPRESSION_TOOL = {
    "name": "simplify_expression",
    "description": (
        "使用 sympy 化简数学表达式。输入 LaTeX 或 sympy 格式的表达式，"
        "返回化简后的结果。"
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

VERIFY_NUMERICAL_TOOL = {
    "name": "verify_numerical",
    "description": (
        "对给定表达式在指定测试点进行数值求值，验证等式或不等式是否成立。"
        "返回每个测试点的计算结果。"
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

SOLVER_TOOLS = [EXECUTE_PYTHON_TOOL]

SOLVER_TOOLS_FULL = [
    EXECUTE_PYTHON_TOOL,
    SIMPLIFY_EXPRESSION_TOOL,
    VERIFY_NUMERICAL_TOOL,
]

VERIFIER_TOOLS = [EXECUTE_PYTHON_TOOL]


def get_tools_for_task(task: str) -> list[dict]:
    """根据任务类型返回可用工具列表"""
    match task:
        case "solve" | "solve_deep":
            return SOLVER_TOOLS
        case "verify" | "numerical_verify":
            return VERIFIER_TOOLS
        case _:
            return []


def to_anthropic_format(tools: list[dict]) -> list[dict]:
    """转换工具定义为 Anthropic API 格式"""
    return tools  # Anthropic 原生格式即为 name/description/input_schema


def to_openai_format(tools: list[dict]) -> list[dict]:
    """转换工具定义为 OpenAI API 格式"""
    result = []
    for tool in tools:
        result.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool.get("input_schema", {}),
            },
        })
    return result
