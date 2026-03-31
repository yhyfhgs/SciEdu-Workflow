"""
MathForge LLM 客户端 — 统一接口，支持 Claude / OpenAI
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional

try:
    import anthropic
except ImportError:
    anthropic = None

try:
    import openai
except ImportError:
    openai = None


@dataclass
class LLMConfig:
    provider: str = "anthropic"          # "anthropic" or "openai"
    model: str = "claude-sonnet-4-20250514"
    temperature: float = 0.7
    max_tokens: int = 4096
    api_key: Optional[str] = None

    # 预设配置
    PRESETS = {
        "generate": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "temperature": 0.7,
            "max_tokens": 4096,
        },
        "solve": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",  # 生产环境可用 opus
            "temperature": 0.0,
            "max_tokens": 8192,
        },
        "review": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "temperature": 0.0,
            "max_tokens": 2048,
        },
        "tag": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-20250514",
            "temperature": 0.0,
            "max_tokens": 1024,
        },
    }

    @classmethod
    def for_task(cls, task: str) -> LLMConfig:
        preset = cls.PRESETS.get(task, cls.PRESETS["generate"])
        return cls(**preset)


class LLMClient:
    """统一 LLM 调用接口"""

    def __init__(self, config: LLMConfig):
        self.config = config

        if config.provider == "anthropic":
            if anthropic is None:
                raise ImportError("pip install anthropic")
            key = config.api_key or os.environ.get("ANTHROPIC_API_KEY")
            self.client = anthropic.Anthropic(api_key=key)
        elif config.provider == "openai":
            if openai is None:
                raise ImportError("pip install openai")
            key = config.api_key or os.environ.get("OPENAI_API_KEY")
            self.client = openai.OpenAI(api_key=key)
        else:
            raise ValueError(f"Unknown provider: {config.provider}")

    def generate(
        self,
        prompt: str,
        system: str = "你是一位资深数学教育专家。",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """发送请求并返回纯文本响应"""
        temp = temperature if temperature is not None else self.config.temperature
        tokens = max_tokens or self.config.max_tokens

        if self.config.provider == "anthropic":
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=tokens,
                temperature=temp,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text

        elif self.config.provider == "openai":
            response = self.client.chat.completions.create(
                model=self.config.model,
                max_tokens=tokens,
                temperature=temp,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            )
            return response.choices[0].message.content

    def generate_json(
        self,
        prompt: str,
        system: str = "你是一位资深数学教育专家。请严格按 JSON 格式输出，不要包含 markdown 代码块。",
        **kwargs,
    ) -> dict:
        """发送请求并解析 JSON 响应"""
        raw = self.generate(prompt, system=system, **kwargs)
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(text: str) -> dict:
        """鲁棒的 JSON 解析：处理 markdown 包裹和常见错误"""
        # 去掉 markdown 代码块
        text = re.sub(r'```(?:json)?\s*', '', text).strip()
        text = text.rstrip('`').strip()

        # 尝试提取 JSON 对象
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        # 最后尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse LLM response as JSON: {e}\nRaw: {text[:500]}")
