"""LLM 响应 JSON 鲁棒解析"""

from __future__ import annotations

import json
import re

from mathforge.core.exceptions import LLMParseError


def parse_llm_json(text: str) -> dict:
    """鲁棒的 JSON 解析，处理 LLM 输出的各种格式问题

    4 阶段:
    1. 去除 Markdown 代码块包裹
    2. 直接解析
    3. 正则提取 JSON 对象
    4. 修复常见错误（尾逗号、单引号）
    """
    # Stage 1: 去除 markdown 代码块
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()
    cleaned = cleaned.rstrip("`").strip()

    # Stage 2: 直接解析
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # Stage 3: 提取 JSON 对象
    match = re.search(r"\{[\s\S]*\}", cleaned)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Stage 4: 修复常见错误
    fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)  # 尾逗号
    fixed = fixed.replace("'", '"')  # 单引号
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 最后对提取的 JSON 对象也做修复尝试
    if match:
        fixed_match = re.sub(r",\s*([}\]])", r"\1", match.group())
        fixed_match = fixed_match.replace("'", '"')
        try:
            return json.loads(fixed_match)
        except json.JSONDecodeError:
            pass

    raise LLMParseError(raw_response=text, parse_error="所有解析策略均失败")
