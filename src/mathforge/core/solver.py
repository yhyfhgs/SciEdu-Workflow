"""Stage 3: 求解引擎（支持多轮 + tool calling 深度求解）"""

from __future__ import annotations

import structlog

from mathforge.core.models import (
    ContextBundle,
    ConversationMessage,
    Question,
    Solution,
    ToolCall,
    ToolResult,
)
from mathforge.core.sandbox import CodeSandbox
from mathforge.llm import LLMClient, LLMConfig
from mathforge.llm.parser import parse_llm_json
from mathforge.llm.tool_definitions import SOLVER_TOOLS
from mathforge.prompts import SOLVE_PROMPT
from mathforge.prompts.tool_prompts import SOLVE_DEEP_SYSTEM, SOLVE_DEEP_USER

logger = structlog.get_logger()


class SolveEngine:
    """对候选题独立求解，支持单轮和多轮 tool calling 模式"""

    def __init__(
        self,
        llm: LLMClient | None = None,
        deep_llm: LLMClient | None = None,
        sandbox: CodeSandbox | None = None,
        max_turns: int = 5,
    ):
        self.llm = llm or LLMClient(LLMConfig.for_task("solve"))
        self.deep_llm = deep_llm or (
            LLMClient(LLMConfig.for_task("solve_deep")) if deep_llm is None else deep_llm
        )
        self.sandbox = sandbox or CodeSandbox()
        self.max_turns = max_turns

    async def solve(self, question: Question, context: ContextBundle) -> Solution | None:
        """异步求解一道题（单轮模式）"""
        prompt = self._build_prompt(question, context)
        try:
            result = await self.llm.generate_json(prompt=prompt, temperature=0.0)
        except Exception as e:
            logger.warning("solve_failed", error=str(e))
            return None

        confidence = result.get("confidence", "low")
        logger.info("solve_complete", confidence=confidence)

        return Solution(
            question_id=question.id,
            solution_latex=result.get("solution_latex", ""),
            methods_used=result.get("methods_used", []),
            step_count=result.get("step_count", 0),
            confidence=confidence,
        )

    def solve_sync(self, question: Question, context: ContextBundle) -> Solution | None:
        """同步求解一道题（单轮模式）"""
        prompt = self._build_prompt(question, context)
        try:
            result = self.llm.generate_json_sync(prompt=prompt, temperature=0.0)
        except Exception as e:
            logger.warning("solve_failed", error=str(e))
            return None

        confidence = result.get("confidence", "low")
        logger.info("solve_complete", confidence=confidence)

        return Solution(
            question_id=question.id,
            solution_latex=result.get("solution_latex", ""),
            methods_used=result.get("methods_used", []),
            step_count=result.get("step_count", 0),
            confidence=confidence,
        )

    # ── 深度求解（多轮 + tool calling）──────────────────

    async def solve_deep(
        self, question: Question, context: ContextBundle,
    ) -> Solution | None:
        """异步深度求解：多轮对话 + 代码执行辅助"""
        if not self.deep_llm.supports_tools:
            logger.info("solve_deep_fallback", reason="no_tool_support")
            return await self.solve(question, context)

        kp_context = self._format_kp_context(context)
        user_prompt = SOLVE_DEEP_USER.format(
            question_latex=question.content_latex,
            knowledge_context=kp_context,
        )

        messages = [{"role": "user", "content": user_prompt}]

        try:
            history = await self.deep_llm.generate_multiturn(
                messages=messages,
                system=SOLVE_DEEP_SYSTEM,
                tools=SOLVER_TOOLS,
                tool_executor=self._execute_tool,
                max_turns=self.max_turns,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning("solve_deep_failed", error=str(e))
            return await self.solve(question, context)

        return self._parse_deep_result(question, history)

    def solve_deep_sync(
        self, question: Question, context: ContextBundle,
    ) -> Solution | None:
        """同步深度求解"""
        if not self.deep_llm.supports_tools:
            logger.info("solve_deep_fallback", reason="no_tool_support")
            return self.solve_sync(question, context)

        kp_context = self._format_kp_context(context)
        user_prompt = SOLVE_DEEP_USER.format(
            question_latex=question.content_latex,
            knowledge_context=kp_context,
        )

        messages = [{"role": "user", "content": user_prompt}]

        try:
            history = self.deep_llm.generate_multiturn_sync(
                messages=messages,
                system=SOLVE_DEEP_SYSTEM,
                tools=SOLVER_TOOLS,
                tool_executor=self._execute_tool,
                max_turns=self.max_turns,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning("solve_deep_failed", error=str(e))
            return self.solve_sync(question, context)

        return self._parse_deep_result(question, history)

    def _execute_tool(self, call: ToolCall) -> ToolResult:
        """本地工具执行器"""
        if call.tool_name == "execute_python":
            code = call.arguments.get("code", "")
            result = self.sandbox.execute_safe(code)
            output = result.stdout if result.success else f"ERROR: {result.stderr}"
            logger.info(
                "tool_executed",
                tool=call.tool_name,
                success=result.success,
                elapsed_ms=result.execution_time_ms,
            )
            return ToolResult(call_id=call.call_id, output=output, is_error=not result.success)

        if call.tool_name == "simplify_expression":
            expr = call.arguments.get("expression", "")
            code = f"from sympy import *\nx = symbols('x'); print(simplify({expr}))"
            result = self.sandbox.execute_safe(code)
            output = result.stdout if result.success else f"ERROR: {result.stderr}"
            return ToolResult(call_id=call.call_id, output=output, is_error=not result.success)

        if call.tool_name == "verify_numerical":
            expr = call.arguments.get("expression", "")
            points = call.arguments.get("test_points", [])
            code = self._build_verify_code(expr, points)
            result = self.sandbox.execute_safe(code)
            output = result.stdout if result.success else f"ERROR: {result.stderr}"
            return ToolResult(call_id=call.call_id, output=output, is_error=not result.success)

        return ToolResult(
            call_id=call.call_id,
            output=f"未知工具: {call.tool_name}",
            is_error=True,
        )

    def _parse_deep_result(
        self, question: Question, history: list[ConversationMessage],
    ) -> Solution | None:
        """从多轮对话历史中提取最终解答"""
        # 找最后一条 assistant 消息
        final_content = ""
        for msg in reversed(history):
            if msg.role == "assistant" and msg.content:
                final_content = msg.content
                break

        if not final_content:
            return None

        # 尝试解析 JSON
        try:
            result = parse_llm_json(final_content)
        except Exception:
            # JSON 解析失败，将全文作为 solution_latex
            result = {"solution_latex": final_content, "confidence": "medium"}

        tool_calls_count = sum(len(m.tool_calls) for m in history if m.role == "assistant")

        return Solution(
            question_id=question.id,
            solution_latex=result.get("solution_latex", final_content),
            methods_used=result.get("methods_used", []),
            step_count=result.get("step_count", 0),
            confidence=result.get("confidence", "medium"),
            conversation_history=history,
            tool_calls_made=tool_calls_count,
        )

    @staticmethod
    def _build_prompt(question: Question, context: ContextBundle) -> str:
        kp_context = "\n".join(
            f"- {kp.title}: {kp.content_latex[:200]}"
            for kp in context.knowledge_points[:5]
        )
        if not kp_context:
            kp_context = "（无可参考知识点）"

        return SOLVE_PROMPT.format(
            question_latex=question.content_latex,
            knowledge_context=kp_context,
        )

    @staticmethod
    def _format_kp_context(context: ContextBundle) -> str:
        kp_context = "\n".join(
            f"- {kp.title}: {kp.content_latex[:200]}"
            for kp in context.knowledge_points[:5]
        )
        return kp_context if kp_context else "（无可参考知识点）"

    @staticmethod
    def _build_verify_code(expr: str, test_points: list[dict]) -> str:
        lines = ["from sympy import *"]
        for i, point in enumerate(test_points):
            assigns = ", ".join(f"{k} = {v}" for k, v in point.items())
            lines.append(f"{assigns}")
            lines.append(f"print(f'Test {i+1}: {{{expr}}}')")
        return "\n".join(lines)
