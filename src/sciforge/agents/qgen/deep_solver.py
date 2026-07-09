"""DeepSolveEngine — 深度求解：多轮推理 + sandbox 工具调用（generate solve-deep）

用 solve_deep_system / solve_deep_user prompt + LLMClient.generate_multiturn 跑多轮：
LLM 推理过程中可调用 ``execute_python`` 工具（core.sandbox 沙箱执行 sympy/numpy）交叉
验证中间结果，直到不再调用工具或达 max_turns。后端不支持 tool calling 时降级为单轮
深度求解（system+user 合并，无工具）。
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from sciforge.agents.qgen.verification_sandbox import (
    ContainerPythonRunner,
    LeanVerifier,
    SandboxLimits,
    TrustedLocalPythonRunner,
    TrustPolicy,
    VerificationSandbox,
)
from sciforge.core.models import (
    ContextBundle,
    Discipline,
    Question,
    Solution,
    ToolCall,
    ToolResult,
)
from sciforge.llm.client import LLMClient
from sciforge.pipeline.retry import call_llm_json_with_retry
from sciforge.prompts.templates import get_generation_prompt_template

logger = structlog.get_logger()

# execute_python 工具定义（name/description/parameters；backend 适配为各 provider 格式）
_EXECUTE_PYTHON_TOOL: dict[str, Any] = {
    "name": "execute_python",
    "description": (
        "执行自包含 Python 代码（可用 sympy/numpy/math/fractions/decimal），"
        "用 print() 输出结果，用于符号/数值计算与中间结果的交叉验证。"
    ),
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "要执行的自包含 Python 代码"}},
        "required": ["code"],
    },
}


class DeepSolveEngine:
    """深度求解引擎：多轮推理 + 沙箱工具调用，降级兼容无工具后端。"""

    def __init__(
        self,
        llm: LLMClient,
        discipline: Discipline = Discipline.MATH,
        *,
        sandbox_timeout: int = 10,
        max_turns: int = 5,
        sandbox: VerificationSandbox | None = None,
        sandbox_trust_policy: TrustPolicy | str = TrustPolicy.CONTAINER_REQUIRED,
    ) -> None:
        self.llm = llm
        self.discipline = discipline
        self.sandbox_timeout = sandbox_timeout
        self.max_turns = max_turns
        if sandbox is None:
            limits = SandboxLimits(timeout_seconds=float(sandbox_timeout))
            sandbox = VerificationSandbox(
                local_python=TrustedLocalPythonRunner(limits=limits),
                container_python=ContainerPythonRunner(limits=limits),
                lean_verifier=LeanVerifier(limits=limits),
            )
        self.sandbox = sandbox
        self.sandbox_trust_policy = TrustPolicy(sandbox_trust_policy)
        self._system = get_generation_prompt_template("solving/solve_deep_system.txt", discipline)
        self._user_template = get_generation_prompt_template(
            "solving/solve_deep_user.txt", discipline
        )

    async def solve_deep(
        self, question: Question, context: ContextBundle | None = None
    ) -> Solution | None:
        """对题目做深度求解；解不出返回 None。"""
        user = self._user_template.format(
            question_latex=question.content,
            knowledge_context=_kp_context(context),
        )

        if not getattr(self.llm, "supports_tools", False):
            logger.info("solve_deep_no_tools_fallback", question_id=question.id)
            return await self._solve_single(user, question)

        async def _executor(call: ToolCall) -> ToolResult:
            code = str(call.arguments.get("code", ""))
            res = await self.sandbox.arun_python(
                code,
                trust_policy=self.sandbox_trust_policy,
            )
            out = (res.stdout or "")[:2000]
            if res.stderr:
                out += "\n[stderr]\n" + res.stderr[:1000]
            if res.reason:
                out += f"\n[{res.status.value}] {res.reason}"
            return ToolResult(
                call_id=call.call_id,
                output=out or "(无输出)",
                is_error=not res.success,
            )

        try:
            history = await self.llm.generate_multiturn(
                messages=[{"role": "user", "content": user}],
                system=self._system,
                tools=[_EXECUTE_PYTHON_TOOL],
                tool_executor=_executor,
                max_turns=self.max_turns,
            )
        except Exception as exc:
            logger.warning("solve_deep_multiturn_failed", error=str(exc)[:200])
            return await self._solve_single(user, question)

        content = ""
        for msg in reversed(history):
            if msg.role == "assistant" and msg.content.strip():
                content = msg.content
                break
        logger.info("solve_deep_done", question_id=question.id, turns=len(history))
        return _to_solution(_extract_json(content), question, content)

    async def _solve_single(self, user_prompt: str, question: Question) -> Solution | None:
        """降级路径：system+user 合并的单轮深度求解（无工具）。"""
        combined = f"{self._system}\n\n{user_prompt}"
        try:
            data = await call_llm_json_with_retry(self.llm, combined, max_attempts=2)
        except Exception as exc:
            logger.warning("solve_deep_single_failed", error=str(exc)[:200])
            return None
        return _to_solution(data, question, "")


def _kp_context(context: ContextBundle | None) -> str:
    if context is None or not context.knowledge_points:
        return ""
    return "\n".join(
        f"- {kp.number} [{kp.kp_type}]: {kp.content[:200]}" for kp in context.knowledge_points[:5]
    )


def _extract_json(text: str) -> dict[str, Any]:
    """从（可能含自然语言的）文本中提取首个 JSON 对象，失败返回空 dict。"""
    if not text:
        return {}
    candidates = [text]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return {}


def _to_solution(data: dict[str, Any], question: Question, raw: str) -> Solution | None:
    solution_text = str(data.get("solution_latex") or data.get("solution") or raw or "").strip()
    if not solution_text:
        return None
    conf = str(data.get("confidence", "medium")).lower()
    if conf not in ("high", "medium", "low"):
        conf = "medium"
    return Solution(
        question_id=question.id,
        solution_latex=solution_text,
        methods_used=list(data.get("methods_used") or []),
        step_count=_non_negative_int(data.get("step_count")),
        self_check=str(data.get("self_check") or ""),
        confidence=conf,  # type: ignore[arg-type]
    )


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if not isinstance(value, (int, float, str)):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
