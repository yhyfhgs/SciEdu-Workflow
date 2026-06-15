"""Anthropic (Claude) 后端"""

from __future__ import annotations

import os
from typing import Any, cast

import structlog

from sciforge.llm.backends.base import BaseBackend
from sciforge.llm.config import LLMConfig, LLMProvider

logger = structlog.get_logger()

# system prompt 自动缓存阈值（字符）。低于此不主动缓存（Anthropic 对 <1024 token 块
# 本就忽略 cache_control）；中文指令很密，>500 字基本已超最小可缓存粒度。
_AUTO_CACHE_MIN_CHARS = 500

# REC-07：检测到 max_tokens 截断时，把 max_tokens 翻倍重抓一次的上限（避免静默丢尾部条目）。
# 仅在原 max_tokens 有余量时触发；上限保守，避免超过模型输出上限触发 4xx。
_TRUNCATION_REFETCH_CEILING = 32768


class AnthropicBackend(BaseBackend):
    """Anthropic Claude API 后端，支持同步和异步"""

    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError("需要安装 anthropic SDK: pip install anthropic") from exc

        api_key = config.api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.client = anthropic.Anthropic(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        self.async_client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )

    @staticmethod
    def _extract_text(response: Any) -> str:
        """从响应中提取文本，跳过 thinking block"""
        for block in response.content:
            if getattr(block, "type", None) == "text" and block.text:
                return cast(str, block.text)
        return cast(str, response.content[0].text or "")

    # ── 参数映射 ──────────────────────────────────────────

    def _to_sdk_params(self, neutral: dict[str, Any]) -> dict[str, Any]:
        """中性字段 → Anthropic Messages API kwargs

        - cache_control 必须注入到 content block 里（不是 top-level kwarg），这里剔除。
        - thinking 经 ``extra_body`` 透传，而非 top-level kwarg：老版 anthropic SDK 的
          ``messages.create()`` 没有 ``thinking`` 形参会直接 TypeError；且 anthropic 兼容
          网关（MiniMax / Kimi 等）本就从请求 body 读 ``thinking``。extra_body 是 SDK 官方
          逃生通道，版本无关。典型用途：``thinking={"type":"disabled"}`` 关闭 MiniMax-M3 推理。
        """
        params = {k: v for k, v in neutral.items() if k != "cache_control"}
        thinking = params.pop("thinking", None)
        if thinking is not None:
            extra = dict(params.get("extra_body") or {})
            extra.setdefault("thinking", thinking)
            params["extra_body"] = extra
        return params

    def _system_param(self, system: str) -> Any:
        """把 system prompt 包成**可缓存 content block**（prompt caching 的正确落点）。

        修复 D11：旧实现把 cache_control 注入到动态 user message 末尾（永不命中）；
        正确做法是缓存**稳定的 system 前缀**。pipeline 各 stage 把 ~4k-10k 字静态指令
        放 system → 同一 (discipline, stage) 的每次调用都命中缓存，省 ~90% 输入 token。

        触发条件：纯 Anthropic provider 且 system 够长（自动），或显式 config.cache_control。
        兼容代理默认不缓存（其能力矩阵排除 cache_control），除非显式配置。
        """
        if not system:
            return system
        # REC-08：纯 Anthropic 自动缓存；anthropic_compatible 需 supports_prompt_cache 显式放行。
        cacheable_provider = self.config.provider == LLMProvider.ANTHROPIC or (
            self.config.provider == LLMProvider.ANTHROPIC_COMPATIBLE
            and getattr(self.config, "supports_prompt_cache", False)
        )
        auto = cacheable_provider and len(system) >= _AUTO_CACHE_MIN_CHARS
        if not (self.config.cache_control or auto):
            return system
        spec = self.config.cache_control or {"type": "ephemeral"}
        return [{"type": "text", "text": system, "cache_control": spec}]

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, int]:
        """读取真实 token usage（REC-08）：含 cache 命中量，供 llm_call_metrics 验证缓存收益。"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return {}
        out: dict[str, int] = {}
        for field in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ):
            val = getattr(usage, field, None)
            if isinstance(val, int):
                out[field] = val
        return out

    def _maybe_inject_cache_control(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """（仅 vision 路径用）若 config.cache_control 非空，注入到最后一个 user message 末尾块。

        文本生成路径已改用 ``_system_param`` 缓存 system 前缀，不再走此方法。
        """
        if not self.config.cache_control:
            return messages

        cache_spec = self.config.cache_control
        out = [dict(m) for m in messages]
        if not out:
            return out
        last = out[-1]
        content = last.get("content")
        if isinstance(content, str):
            last["content"] = [{"type": "text", "text": content, "cache_control": cache_spec}]
        elif isinstance(content, list) and content:
            tail = content[-1]
            if isinstance(tail, dict):
                tail["cache_control"] = cache_spec
        return out

    # ── 基础文本生成 ──────────────────────────────────────

    async def generate(self, prompt: str, system: str, **kwargs: Any) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = [{"role": "user", "content": prompt}]
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                system=cast(Any, self._system_param(system)),
                messages=cast(Any, messages),
                **sdk_params,
            )
            # REC-07：max_tokens 截断 → 有余量时翻倍重抓一次（避免静默丢尾部条目/坏 JSON）
            if getattr(response, "stop_reason", None) == "max_tokens":
                cur = int(sdk_params.get("max_tokens", self.config.max_tokens))
                bumped = min(cur * 2, _TRUNCATION_REFETCH_CEILING)
                if bumped > cur:
                    logger.warning(
                        "anthropic_truncation_refetch",
                        old_max_tokens=cur,
                        new_max_tokens=bumped,
                        model=self.config.model,
                    )
                    response = await self.async_client.messages.create(
                        model=self.config.model,
                        system=cast(Any, self._system_param(system)),
                        messages=cast(Any, messages),
                        **{**sdk_params, "max_tokens": bumped},
                    )
            self.last_usage = self._extract_usage(response)
            return self._extract_text(response)
        except Exception as e:
            self._raise_mapped(e)

    def generate_sync(self, prompt: str, system: str, **kwargs: Any) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = [{"role": "user", "content": prompt}]
        try:
            response = self.client.messages.create(
                model=self.config.model,
                system=cast(Any, self._system_param(system)),
                messages=cast(Any, messages),
                **sdk_params,
            )
            self.last_usage = self._extract_usage(response)
            return self._extract_text(response)
        except Exception as e:
            self._raise_mapped(e)

    # ── Vision (多模态) ───────────────────────────────────

    def _make_image_block(self, image_data: bytes, media_type: str) -> dict[str, Any]:
        b64 = self._encode_image(image_data)
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        }

    def _build_vision_message(
        self, prompt: str, image_data: bytes, image_media_type: str
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    self._make_image_block(image_data, image_media_type),
                    {"type": "text", "text": prompt},
                ],
            }
        ]

    def _build_multi_vision_message(
        self, prompt: str, images: list[bytes], image_media_type: str
    ) -> list[dict[str, Any]]:
        content: list[dict[str, Any]] = [
            self._make_image_block(img, image_media_type) for img in images
        ]
        content.append({"type": "text", "text": prompt})
        return [{"role": "user", "content": content}]

    async def generate_vision(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = self._maybe_inject_cache_control(
            self._build_vision_message(prompt, image_data, image_media_type)
        )
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                **sdk_params,
            )
            return self._extract_text(response)
        except Exception as e:
            self._raise_mapped(e)

    async def generate_vision_multi(
        self,
        prompt: str,
        images: list[bytes],
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = self._maybe_inject_cache_control(
            self._build_multi_vision_message(prompt, images, image_media_type)
        )
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                **sdk_params,
            )
            return self._extract_text(response)
        except Exception as e:
            self._raise_mapped(e)

    def generate_vision_sync(
        self,
        prompt: str,
        image_data: bytes,
        image_media_type: str,
        system: str,
        **kwargs: Any,
    ) -> str:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        messages = self._maybe_inject_cache_control(
            self._build_vision_message(prompt, image_data, image_media_type)
        )
        try:
            response = self.client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                **sdk_params,
            )
            return self._extract_text(response)
        except Exception as e:
            self._raise_mapped(e)

    # ── Tool Calling ─────────────────────────────────────

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        try:
            response = await self.async_client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                tools=cast(Any, tools),
                **sdk_params,
            )
            return self._parse_tool_response(response)
        except Exception as e:
            self._raise_mapped(e)

    def generate_with_tools_sync(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        sdk_params = self._to_sdk_params(self._resolve_params(**kwargs))
        try:
            response = self.client.messages.create(
                model=self.config.model,
                system=system,
                messages=cast(Any, messages),
                tools=cast(Any, tools),
                **sdk_params,
            )
            return self._parse_tool_response(response)
        except Exception as e:
            self._raise_mapped(e)

    @staticmethod
    def _parse_tool_response(response: Any) -> dict[str, Any]:
        """将 Anthropic 响应解析为统一格式"""
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    {
                        "id": block.id,
                        "name": block.name,
                        "arguments": block.input,
                    }
                )
        return {
            "content": content,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
            "raw_content": response.content,
        }

    # ── Message Batches API（REC-23：异步批处理，约半价）────────────────
    # 适合**非交互式批处理**（如 parse run-book / 目录批量重标）：把彼此独立的调用
    # 收集成一个 batch 一次提交、轮询取回。流式 Producer-Consumer 主路径不走这里。

    def _batches_api(self) -> Any:
        batches = getattr(getattr(self.async_client, "messages", None), "batches", None)
        if batches is None:
            raise RuntimeError(
                "当前 anthropic SDK 不支持 Message Batches API（请升级 anthropic SDK）"
            )
        return batches

    async def submit_message_batch(self, requests: list[dict[str, Any]]) -> str:
        """提交一批独立请求，返回 batch_id。

        requests 每项：``{"custom_id": str, "prompt": str, "system"?: str, "max_tokens"?: int}``。
        """
        sdk_params = self._to_sdk_params(self._resolve_params())
        body: list[dict[str, Any]] = []
        for r in requests:
            params: dict[str, Any] = dict(sdk_params)
            params["model"] = self.config.model
            params["max_tokens"] = int(
                r.get("max_tokens") or sdk_params.get("max_tokens") or self.config.max_tokens
            )
            params["messages"] = [{"role": "user", "content": r["prompt"]}]
            if r.get("system"):
                params["system"] = r["system"]
            body.append({"custom_id": r["custom_id"], "params": params})
        batch = await self._batches_api().create(requests=body)
        return str(batch.id)

    async def get_message_batch_status(self, batch_id: str) -> str:
        """返回 batch 处理状态（in_progress / ended / ...）。"""
        batch = await self._batches_api().retrieve(batch_id)
        return str(getattr(batch, "processing_status", "unknown"))

    async def fetch_message_batch_results(self, batch_id: str) -> dict[str, str]:
        """取回结果：``{custom_id: text}``。失败/拒答条目映射为空串。"""
        out: dict[str, str] = {}
        async for entry in await self._batches_api().results(batch_id):
            cid = str(getattr(entry, "custom_id", ""))
            result = getattr(entry, "result", None)
            text = ""
            if result is not None and getattr(result, "type", None) == "succeeded":
                msg = getattr(result, "message", None)
                blocks = getattr(msg, "content", []) or []
                text = "".join(
                    getattr(b, "text", "") for b in blocks if getattr(b, "type", None) == "text"
                )
            out[cid] = text
        return out
