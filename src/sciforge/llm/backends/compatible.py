"""OpenAI 兼容后端（中转平台、vLLM、Ollama 等）"""

from __future__ import annotations

from sciforge.llm.backends.openai_backend import OpenAIBackend
from sciforge.llm.config import LLMConfig


class OpenAICompatibleBackend(OpenAIBackend):
    """适用于 OpenAI 兼容 API 的后端

    与 OpenAIBackend 相同，但强制要求 base_url 且 api_key 可为空。
    """

    def __init__(self, config: LLMConfig):
        if not config.base_url:
            raise ValueError("openai_compatible 提供者必须设置 base_url")
        # 确保有 api_key（部分中转不需要）
        if not config.api_key:
            config = config.model_copy(update={"api_key": "dummy"})
        super().__init__(config)
