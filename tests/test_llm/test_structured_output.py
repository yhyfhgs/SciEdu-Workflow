"""WP-41 to_strict_json_schema——Pydantic schema → provider strict 兼容形式。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sciforge.llm.structured_output import to_strict_json_schema


class _Nested(BaseModel):
    label: str = Field(min_length=1, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=5)


class _Sample(BaseModel):
    model_config = ConfigDict(extra="ignore")

    required_field: str
    optional_field: str = ""
    optional_int: int | None = None
    nested: _Nested


def test_all_fields_become_required() -> None:
    schema = to_strict_json_schema(_Sample)
    assert set(schema["required"]) == {"required_field", "optional_field", "optional_int", "nested"}


def test_additional_properties_false() -> None:
    schema = to_strict_json_schema(_Sample)
    assert schema["additionalProperties"] is False


def test_nested_def_also_strictified() -> None:
    schema = to_strict_json_schema(_Sample)
    nested_def = schema["$defs"]["_Nested"]
    assert set(nested_def["required"]) == {"label", "tags"}
    assert nested_def["additionalProperties"] is False


def test_forbidden_length_constraints_removed() -> None:
    schema = to_strict_json_schema(_Sample)
    nested_def = schema["$defs"]["_Nested"]
    label_schema = nested_def["properties"]["label"]
    assert "minLength" not in label_schema
    assert "maxLength" not in label_schema
    tags_schema = nested_def["properties"]["tags"]
    assert "maxItems" not in tags_schema


def test_field_semantics_unchanged() -> None:
    """转换只放宽 provider 侧校验，不改变字段类型/描述本身。"""
    schema = to_strict_json_schema(_Sample)
    assert schema["properties"]["required_field"]["type"] == "string"
    assert schema["properties"]["optional_int"]["anyOf"] == [
        {"type": "integer"},
        {"type": "null"},
    ]


def test_real_pipeline_schema_converts_without_error() -> None:
    """在实际 pipeline schema 上跑一遍，确认不抛异常且结构合理。"""
    from sciforge.pipeline.llm_schemas import BatchItemExtractPayloadLLM

    schema = to_strict_json_schema(BatchItemExtractPayloadLLM)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "items" in schema["required"]
