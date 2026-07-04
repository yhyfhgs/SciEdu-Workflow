"""v11 §G.3 结构化输出升级——Pydantic schema → provider strict JSON Schema。

背景（Docs/20 C1/C2/C5）：
- Anthropic 已 GA 原生 JSON Schema 输出（``output_config.format``，非
  ``response_format``——后者不在 capabilities 能力集，会被静默丢弃）
- OpenAI Structured Outputs 用 ``response_format.json_schema`` + ``strict:true``
- 两者都要求：**全字段进 required**（可选字段用 ``anyOf[...,null]`` 而非
  省略 required）+ ``additionalProperties:false``；DeepSeek strict 工具调用
  额外禁止 ``minItems/maxItems/minLength/maxLength``（C2）

Pydantic v2 的 ``model_json_schema()`` 默认只把无默认值的字段放进
``required``，且保留 min/maxLength 约束——本模块转换为跨 provider strict
兼容形式。转换是纯结构变换，不改变字段语义，只放宽 provider 侧的校验。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

# strict 模式下 DeepSeek 等网关禁止的约束关键字（C2）
_STRICT_FORBIDDEN_KEYS = ("minLength", "maxLength", "minItems", "maxItems")


def _strictify_node(node: dict[str, Any]) -> None:
    """就地转换单个 schema 节点为 strict 兼容形式（递归处理子节点）。"""
    for key in _STRICT_FORBIDDEN_KEYS:
        node.pop(key, None)

    if node.get("type") == "object" and isinstance(node.get("properties"), dict):
        node["required"] = list(node["properties"].keys())
        node["additionalProperties"] = False

    for value in node.values():
        if isinstance(value, dict):
            _strictify_node(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    _strictify_node(item)


def to_strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """把 Pydantic model 转成跨 provider strict 兼容的 JSON Schema。

    递归处理顶层 schema 与 ``$defs`` 中的全部嵌套模型定义。
    """
    schema = model.model_json_schema()
    _strictify_node(schema)
    defs = schema.get("$defs")
    if isinstance(defs, dict):
        for def_schema in defs.values():
            if isinstance(def_schema, dict):
                _strictify_node(def_schema)
    return schema
