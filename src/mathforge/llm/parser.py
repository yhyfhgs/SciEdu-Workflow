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

    # Stage 3: 提取 JSON 对象（用平衡括号匹配代替贪婪正则）
    match = _extract_json_object(cleaned)
    if match:
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            pass

    # Stage 4: 修复常见错误
    fixed = re.sub(r",\s*([}\]])", r"\1", cleaned)  # 尾逗号
    # 安全替换单引号：只替换 JSON 结构中的单引号键/值定界符
    fixed = _safe_replace_quotes(fixed)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # 最后对提取的 JSON 对象也做修复尝试
    if match:
        fixed_match = re.sub(r",\s*([}\]])", r"\1", match)
        fixed_match = _safe_replace_quotes(fixed_match)
        try:
            return json.loads(fixed_match)
        except json.JSONDecodeError:
            pass

    # Stage 5: 截断 JSON 修复 — LLM 输出被 max_tokens 截断时
    truncated = _repair_truncated_json(cleaned)
    if truncated:
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            pass

    raise LLMParseError(raw_response=text, parse_error="所有解析策略均失败")


def _extract_json_object(text: str) -> str | None:
    """用平衡括号匹配提取第一个完整 JSON 对象"""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def _repair_truncated_json(text: str) -> str | None:
    """修复被截断的 JSON — 关闭未闭合的字符串、数组、对象

    当 LLM 输出因 max_tokens 被截断时，JSON 通常缺少尾部闭合。
    策略：找到最后一个完整的数组元素，截断其后内容，补齐闭合符号。
    """
    start = text.find("{")
    if start == -1:
        return None

    # 尝试在截断位置补齐闭合
    fragment = text[start:]

    # 去掉尾部的不完整 value（从最后一个完整的 }, 或 ] 处截断）
    # 找最后一个完整的 JSON 值结束位置
    last_complete = -1
    for pattern in ["},", "}]", '"},', '"]', "null,", "true,", "false,"]:
        pos = fragment.rfind(pattern)
        if pos > last_complete:
            last_complete = pos + len(pattern) - 1  # 保留逗号前的字符

    if last_complete <= 0:
        return None

    truncated = fragment[: last_complete + 1]

    # 去掉尾部逗号
    truncated = truncated.rstrip().rstrip(",")

    # 计算需要补齐的闭合符号
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in truncated:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()

    # 闭合
    truncated += "".join(reversed(stack))

    return truncated


def _safe_replace_quotes(text: str) -> str:
    """安全替换 JSON 结构中的单引号

    只替换明确是键/值定界符的单引号，不替换内容中的撇号。
    """
    # 尝试用正则匹配 JSON 键值对中的单引号
    result = re.sub(r"'(\w+)'\s*:", r'"\1":', text)
    result = re.sub(r":\s*'([^']*)'", r': "\1"', result)
    return result
