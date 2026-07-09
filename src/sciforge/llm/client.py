"""统一 LLM 客户端"""

from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from datetime import UTC
from typing import TYPE_CHECKING, Any, TypeVar, cast

import structlog
from pydantic import BaseModel

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
        self._total_tokens: int = 0
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

    @property
    def call_count(self) -> int:
        """当前 client 实例已完成的模型调用数。"""
        return int(getattr(self, "_call_count", 0))

    @property
    def total_tokens(self) -> int:
        """当前 client 实例累计 token；provider 无 usage 时用字符数保守估算。"""
        return int(getattr(self, "_total_tokens", 0))

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

    def _default_json_mode(
        self, kwargs: dict[str, Any], schema_model: type[BaseModel] | None = None
    ) -> None:
        """REC-07：JSON 调用默认启用原生 JSON 模式，降低非法 JSON → 减少重试。

        只在调用方与 config 都未显式设置 response_format 时注入。能力矩阵会按 provider 过滤：
        OpenAI / Gemini / OpenAI 兼容保留生效；Anthropic / anthropic 兼容不支持 → 静默丢弃
        （故对生产兼容网关零影响，依旧靠 prompt + thinking 控制）。

        v11 §G.3（C1/C5，opt-in ``config.output_config_json_schema_enabled``）：
        提供 ``schema_model`` 且开关打开时，按 provider 下发**严格** JSON Schema
        而非笼统的 ``json_object``——Anthropic 走 GA 的 ``output_config.format``
        （非 ``response_format``，能力矩阵已放行）；OpenAI/OpenAI 兼容走
        ``response_format.json_schema`` + ``strict:true``。schema 经
        ``to_strict_json_schema`` 转换（全字段 required + additionalProperties:
        false，避开 min/maxLength）。默认关闭时行为与现状完全一致。
        """
        cfg_has = getattr(self.config, "response_format", None) is not None
        if "response_format" in kwargs or cfg_has:
            return

        use_strict_schema = schema_model is not None and bool(
            getattr(self.config, "output_config_json_schema_enabled", False)
        )
        if use_strict_schema:
            assert schema_model is not None  # for mypy；use_strict_schema 已保证
            from sciforge.llm.structured_output import to_strict_json_schema

            schema = to_strict_json_schema(schema_model)
            if self.config.provider in (LLMProvider.ANTHROPIC, LLMProvider.ANTHROPIC_COMPATIBLE):
                if "output_config" not in kwargs:
                    kwargs["output_config"] = {
                        "format": {
                            "type": "json_schema",
                            "schema": schema,
                            "name": schema_model.__name__,
                        }
                    }
                return
            if self.config.provider in (LLMProvider.OPENAI, LLMProvider.OPENAI_COMPATIBLE):
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_model.__name__,
                        "strict": True,
                        "schema": schema,
                    },
                }
                return

        if "response_format" not in kwargs:
            kwargs["response_format"] = {"type": "json_object"}

    async def generate_json(
        self,
        prompt: str,
        system: str | None = None,
        *,
        schema_model: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """异步生成并解析 JSON

        ``schema_model``：可选 Pydantic model，配合
        ``config.output_config_json_schema_enabled`` 下发严格 schema（§G.3）；
        不传或开关关闭时行为与现状一致。
        """
        system = system or JSON_SYSTEM
        self._default_json_mode(kwargs, schema_model)
        raw = await self.generate(prompt, system=system, **kwargs)
        return parse_llm_json(raw)

    def generate_json_sync(
        self,
        prompt: str,
        system: str | None = None,
        *,
        schema_model: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """同步生成并解析 JSON（``schema_model`` 语义同 :meth:`generate_json`）"""
        system = system or JSON_SYSTEM
        self._default_json_mode(kwargs, schema_model)
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
            reason = getattr(self._backend, "tool_calling_unavailable_reason", "")
            detail = f"：{reason}" if reason else ""
            raise ToolCallError(f"后端 {self.config.provider.value} 不支持 tool calling{detail}")

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
        import json as _json

        self._log_exchange(
            _json.dumps(messages, ensure_ascii=False, default=str),
            _json.dumps(result, ensure_ascii=False, default=str),
            elapsed,
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
            reason = getattr(self._backend, "tool_calling_unavailable_reason", "")
            detail = f"：{reason}" if reason else ""
            raise ToolCallError(f"后端 {self.config.provider.value} 不支持 tool calling{detail}")

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
        import json as _json

        self._log_exchange(
            _json.dumps(messages, ensure_ascii=False, default=str),
            _json.dumps(result, ensure_ascii=False, default=str),
            elapsed,
        )
        return result

    # ── Multi-turn with Tool Execution ───────────────────

    @staticmethod
    def _declared_tool_names(tools: list[dict[str, Any]]) -> set[str]:
        """兼容仓库中性格式与已包装的 OpenAI function 格式。"""
        names: set[str] = set()
        for tool in tools:
            name = tool.get("name")
            if isinstance(name, str) and name:
                names.add(name)
                continue
            function = tool.get("function")
            if isinstance(function, dict):
                wrapped_name = function.get("name")
                if isinstance(wrapped_name, str) and wrapped_name:
                    names.add(wrapped_name)
        return names

    @staticmethod
    def _decode_tool_calls(
        response: dict[str, Any],
        declared_names: set[str],
    ) -> list[tuple[ToolCall, ToolResult | None]]:
        """把统一 backend 响应变成可审计调用，并预判不可安全执行的请求。"""
        raw_calls = response.get("tool_calls", [])
        if not isinstance(raw_calls, list):
            raise ToolCallError("后端返回的 tool_calls 必须是列表")

        decoded: list[tuple[ToolCall, ToolResult | None]] = []
        for index, raw_call in enumerate(raw_calls):
            if not isinstance(raw_call, dict):
                raise ToolCallError(f"第 {index + 1} 个工具调用必须是对象")

            call_id = raw_call.get("id")
            if not isinstance(call_id, str) or not call_id:
                # 没有 call id 就无法构造任何 provider 可接受的配对结果。
                raise ToolCallError(f"第 {index + 1} 个工具调用缺少有效 id")

            raw_name = raw_call.get("name")
            name = raw_name if isinstance(raw_name, str) and raw_name else "<invalid-tool>"
            raw_arguments = raw_call.get("arguments", {})
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
            call = ToolCall(
                tool_name=name,
                arguments=cast(dict[str, object], arguments),
                call_id=call_id,
            )

            error: ToolResult | None = None
            arguments_error = raw_call.get("arguments_error")
            if name == "<invalid-tool>":
                error = ToolResult(
                    call_id=call_id,
                    output="工具调用无效：缺少有效工具名",
                    is_error=True,
                )
            elif name not in declared_names:
                error = ToolResult(
                    call_id=call_id,
                    output=f"未知工具：{name}",
                    is_error=True,
                )
            elif arguments_error:
                error = ToolResult(
                    call_id=call_id,
                    output=f"工具参数无效：{arguments_error}",
                    is_error=True,
                )
            elif not isinstance(raw_arguments, dict):
                error = ToolResult(
                    call_id=call_id,
                    output=(
                        "工具参数无效：arguments 必须是 JSON object，"
                        f"实际为 {type(raw_arguments).__name__}"
                    ),
                    is_error=True,
                )
            decoded.append((call, error))
        return decoded

    @staticmethod
    async def _execute_tool_async(
        call: ToolCall,
        preflight_error: ToolResult | None,
        executor: Callable[[ToolCall], ToolResult | Awaitable[ToolResult]] | None,
    ) -> ToolResult:
        if preflight_error is not None:
            return preflight_error
        if executor is None:
            return ToolResult(
                call_id=call.call_id,
                output="工具执行器未配置",
                is_error=True,
            )

        try:
            # 同步 sandbox / 数值工具不能阻塞 pipeline 的 event loop。多个独立
            # tool_calls 由外层 gather 并发等待，同时保留原调用顺序。
            result = await asyncio.to_thread(executor, call)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:  # noqa: BLE001 — 错误需回传给模型自修正
            return ToolResult(
                call_id=call.call_id,
                output=f"工具执行失败：{type(exc).__name__}: {exc}",
                is_error=True,
            )

        if not isinstance(result, ToolResult):
            return ToolResult(
                call_id=call.call_id,
                output=(f"工具执行器返回类型无效：应为 ToolResult，实际为 {type(result).__name__}"),
                is_error=True,
            )
        if result.call_id != call.call_id:
            # provider 只认模型给出的 id；执行器的业务输出保留，但不能让错误 id
            # 破坏整轮协议。
            result = result.model_copy(update={"call_id": call.call_id})
        return result

    @staticmethod
    def _execute_tool_sync(
        call: ToolCall,
        preflight_error: ToolResult | None,
        executor: Callable[[ToolCall], ToolResult] | None,
    ) -> ToolResult:
        if preflight_error is not None:
            return preflight_error
        if executor is None:
            return ToolResult(
                call_id=call.call_id,
                output="工具执行器未配置",
                is_error=True,
            )
        try:
            result = executor(call)
        except Exception as exc:  # noqa: BLE001 — 错误需回传给模型自修正
            return ToolResult(
                call_id=call.call_id,
                output=f"工具执行失败：{type(exc).__name__}: {exc}",
                is_error=True,
            )
        if not isinstance(result, ToolResult):
            return ToolResult(
                call_id=call.call_id,
                output=(f"工具执行器返回类型无效：应为 ToolResult，实际为 {type(result).__name__}"),
                is_error=True,
            )
        if result.call_id != call.call_id:
            result = result.model_copy(update={"call_id": call.call_id})
        return result

    def _append_provider_tool_round(
        self,
        current_messages: list[dict[str, Any]],
        response: dict[str, Any],
        tool_results: list[ToolResult],
    ) -> None:
        backend = cast(ToolCallingBackend, self._backend)
        round_messages = backend.build_tool_result_messages(response, tool_results)
        if not isinstance(round_messages, list) or not all(
            isinstance(message, dict) for message in round_messages
        ):
            raise ToolCallError("后端生成的工具结果消息必须是对象列表")
        current_messages.extend(round_messages)

    async def generate_multiturn(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[[ToolCall], ToolResult | Awaitable[ToolResult]] | None = None,
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
        tool_specs = tools or []
        declared_names = self._declared_tool_names(tool_specs)

        for turn in range(max_turns):
            result = await self.generate_with_tools(
                messages=current_messages,
                system=system,
                tools=tool_specs,
                **kwargs,
            )

            decoded_calls = self._decode_tool_calls(result, declared_names)
            content = result.get("content")
            assistant_content = content if isinstance(content, str) else ""

            # 记录 assistant 消息
            assistant_msg = ConversationMessage(
                role="assistant",
                content=assistant_content,
                tool_calls=[call for call, _ in decoded_calls],
            )
            history.append(assistant_msg)

            # 无工具调用，结束
            if not decoded_calls:
                break

            tool_results = list(
                await asyncio.gather(
                    *(
                        self._execute_tool_async(call, preflight_error, tool_executor)
                        for call, preflight_error in decoded_calls
                    )
                )
            )

            # 记录 tool 消息
            tool_msg = ConversationMessage(role="tool", tool_results=tool_results)
            history.append(tool_msg)

            # backend 负责保留原始 assistant 工具块并编码对应的结果消息。
            self._append_provider_tool_round(current_messages, result, tool_results)

            logger.info(
                "multiturn_round",
                turn=turn + 1,
                tool_calls=len(decoded_calls),
            )

            if turn + 1 == max_turns:
                logger.warning(
                    "multiturn_max_turns_reached",
                    max_turns=max_turns,
                    pending_tool_calls=len(decoded_calls),
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
        tool_specs = tools or []
        declared_names = self._declared_tool_names(tool_specs)

        for turn in range(max_turns):
            result = self.generate_with_tools_sync(
                messages=current_messages,
                system=system,
                tools=tool_specs,
                **kwargs,
            )

            decoded_calls = self._decode_tool_calls(result, declared_names)
            content = result.get("content")
            assistant_content = content if isinstance(content, str) else ""

            assistant_msg = ConversationMessage(
                role="assistant",
                content=assistant_content,
                tool_calls=[call for call, _ in decoded_calls],
            )
            history.append(assistant_msg)

            if not decoded_calls:
                break

            tool_results = [
                self._execute_tool_sync(call, preflight_error, tool_executor)
                for call, preflight_error in decoded_calls
            ]

            tool_msg = ConversationMessage(role="tool", tool_results=tool_results)
            history.append(tool_msg)

            self._append_provider_tool_round(current_messages, result, tool_results)

            logger.info(
                "multiturn_round",
                turn=turn + 1,
                tool_calls=len(decoded_calls),
            )

            if turn + 1 == max_turns:
                logger.warning(
                    "multiturn_max_turns_reached",
                    max_turns=max_turns,
                    pending_tool_calls=len(decoded_calls),
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

        self._call_count = int(getattr(self, "_call_count", 0)) + 1
        usage = self.last_usage
        measured_total = int(usage.get("total_tokens", 0))
        if measured_total <= 0:
            measured_total = sum(
                int(usage.get(key, 0))
                for key in (
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            )
        if measured_total <= 0:
            measured_total = max(1, (len(prompt) + len(response) + 3) // 4)
        self._total_tokens = int(getattr(self, "_total_tokens", 0)) + measured_total

        # 定期记录完整 prompt/response 到 DEBUG
        log_interval = max(1, int(getattr(self, "_log_interval", 10)))
        if self._call_count % log_interval == 0:
            logger.debug(
                "llm_full_exchange",
                call_count=self._call_count,
                prompt=prompt[:2000],
                response=response[:2000],
            )

        # 写 JSONL 文件
        log_dir = getattr(self, "_log_dir", None)
        if log_dir:
            log_path = Path(log_dir)
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
