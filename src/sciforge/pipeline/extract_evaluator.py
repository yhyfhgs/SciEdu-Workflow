"""M4 WP-44：风险触发 Evaluator 多轮（v11 I.4/I.6）——条件化二次验证。

设计原则（Docs/20 §4，Anthropic harness 文章）：
"让 agent 评估自己的产出时，它们往往自信地夸赞——即便质量平庸"——因此
Evaluator 用**独立 system**（"审稿人"角色），通过 WP-42 注册的确定性工具
核实原文，工具结果是 ground truth，LLM **不能否决**工具结果本身。

**只在高风险窗口触发**（harness.risk_score 超阈值，见 I.6 三档判定）：
70-80% 干净窗口 0 额外 LLM 调用；仅编号跳号/near-tail/边界可疑等窗口才
触发多轮。provider 约束（C8）：``generate_multiturn`` 的 tool_result 回传
硬编码 Anthropic Messages 格式，OpenAI/Gemini 自动退化为单轮（本模块跳过，
调用方继续依赖 harness 已完成的确定性审计，不放弃 coverage 检查）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from sciforge.core.models import ToolCall, ToolResult
from sciforge.llm.config import LLMProvider
from sciforge.pipeline.extract_tools import EVALUATOR_TOOLS, execute_extract_tool

if TYPE_CHECKING:
    from sciforge.llm.client import LLMClient

# C8：generate_multiturn 的 tool_result 回传硬编码 Anthropic Messages 格式，
# 仅这两类 provider 走得通；OpenAI/Gemini 调用会失败或格式不兼容。
MULTITURN_SUPPORTED_PROVIDERS = (LLMProvider.ANTHROPIC, LLMProvider.ANTHROPIC_COMPATIBLE)

EVALUATOR_SYSTEM = (
    "你是审稿人（Evaluator），职责是对照原文与确定性工具的真值结果，核实提取者的"
    "判断是否有遗漏条目、边界错误、编号跳号或题答配对不自洽。\n"
    "工具返回的结果是确定性真值，你不能否决或质疑工具结果本身，只能基于工具结果"
    "调整你的判断——不要相信提取者自述，一切以工具核实结果为准。\n"
    "请至少调用一次工具核实后，用简短文字总结发现的问题；"
    "若审核后未发现问题，明确说明「审核通过，未发现问题」。"
)


class EvaluatorVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ran: bool = False  # False = 因 provider 不支持而跳过（未消耗 LLM 调用）
    turns_used: int = 0
    tool_calls_made: int = 0
    summary: str = ""
    skip_reason: str = ""


def _tool_executor(call: ToolCall) -> ToolResult:
    """generate_multiturn 的 tool_executor：路由到 M1-M3 确定性纯函数。

    同步函数（``generate_multiturn`` 的 ``tool_executor`` 签名要求同步）；
    工具本体全部纯 Python、无 IO，天然适配。
    """
    try:
        result = execute_extract_tool(call.tool_name, call.arguments)
        return ToolResult(call_id=call.call_id, output=json.dumps(result, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 — 工具失败回传错误信息，不让 multiturn 中断
        return ToolResult(call_id=call.call_id, output=f"工具执行失败: {exc}", is_error=True)


def build_review_prompt(window_text: str, extracted_summary: str) -> str:
    """构造 Evaluator 的初始审核请求（不含 system，system 见 ``EVALUATOR_SYSTEM``）。"""
    return (
        "## 待审核窗口原文（含全局行号）\n"
        f"{window_text}\n\n"
        "## 提取者已提取的条目摘要\n"
        f"{extracted_summary}\n\n"
        "请调用工具核实是否存在遗漏条目、边界错误、编号跳号或题答配对不自洽。"
    )


def supports_multiturn_evaluator(llm: LLMClient) -> bool:
    """返回当前 client 是否能安全执行 evaluator 的 tool-result 回路。

    这里刻意做成无副作用能力探测，让 Stage1 在构造 prompt、调用 evaluator
    之前就能跳过不支持的 provider；缺失/测试用 config 也按不支持处理。
    """
    provider = getattr(getattr(llm, "config", None), "provider", None)
    return provider in MULTITURN_SUPPORTED_PROVIDERS


async def run_evaluator(
    prompt: str,
    *,
    llm: LLMClient,
    max_turns: int = 3,
) -> EvaluatorVerdict:
    """条件触发的 Evaluator 多轮回路（仅高风险窗口调用，见 I.6）。

    provider 不支持 multiturn（C8）→ 返回 ``ran=False``，**不消耗任何 LLM
    调用**——调用方应继续依赖 harness 已完成的确定性审计（③AUDIT），不因
    evaluator 跳过而放弃 coverage 检查。
    """
    if not supports_multiturn_evaluator(llm):
        provider = getattr(getattr(llm, "config", None), "provider", None)
        provider_name = getattr(provider, "value", str(provider or "unknown"))
        return EvaluatorVerdict(
            ran=False,
            skip_reason=f"provider={provider_name} 不支持 multiturn（C8），已跳过",
        )

    history = await llm.generate_multiturn(
        [{"role": "user", "content": prompt}],
        system=EVALUATOR_SYSTEM,
        tools=EVALUATOR_TOOLS,
        tool_executor=_tool_executor,
        max_turns=max_turns,
    )

    turns_used = sum(1 for m in history if m.role == "assistant")
    tool_calls_made = sum(len(m.tool_calls) for m in history if m.role == "assistant")
    summary = next(
        (m.content for m in reversed(history) if m.role == "assistant" and m.content), ""
    )
    return EvaluatorVerdict(
        ran=True, turns_used=turns_used, tool_calls_made=tool_calls_made, summary=summary
    )


# ── I.6 风险分档（复用 harness.risk_score，纯确定性判定是否触发）───


def should_trigger_evaluator(risk_score: float, *, threshold: float = 0.3) -> bool:
    """risk_score 超阈值才触发 evaluator——70-80% 干净窗口应为 0 额外调用（I.6）。"""
    return risk_score >= threshold
