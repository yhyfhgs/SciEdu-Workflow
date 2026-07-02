"""Golden benchmark ground-truth 数据模型（v11 §I.1）。

gt 锚定在 **pinned 的 Task0 markdown 输出**（``tests/golden/docs/`` 下的 fixture），
以 1-based 全局行号 + 行内 0-based codepoint 偏移定位——与 Stage1 ``_annotate``
的行号语义一致。bench 从 Stage1 起跑，避免 recognizer/VLM 输出漂移作废人工标注
（实施计划裁定 B）。

标注规范见 ``tests/golden/README.md``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SolutionStatus = Literal["complete", "no_solution_in_source", "solution_deferred"]


class GtMarker(BaseModel):
    """条目起始标记真值（标记台账的人工对照物）。

    一个 marker = 一处条目起始（定义/定理/例/习题…的标题行）。
    marker 粒度召回率 = 被某提取条目行区间覆盖的 marker 占比。
    """

    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1, description="pinned markdown 中的 1-based 全局行号")
    kind: str = Field(min_length=1, description="definition/theorem/exercise/... 条目类型")
    number: str = Field(default="", description="编号原文，如 '3.1'、'1'；无编号留空")


class GtItem(BaseModel):
    """单条真值条目：边界（行 + 可选行内字符偏移）、类型、题答配对。"""

    model_config = ConfigDict(extra="forbid")

    gt_id: str = Field(min_length=1, description="标注集内唯一 id，如 'thm-3.1'")
    category: Literal["knowledge", "question"]
    kind: str = Field(min_length=1, description="与 SingleItemExtractLLM.item_type 同语义")
    number: str = ""
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    start_char: int = Field(ge=0, default=0, description="start_line 行内 0-based 偏移")
    end_char: int | None = Field(
        default=None, ge=0, description="end_line 行内 exclusive 偏移；None = 行尾"
    )
    solution_status: SolutionStatus = "complete"
    crosses_window: bool = Field(
        default=False,
        description="默认窗口策略下该条目跨提取窗口（供跨窗合并率指标）",
    )
    note: str = ""

    @model_validator(mode="after")
    def _check_range(self) -> GtItem:
        if self.start_line > self.end_line:
            raise ValueError(f"start_line ({self.start_line}) > end_line ({self.end_line})")
        return self


class GoldenDoc(BaseModel):
    """一份 golden 文档的完整 ground truth。"""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(min_length=1)
    source_fixture: str = Field(
        min_length=1, description="相对 tests/golden/docs/ 的 pinned markdown 文件名"
    )
    genre: str = "unknown"
    discipline: str = "math"
    markers: list[GtMarker] = Field(default_factory=list)
    items: list[GtItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_gt_ids(self) -> GoldenDoc:
        seen: set[str] = set()
        for item in self.items:
            if item.gt_id in seen:
                raise ValueError(f"重复 gt_id: {item.gt_id!r}")
            seen.add(item.gt_id)
        return self


def load_golden_doc(path: Path) -> GoldenDoc:
    """读取一份 gt JSON（tests/golden/gt/<doc_id>.json）。"""
    return GoldenDoc.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_golden_dir(gt_dir: Path) -> list[GoldenDoc]:
    """读取 gt 目录下全部标注（按文件名排序，忽略非 .json）。"""
    return [load_golden_doc(p) for p in sorted(gt_dir.glob("*.json"))]
