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
    多重策略：
    1. 找到最后一个完整的 JSON 对象/数组元素边界
    2. 截断不完整部分
    3. 补齐所有未闭合的括号
    """
    start = text.find("{")
    if start == -1:
        return None

    fragment = text[start:]

    # 策略 A: 找最后一个完整的数组元素边界
    # 搜索多种完整元素结束模式
    candidates: list[int] = []
    for pattern in [
        "},", "}]", '"},', '"]', "null,", "true,", "false,",
        '"}\n', '}\n', ']\n',  # 换行分隔
        "null}", "null]",
    ]:
        pos = fragment.rfind(pattern)
        if pos > 0:
            # 保留到 pattern 的结束字符（但不含尾部逗号/换行）
            end = pos + len(pattern)
            candidates.append(end)

    # 策略 B: 如果截断在字符串中间，找最后一个完整的 key-value 对
    # 匹配 "key": "value" 或 "key": number 的结束位置
    for m in re.finditer(r'"[^"]*"\s*:\s*(?:"[^"]*"|[\d.]+|null|true|false)\s*[,}\]]', fragment):
        candidates.append(m.end())

    if not candidates:
        return None

    # 取最靠后的完整位置
    best = max(candidates)
    truncated = fragment[:best]

    # 去掉尾部逗号和空白
    truncated = truncated.rstrip().rstrip(",").rstrip()

    # 补齐闭合括号
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
