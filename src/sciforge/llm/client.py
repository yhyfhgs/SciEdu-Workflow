"""统一 LLM 客户端"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC
from typing import TYPE_CHECKING, Any, TypeVar, cast

import structlog

from sciforge.core.exceptions import LLMVisionError, ToolCallError
from sciforge.core.models import ConversationMessage, ToolCall, ToolResult
from sciforge.llm.backends import ToolCallingBackend, VisionBackend
from sciforge.llm.config import LLMConfig, LLMProvider
from sciforge.llm.parser import parse_llm_json

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()

T = TypeVar("T")

DEFAULT_SYSTEM = "你是一位资深数学教育专家。"
JSON_SYSTEM = "你是一位资深数学教育专家。请严格按 JSON 格式输出，不要包含 markdown 代码块。"

# REC-27：llm_calls.jsonl 单条 prompt/response 截断上限 + 文件轮转阈值，防 VLM 大 prompt 撑爆
_LOG_TEXT_CAP = 8000
_LOG_ROTATE_BYTES = 64 * 1024 * 1024  # 64 MB


class LLMClient:
    """统一 LLM 客户端，支持多提供者、同步/异步"""

    def __init__(self, config: LLMConfig):
        self.config = config
        self._backend = self._create_backend(config)
        # Pipeline logging
        self._call_count: int = 0
        self._log_interval: int = 10
        self._log_dir: str | None = None
        # 全局 semaphore（跨 model 的总并发上限）+ per-model semaphore（按 model id 限流）
        # 两者嵌套：获得全局许可后再争 model 许可，保证 provider 级限流精准
        self._request_semaphore: asyncio.Semaphore | None = None
        self._model_semaphore: asyncio.Semaphore | None = None

    @property
    def last_usage(self) -> dict[str, int]:
        """REC-08：最近一次调用的真实 token usage（含 cache_read/creation_input_tokens）。

        backend 不支持/未采集时返回 ``{}``。供 retry 的 ``llm_call_metrics`` 用真实 token
        替代 ``chars//4`` 估算（中文严重偏差），并验证 prompt cache 命中。
        """
        return getattr(self._backend, "last_usage", {})

    def set_request_semaphore(self, semaphore: asyncio.Semaphore | None) -> None:
        """绑定跨 model 全局并发闸门（对应 AppConfig.max_concurrent_llm_calls）"""
        self._request_semaphore = semaphore

    def set_model_semaphore(self, semaphore: asyncio.Semaphore | None) -> None:
        """绑定 per-model 并发闸门（对应 AppConfig.llm_model_concurrency[self.config.model]）

        None 表示该 model 无独立限流，仅受全局 semaphore 约束。
        """
        self._model_semaphore = semaphore

    async def _run_with_request_limit(self, fn: Callable[[], Awaitable[T]]) -> T:
        """两层嵌套：先全局，再 per-model；未设置则直通"""
        global_sem = getattr(self, "_request_semaphore", None)
        model_sem = getattr(self, "_model_semaphore", None)
        if global_sem is None and model_sem is None:
            return await fn()
        if global_sem is None:
            assert model_sem is not None
            async with model_sem:
                return await fn()
        if model_sem is None:
            async with global_sem:
                return await fn()
        async with global_sem:
            async with model_sem:
                return await fn()

    def _create_backend(self, config: LLMConfig) -> Any:
        match config.provider:
            case LLMProvider.ANTHROPIC:
                from sciforge.llm.backends.anthropic import AnthropicBackend

                return AnthropicBackend(config)
            case LLMProvider.ANTHROPIC_COMPATIBLE:
                from sciforge.llm.backends.anthropic_compatible import (
                    AnthropicCompatibleBackend,
                )

                return AnthropicCompatibleBackend(config)
            case LLMProvider.OPENAI:
                from sciforge.llm.backends.openai_backend import OpenAIBackend

                return OpenAIBackend(config)
            case LLMProvider.GEMINI:
                from sciforge.llm.backends.gemini import GeminiBackend

                return GeminiBackend(config)
            case LLMProvider.OPENAI_COMPATIBLE:
                from sciforge.llm.backends.compatible import OpenAICompatibleBackend

                return OpenAICompatibleBackend(config)
            case _:
                raise ValueError(f"未知提供者: {config.provider}")

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> str:
        """异步生成文本

        ``**extra`` 透传额外生成参数（如 ``response_format`` / ``thinking`` / ``seed``）；
        backend 的 ``_resolve_params`` 会按 provider 能力矩阵静默过滤掉不支持项
        （例如 anthropic 兼容网关丢弃 ``response_format``）。
        """
        system = system or DEFAULT_SYSTEM
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        for key, value in extra.items():
            if value is not None:
                kwargs[key] = value

        logger.debug(
            "llm_request",
            provider=self.config.provider.value,
            model=self.config.model,
            temperature=kwargs.get("temperature", self.config.temperature),
        )

        start = time.monotonic()
        result = cast(
            str | None,
            await self._run_with_request_limit(
                lambda: self._backend.generate(prompt=prompt, system=system, **kwargs)
            ),
        )
        elapsed = int((time.monotonic() - start) * 1000)

        if result is None:
            logger.warning(
                "llm_response_none",
                provider=self.config.provider.value,
                model=self.config.model,
            )
            result = ""

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
        **extra: Any,
    ) -> str:
        """同步生成文本

        ``**extra`` 透传额外生成参数（如 ``response_format`` / ``thinking`` / ``seed``）；
        backend 的 ``_resolve_params`` 会按 provider 能力矩阵静默过滤掉不支持项。
        """
        system = system or DEFAULT_SYSTEM
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        for key, value in extra.items():
            if value is not None:
                kwargs[key] = value

        logger.debug(
            "llm_request",
            provider=self.config.provider.value,
            model=self.config.model,
            temperature=kwargs.get("temperature", self.config.temperature),
        )

        start = time.monotonic()
        result = cast(
            str | None,
            self._backend.generate_sync(prompt=prompt, system=system, **kwargs),
        )
        elapsed = int((time.monotonic() - start) * 1000)

        if result is None:
            logger.warning(
                "llm_response_none",
                provider=self.config.provider.value,
                model=self.config.model,
            )
            result = ""

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

    def _default_json_mode(self, kwargs: dict[str, Any]) -> None:
        """REC-07：JSON 调用默认启用原生 JSON 模式，降低非法 JSON → 减少重试。

        只在调用方与 config 都未显式设置 response_format 时注入。能力矩阵会按 provider 过滤：
        OpenAI / Gemini / OpenAI 兼容保留生效；Anthropic / anthropic 兼容不支持 → 静默丢弃
        （故对生产兼容网关零影响，依旧靠 prompt + thinking 控制）。
        """
        cfg_has = getattr(self.config, "response_format", None) is not None
        if "response_format" not in kwargs and not cfg_has:
            kwargs["response_format"] = {"type": "json_object"}

    async def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """异步生成并解析 JSON"""
        system = system or JSON_SYSTEM
        self._default_json_mode(kwargs)
        raw = await self.generate(prompt, system=system, **kwargs)
        return parse_llm_json(raw)

    def generate_json_sync(
        self,
        prompt: str,
        system: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """同步生成并解析 JSON"""
        system = system or JSON_SYSTEM
        self._default_json_mode(kwargs)
        raw = self.generate_sync(prompt, system=system, **kwargs)
        return parse_llm_json(raw)

    # ── Message Batches（REC-23：异步批处理，约半价）─────────────────
    @property
    def supports_batch(self) -> bool:
        """当前后端是否提供 Message Batches API（非交互式批处理）。"""
        return hasattr(self._backend, "submit_message_batch")

    async def submit_message_batch(self, requests: list[dict[str, Any]]) -> str:
        """提交一批独立请求，返回 batch_id（详见 backend.submit_message_batch）。"""
        return await self._backend.submit_message_batch(requests)  # type: ignore[no-any-return]

    async def get_message_batch_status(self, batch_id: str) -> str:
        return await self._backend.get_message_batch_status(batch_id)  # type: ignore[no-any-return]

    async def fetch_message_batch_results(self, batch_id: str) -> dict[str, str]:
        return await self._backend.fetch_message_batch_results(batch_id)  # type: ignore[no-any-return]

    # ── Vision (多模态) ───────────────────────────────────

    @property
    def supports_vision(self) -> bool:
        """当前后端是否支持多模态图像输入"""
        return isinstance(self._backend, VisionBackend)

    async def generate_with_image(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str = "image/png",
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """异步多模态生成：图像 + 文本 → 文本"""
        if not self.supports_vision:
            raise LLMVisionError(f"后端 {self.config.provider} 不支持多模态图像输入")

        system = system or DEFAULT_SYSTEM
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        logger.debug(
            "llm_vision_request",
            provider=self.config.provider.value,
            model=self.config.model,
            image_size_bytes=len(image_data),
            image_media_type=image_media_type,
        )

        start = time.monotonic()
        result = cast(
            str | None,
            await self._run_with_request_limit(
                lambda: self._backend.generate_vision(
                    prompt=prompt,
                    image_data=image_data,
                    image_media_type=image_media_type,
                    system=system,
                    **kwargs,
                )
            ),
        )
        elapsed = int((time.monotonic() - start) * 1000)

        if result is None:
            logger.warning(
                "llm_response_none",
                provider=self.config.provider.value,
                model=self.config.model,
            )
            result = ""

        logger.info(
            "llm_vision_response",
            provider=self.config.provider.value,
            model=self.config.model,
            image_size_bytes=len(image_data),
            response_length=len(result),
            latency_ms=elapsed,
        )
        self._log_exchange(f"[image:{len(image_data)}B] {prompt}", result, elapsed)
        return result

    async def generate_with_images(
        self,
        prompt: str,
        images: list[bytes],
        image_media_type: str = "image/png",
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """异步多图 vision 生成"""
        if not self.supports_vision:
            raise LLMVisionError(f"后端 {self.config.provider} 不支持多模态图像输入")

        system = system or DEFAULT_SYSTEM
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        logger.debug(
            "llm_multi_vision_request",
            provider=self.config.provider.value,
            model=self.config.model,
            image_count=len(images),
            total_bytes=sum(len(i) for i in images),
        )

        if not images:
            raise LLMVisionError("generate_with_images: images 列表为空")

        if not hasattr(self._backend, "generate_vision_multi"):
            raise LLMVisionError(
                f"后端 {self.config.provider} 未实现 generate_vision_multi，"
                f"无法发送 {len(images)} 张图像"
            )

        start = time.monotonic()
        result = cast(
            str | None,
            await self._run_with_request_limit(
                lambda: self._backend.generate_vision_multi(
                    prompt=prompt,
                    images=images,
                    image_media_type=image_media_type,
                    system=system,
                    **kwargs,
                )
            ),
        )
        elapsed = int((time.monotonic() - start) * 1000)

        if result is None:
            result = ""

        logger.info(
            "llm_multi_vision_response",
            model=self.config.model,
            image_count=len(images),
            total_image_bytes=sum(len(i) for i in images),
            response_length=len(result),
            latency_ms=elapsed,
        )
        self._log_exchange(f"[images:{len(images)}] {prompt[:200]}", result, elapsed)
        return result

    def generate_with_image_sync(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str = "image/png",
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """同步多模态生成：图像 + 文本 → 文本"""
        if not self.supports_vision:
            raise LLMVisionError(f"后端 {self.config.provider} 不支持多模态图像输入")

        system = system or DEFAULT_SYSTEM
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        logger.debug(
            "llm_vision_request",
            provider=self.config.provider.value,
            model=self.config.model,
            image_size_bytes=len(image_data),
            image_media_type=image_media_type,
        )

        start = time.monotonic()
        result = cast(
            str | None,
            self._backend.generate_vision_sync(
                prompt=prompt,
                image_data=image_data,
                image_media_type=image_media_type,
                system=system,
                **kwargs,
            ),
        )
        elapsed = int((time.monotonic() - start) * 1000)

        if result is None:
            logger.warning(
                "llm_response_none",
                provider=self.config.provider.value,
                model=self.config.model,
            )
            result = ""

        logger.info(
            "llm_vision_response",
            provider=self.config.provider.value,
            model=self.config.model,
            image_size_bytes=len(image_data),
            response_length=len(result),
            latency_ms=elapsed,
        )
        self._log_exchange(f"[image:{len(image_data)}B] {prompt}", result, elapsed)
        return result

    # ── Tool Calling ─────────────────────────────────────

    @property
    def supports_tools(self) -> bool:
        """当前后端是否支持 tool calling"""
        return isinstance(self._backend, ToolCallingBackend)

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """异步 tool calling 单轮调用"""
        if not self.supports_tools:
            raise ToolCallError(f"后端 {self.config.provider} 不支持 tool calling")

        system = system or DEFAULT_SYSTEM
        start = time.monotonic()
        result = cast(
            dict[str, Any],
            await self._run_with_request_limit(
                lambda: self._backend.generate_with_tools(
                    messages=messages,
                    system=system,
                    tools=tools or [],
                    **kwargs,
                )
            ),
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
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """同步 tool calling 单轮调用"""
        if not self.supports_tools:
            raise ToolCallError(f"后端 {self.config.provider} 不支持 tool calling")

        system = system or DEFAULT_SYSTEM
        start = time.monotonic()
        result = cast(
            dict[str, Any],
            self._backend.generate_with_tools_sync(
                messages=messages,
                system=system,
                tools=tools or [],
                **kwargs,
            ),
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
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[ToolCall], ToolResult] | None = None,
        max_turns: int = 5,
        **kwargs: Any,
    ) -> list[ConversationMessage]:
        """异步多轮对话 + 本地 tool 执行循环

        每轮：LLM 响应 → 检查 tool_calls → 执行 → 回传结果 → 下一轮
        直到 LLM 不再调用工具或达到 max_turns。

        Returns:
            完整对话历史
        """
        system = system or DEFAULT_SYSTEM
        history: list[ConversationMessage] = []
        current_messages: list[dict[str, Any]] = list(messages)

        for turn in range(max_turns):
            result = await self.generate_with_tools(
                messages=current_messages,
                system=system,
                tools=tools,
                **kwargs,
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
            tool_content: list[dict[str, Any]] = []
            for tr in tool_results:
                tool_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.call_id,
                        "content": tr.output,
                        "is_error": tr.is_error,
                    }
                )
            current_messages.append({"role": "user", "content": tool_content})

            logger.info(
                "multiturn_round",
                turn=turn + 1,
                tool_calls=len(result["tool_calls"]),
            )

        return history

    def generate_multiturn_sync(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[ToolCall], ToolResult] | None = None,
        max_turns: int = 5,
        **kwargs: Any,
    ) -> list[ConversationMessage]:
        """同步多轮对话 + 本地 tool 执行循环"""
        system = system or DEFAULT_SYSTEM
        history: list[ConversationMessage] = []
        current_messages: list[dict[str, Any]] = list(messages)

        for turn in range(max_turns):
            result = self.generate_with_tools_sync(
                messages=current_messages,
                system=system,
                tools=tools,
                **kwargs,
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
            tool_content: list[dict[str, Any]] = []
            for tr in tool_results:
                tool_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.call_id,
                        "content": tr.output,
                        "is_error": tr.is_error,
                    }
                )
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
        from datetime import datetime
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

            # REC-27：截断 prompt/response（保留完整长度字段），避免 VLM 大 prompt 把
            # JSONL 撑爆；超过上限的整文留 prompt_length/response_length 即可。
            cap = _LOG_TEXT_CAP
            entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "call_count": self._call_count,
                "provider": self.config.provider.value,
                "model": self.config.model,
                "prompt_length": len(prompt),
                "response_length": len(response),
                "latency_ms": latency_ms,
                "prompt": prompt[:cap],
                "response": response[:cap],
                "truncated": len(prompt) > cap or len(response) > cap,
            }

            log_file = log_path / "llm_calls.jsonl"
            try:
                # REC-27：超过 _LOG_ROTATE_BYTES 轮转一次（.1），防单文件无限增长
                if log_file.exists() and log_file.stat().st_size > _LOG_ROTATE_BYTES:
                    log_file.replace(log_path / "llm_calls.jsonl.1")
                with log_file.open("a", encoding="utf-8") as f:
                    f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass
