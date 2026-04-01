"""统一 LLM 客户端"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import structlog

from mathforge.core.exceptions import ToolCallError
from mathforge.core.models import ConversationMessage, ToolCall, ToolResult
from mathforge.llm.backends import ToolCallingBackend
from mathforge.llm.config import LLMConfig, LLMProvider
from mathforge.llm.parser import parse_llm_json

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()

DEFAULT_SYSTEM = "你是一位资深数学教育专家。"
JSON_SYSTEM = "你是一位资深数学教育专家。请严格按 JSON 格式输出，不要包含 markdown 代码块。"


class LLMClient:
    """统一 LLM 客户端，支持多提供者、同步/异步"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._backend = self._create_backend(config)
        # Pipeline logging
        self._call_count: int = 0
        self._log_interval: int = 10
        self._log_dir: str | None = None

    def _create_backend(self, config: LLMConfig):
        match config.provider:
            case LLMProvider.ANTHROPIC:
                from mathforge.llm.backends.anthropic import AnthropicBackend
                return AnthropicBackend(config)
            case LLMProvider.OPENAI:
                from mathforge.llm.backends.openai_backend import OpenAIBackend
                return OpenAIBackend(config)
            case LLMProvider.GEMINI:
                from mathforge.llm.backends.gemini import GeminiBackend
                return GeminiBackend(config)
            case LLMProvider.OPENAI_COMPATIBLE:
                from mathforge.llm.backends.compatible import OpenAICompatibleBackend
                return OpenAICompatibleBackend(config)
            case _:
                raise ValueError(f"未知提供者: {config.provider}")

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """异步生成文本"""
        system = system or DEFAULT_SYSTEM
        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        logger.debug(
            "llm_request",
            provider=self.config.provider.value,
            model=self.config.model,
            temperature=kwargs.get("temperature", self.config.temperature),
        )

        start = time.monotonic()
        result = await self._backend.generate(prompt=prompt, system=system, **kwargs)
        elapsed = int((time.monotonic() - start) * 1000)

        logger.info(
            "llm_response",
            provider=self.config.provider.value,
            model=self.config.model,
            prompt_length=len(prompt),
            response_length=len(result),
            latency_ms=elapsed,
        )
        self._log_exchange(prompt, result, elapsed)
        return result

    def generate_sync(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """同步生成文本"""
        system = system or DEFAULT_SYSTEM
        kwargs = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        logger.debug(
            "llm_request",
            provider=self.config.provider.value,
            model=self.config.model,
            temperature=kwargs.get("temperature", self.config.temperature),
        )

        start = time.monotonic()
        result = self._backend.generate_sync(prompt=prompt, system=system, **kwargs)
        elapsed = int((time.monotonic() - start) * 1000)

        logger.info(
            "llm_response",
            provider=self.config.provider.value,
            model=self.config.model,
            prompt_length=len(prompt),
            response_length=len(result),
            latency_ms=elapsed,
        )
        self._log_exchange(prompt, result, elapsed)
        return result

    async def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        **kwargs,
    ) -> dict:
        """异步生成并解析 JSON"""
        system = system or JSON_SYSTEM
        raw = await self.generate(prompt, system=system, **kwargs)
        return parse_llm_json(raw)

    def generate_json_sync(
        self,
        prompt: str,
        system: str | None = None,
        **kwargs,
    ) -> dict:
        """同步生成并解析 JSON"""
        system = system or JSON_SYSTEM
        raw = self.generate_sync(prompt, system=system, **kwargs)
        return parse_llm_json(raw)

    # ── Tool Calling ─────────────────────────────────────

    @property
    def supports_tools(self) -> bool:
        """当前后端是否支持 tool calling"""
        return isinstance(self._backend, ToolCallingBackend)

    async def generate_with_tools(
        self,
        messages: list[dict],
        system: str | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict:
        """异步 tool calling 单轮调用"""
        if not self.supports_tools:
            raise ToolCallError(f"后端 {self.config.provider} 不支持 tool calling")

        system = system or DEFAULT_SYSTEM
        start = time.monotonic()
        result = await self._backend.generate_with_tools(
            messages=messages, system=system, tools=tools or [], **kwargs,
        )
        elapsed = int((time.monotonic() - start) * 1000)

        logger.info(
            "llm_tool_response",
            provider=self.config.provider.value,
            tool_calls=len(result.get("tool_calls", [])),
            stop_reason=result.get("stop_reason"),
            latency_ms=elapsed,
        )
        return result

    def generate_with_tools_sync(
        self,
        messages: list[dict],
        system: str | None = None,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> dict:
        """同步 tool calling 单轮调用"""
        if not self.supports_tools:
            raise ToolCallError(f"后端 {self.config.provider} 不支持 tool calling")

        system = system or DEFAULT_SYSTEM
        start = time.monotonic()
        result = self._backend.generate_with_tools_sync(
            messages=messages, system=system, tools=tools or [], **kwargs,
        )
        elapsed = int((time.monotonic() - start) * 1000)

        logger.info(
            "llm_tool_response",
            provider=self.config.provider.value,
            tool_calls=len(result.get("tool_calls", [])),
            stop_reason=result.get("stop_reason"),
            latency_ms=elapsed,
        )
        return result

    # ── Multi-turn with Tool Execution ───────────────────

    async def generate_multiturn(
        self,
        messages: list[dict],
        system: str | None = None,
        tools: list[dict] | None = None,
        tool_executor: Callable[[ToolCall], ToolResult] | None = None,
        max_turns: int = 5,
        **kwargs,
    ) -> list[ConversationMessage]:
        """异步多轮对话 + 本地 tool 执行循环

        每轮：LLM 响应 → 检查 tool_calls → 执行 → 回传结果 → 下一轮
        直到 LLM 不再调用工具或达到 max_turns。

        Returns:
            完整对话历史
        """
        system = system or DEFAULT_SYSTEM
        history: list[ConversationMessage] = []
        current_messages = list(messages)

        for turn in range(max_turns):
            result = await self.generate_with_tools(
                messages=current_messages, system=system, tools=tools, **kwargs,
            )

            # 记录 assistant 消息
            assistant_msg = ConversationMessage(
                role="assistant",
                content=result.get("content", ""),
                tool_calls=[
                    ToolCall(
                        tool_name=tc["name"],
                        arguments=tc.get("arguments", {}),
                        call_id=tc.get("id", ""),
                    )
                    for tc in result.get("tool_calls", [])
                ],
            )
            history.append(assistant_msg)

            # 无工具调用，结束
            if not result.get("tool_calls"):
                break

            # 执行工具并收集结果
            tool_results: list[ToolResult] = []
            for tc in result["tool_calls"]:
                if tool_executor:
                    call = ToolCall(
                        tool_name=tc["name"],
                        arguments=tc.get("arguments", {}),
                        call_id=tc.get("id", ""),
                    )
                    tr = tool_executor(call)
                else:
                    tr = ToolResult(
                        call_id=tc.get("id", ""),
                        output="工具执行器未配置",
                        is_error=True,
                    )
                tool_results.append(tr)

            # 记录 tool 消息
            tool_msg = ConversationMessage(role="tool", tool_results=tool_results)
            history.append(tool_msg)

            # 构建下一轮消息（附加 assistant + tool results）
            current_messages.append({"role": "assistant", "content": result.get("content", "")})
            # Anthropic 格式: tool results 作为 user 消息
            tool_content = []
            for tr in tool_results:
                tool_content.append({
                    "type": "tool_result",
                    "tool_use_id": tr.call_id,
                    "content": tr.output,
                    "is_error": tr.is_error,
                })
            current_messages.append({"role": "user", "content": tool_content})

            logger.info(
                "multiturn_round",
                turn=turn + 1,
                tool_calls=len(result["tool_calls"]),
            )

        return history

    def generate_multiturn_sync(
        self,
        messages: list[dict],
        system: str | None = None,
        tools: list[dict] | None = None,
        tool_executor: Callable[[ToolCall], ToolResult] | None = None,
        max_turns: int = 5,
        **kwargs,
    ) -> list[ConversationMessage]:
        """同步多轮对话 + 本地 tool 执行循环"""
        system = system or DEFAULT_SYSTEM
        history: list[ConversationMessage] = []
        current_messages = list(messages)

        for turn in range(max_turns):
            result = self.generate_with_tools_sync(
                messages=current_messages, system=system, tools=tools, **kwargs,
            )

            assistant_msg = ConversationMessage(
                role="assistant",
                content=result.get("content", ""),
                tool_calls=[
                    ToolCall(
                        tool_name=tc["name"],
                        arguments=tc.get("arguments", {}),
                        call_id=tc.get("id", ""),
                    )
                    for tc in result.get("tool_calls", [])
                ],
            )
            history.append(assistant_msg)

            if not result.get("tool_calls"):
                break

            tool_results: list[ToolResult] = []
            for tc in result["tool_calls"]:
                if tool_executor:
                    call = ToolCall(
                        tool_name=tc["name"],
                        arguments=tc.get("arguments", {}),
                        call_id=tc.get("id", ""),
                    )
                    tr = tool_executor(call)
                else:
                    tr = ToolResult(
                        call_id=tc.get("id", ""),
                        output="工具执行器未配置",
                        is_error=True,
                    )
                tool_results.append(tr)

            tool_msg = ConversationMessage(role="tool", tool_results=tool_results)
            history.append(tool_msg)

            current_messages.append({"role": "assistant", "content": result.get("content", "")})
            tool_content = []
            for tr in tool_results:
                tool_content.append({
                    "type": "tool_result",
                    "tool_use_id": tr.call_id,
                    "content": tr.output,
                    "is_error": tr.is_error,
                })
            current_messages.append({"role": "user", "content": tool_content})

            logger.info(
                "multiturn_round",
                turn=turn + 1,
                tool_calls=len(result["tool_calls"]),
            )

        return history

    # ── Pipeline JSONL Logging ──────────────────────────────

    def enable_pipeline_logging(self, log_dir: str, interval: int = 10) -> None:
        """启用 Pipeline JSONL 日志"""
        self._log_dir = log_dir
        self._log_interval = interval

    def _log_exchange(self, prompt: str, response: str, latency_ms: int) -> None:
        """记录 LLM 交互到 JSONL 文件 + 定期 DEBUG 输出"""
        import json as _json
        from datetime import datetime, timezone
        from pathlib import Path

        self._call_count += 1

        # 定期记录完整 prompt/response 到 DEBUG
        if self._call_count % self._log_interval == 0:
            logger.debug(
                "llm_full_exchange",
                call_count=self._call_count,
                prompt=prompt[:2000],
                response=response[:2000],
            )

        # 写 JSONL 文件
        if self._log_dir:
            log_path = Path(self._log_dir)
            log_path.mkdir(parents=True, exist_ok=True)

            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "call_count": self._call_count,
                "provider": self.config.provider.value,
                "model": self.config.model,
                "prompt_length": len(prompt),
                "response_length": len(response),
                "latency_ms": latency_ms,
                "prompt": prompt[:5000],
                "response": response[:5000],
            }

            log_file = log_path / "llm_calls.jsonl"
            try:
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass
