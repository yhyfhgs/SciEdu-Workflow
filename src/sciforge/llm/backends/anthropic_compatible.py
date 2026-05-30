"""Anthropic Messages API 兼容后端

适用于以 Anthropic /v1/messages 格式暴露的中转/代理网关，例如：
- Z.AI: https://api.z.ai/api/coding/paas/v4/anthropic
- DeepSeek Anthropic 兼容端点
- Moonshot (Kimi) Anthropic 兼容端点
- OpenRouter 的 /v1/messages 路由
- 任何自建的 anthropic 风格反代

行为与 AnthropicBackend 完全一致（Vision / Tool Calling / cache_control 都继承），
区别仅在于：
1. 强制要求 config.base_url（否则抛 ValueError）
2. 允许 api_key 为空，用 "dummy" 填充（部分本地/私有部署不鉴权）

不支持的能力（thinking / metadata / cache_control / service_tier）
由 PROVIDER_CAPABILITIES[ANTHROPIC_COMPATIBLE] 能力矩阵在 _resolve_params 阶段静默过滤，
无需在本类中再处理。
"""

from __future__ import annotations

from sciforge.llm.backends.anthropic import AnthropicBackend
from sciforge.llm.config import LLMConfig


class AnthropicCompatibleBackend(AnthropicBackend):
    """Anthropic Messages API 兼容后端"""

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            raise ValueError("anthropic_compatible 提供者必须设置 base_url")
        if not config.api_key:
            config = config.model_copy(update={"api_key": "dummy"})
        super().__init__(config)
