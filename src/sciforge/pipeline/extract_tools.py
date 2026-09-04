"""T4/T6/T7/T8 注册为 LLM 可调用 ToolSpec（v11 §3.3/II.2，M4，Evaluator 多轮用）。

复用现有 ``llm/tool_definitions.py`` 的 ``ToolSpec`` 结构与
``to_anthropic_format``/``to_openai_format``——不新建格式层。工具本体是
``text_tools``/``boundary_patch`` 里已实现的纯函数（M1/M2 落地），本模块只做
"LLM 工具调用协议" 的薄封装 + dispatcher。

Schema 约束（C2，DeepSeek strict 工具调用）：全属性进 ``required`` +
``additionalProperties:false``，避开 ``minItems/maxItems/minLength/maxLength``。

面向场景：M4 Evaluator（"审稿人"角色）在 multiturn 回路中主动调用这些
只读确定性工具核实原文，工具结果是 ground truth，LLM 不能否决（§4）。
"""

from __future__ import annotations

from typing import Any

from sciforge.llm.tool_definitions import ToolSpec

FIND_ITEM_MARKERS_TOOL: ToolSpec = {
    "name": "find_item_markers",
    "description": (
        "在给定原文文本片段上做确定性正则扫描，返回全部条目起始标记"
        "（定理/定义/引理/推论/命题/例题/习题/问题/解答/证明等）的行号、类型、"
        "编号提示与强度。用于核实某段文本是否存在被遗漏的条目标记——"
        "结果是确定性真值，不是模型猜测。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待扫描的原文文本片段（保留原始换行）",
            },
            "line_base": {
                "type": "integer",
                "description": "text 首行对应的全局行号（1-based）",
            },
        },
        "required": ["text", "line_base"],
        "additionalProperties": False,
    },
}

AUDIT_COVERAGE_TOOL: ToolSpec = {
    "name": "audit_coverage",
    "description": (
        "给定原文文本片段与已提取条目的行区间，返回哪些条目起始标记未被任何"
        "已提取条目覆盖（候选遗漏）、以及条目区间内部是否吞并了相邻标记"
        "（边界可疑）。用于核实提取结果相对原文是否有遗漏或错配。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "待审计的原文文本片段（保留原始换行）",
            },
            "line_base": {
                "type": "integer",
                "description": "text 首行对应的全局行号（1-based）",
            },
            "item_ranges": {
                "type": "array",
                "description": "已提取条目的行区间列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_ref": {"type": "string", "description": "条目引用标识"},
                        "start_line": {"type": "integer", "description": "起始全局行号"},
                        "end_line": {"type": "integer", "description": "结束全局行号"},
                    },
                    "required": ["item_ref", "start_line", "end_line"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["text", "line_base", "item_ranges"],
        "additionalProperties": False,
    },
}

CHECK_NUMBERING_CONTINUITY_TOOL: ToolSpec = {
    "name": "check_numbering_continuity",
    "description": (
        "检查一组按文档顺序排列的条目编号（如 ['3.1','3.2','3.4']）是否存在"
        "跳号、重号或乱序。同前缀相邻编号才比较，前缀切换视为新序列。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "numbers": {
                "type": "array",
                "description": "按文档顺序排列的编号原文列表",
                "items": {"type": "string"},
            },
        },
        "required": ["numbers"],
        "additionalProperties": False,
    },
}

CHECK_PAIRING_TOOL: ToolSpec = {
    "name": "check_pairing",
    "description": (
        "检查一批条目的题答/定理证明配对是否自洽：声称 complete 却无解答正文、"
        "有解答正文却状态不是 complete、习题类条目贴近窗口末尾却声称无解答"
        "（疑似截断误判）。返回具体不自洽的条目与原因。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "待检查条目列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_ref": {"type": "string", "description": "条目引用标识"},
                        "category": {"type": "string", "description": "knowledge 或 question"},
                        "item_type": {"type": "string", "description": "条目类型"},
                        "solution_status": {
                            "type": "string",
                            "description": "complete/no_solution_in_source/solution_deferred",
                        },
                        "has_payload": {
                            "type": "boolean",
                            "description": "是否已有解答/证明正文",
                        },
                        "start_line": {"type": "integer", "description": "起始全局行号"},
                        "end_line": {"type": "integer", "description": "结束全局行号"},
                    },
                    "required": [
                        "item_ref",
                        "category",
                        "item_type",
                        "solution_status",
                        "has_payload",
                        "start_line",
                        "end_line",
                    ],
                    "additionalProperties": False,
                },
            },
            "window_last_line": {
                "type": "integer",
                "description": "当前窗口末行全局行号（用于判定是否贴近窗口末尾）",
            },
        },
        "required": ["items", "window_last_line"],
        "additionalProperties": False,
    },
}

EVALUATOR_TOOLS: list[ToolSpec] = [
    FIND_ITEM_MARKERS_TOOL,
    AUDIT_COVERAGE_TOOL,
    CHECK_NUMBERING_CONTINUITY_TOOL,
    CHECK_PAIRING_TOOL,
]


def execute_extract_tool(name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    """dispatcher：工具名 → 调用对应确定性纯函数 → 返回可 JSON 序列化的结果。

    未知工具名抛 ``ValueError``（不静默返回空结果，避免 Evaluator 误以为"查了但没发现问题"）。
    """
    if name == "find_item_markers":
        from sciforge.pipeline.text_tools import find_item_markers

        markers = find_item_markers(
            tool_input["text"].splitlines(), line_base=tool_input["line_base"]
        )
        return {"markers": [m.model_dump() for m in markers]}

    if name == "audit_coverage":
        from sciforge.pipeline.boundary_patch import AuditItem, audit_coverage, build_marker_ledger

        lines = tool_input["text"].splitlines()
        ledger = build_marker_ledger(lines, line_base=tool_input["line_base"])
        audit_items = [
            AuditItem(
                item_ref=r["item_ref"],
                start_line=r["start_line"],
                end_line=r["end_line"],
            )
            for r in tool_input["item_ranges"]
        ]
        audit_report = audit_coverage(ledger, audit_items)
        return {
            "missed_markers": [m.model_dump() for m in audit_report.missed_markers],
            "boundary_suspects": [s.model_dump() for s in audit_report.boundary_suspects],
            "clean": audit_report.clean,
        }

    if name == "check_numbering_continuity":
        from sciforge.pipeline.text_tools import check_numbering_continuity

        numbering_report = check_numbering_continuity(tool_input["numbers"])
        return {
            "issues": [i.model_dump() for i in numbering_report.issues],
            "ok": numbering_report.ok,
        }

    if name == "check_pairing":
        from sciforge.pipeline.text_tools import PairingItem, check_pairing

        pairing_items = [PairingItem.model_validate(it) for it in tool_input["items"]]
        pairing_report = check_pairing(
            pairing_items, window_last_line=tool_input["window_last_line"]
        )
        return {
            "issues": [i.model_dump() for i in pairing_report.issues],
            "ok": pairing_report.ok,
        }

    raise ValueError(f"未知工具: {name}")
