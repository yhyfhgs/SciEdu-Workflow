"""从 pipeline checkpoint 镜像读取提取结果（bench 的输入侧）。

Stage1 每条 item 镜像为 ``<checkpoint>/<file_hash>/items/stage1/item_<id>.json``
（``stage1_extract._mirror_batch``），bench 直接消费该镜像——离线、0 LLM、
不依赖 DB。字段视图保持最小：只取指标计算所需。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel, ConfigDict, Field

logger = structlog.get_logger()


class ExtractedRecord(BaseModel):
    """镜像 JSON 的最小视图（指标计算所需字段）。

    ``start_char``/``end_char`` 为 M1 char span 预留：镜像里没有时保持 None，
    指标退化为行级 IoU；落地后自动升级为字符级。
    """

    model_config = ConfigDict(extra="ignore")

    item_id: int
    category: str
    item_type: str
    number: str = ""
    solution_status: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    start_char: int | None = None
    end_char: int | None = None


def _record_from_snapshot(snapshot: dict[str, Any]) -> ExtractedRecord:
    item = dict(snapshot.get("item") or {})
    anchors = item.get("anchors") or {}
    return ExtractedRecord.model_validate(
        {
            "item_id": snapshot["item_id"],
            "category": item.get("category", ""),
            "item_type": item.get("item_type", ""),
            "number": item.get("number", ""),
            "solution_status": item.get("solution_status", ""),
            "start_line": item.get("start_line", 1),
            "end_line": item.get("end_line", 1),
            # M1 char span 落地后由 resolve_span 写回；当前镜像无此字段 → None
            "start_char": anchors.get("resolved_start_char"),
            "end_char": anchors.get("resolved_end_char"),
        }
    )


def load_extracted_records(
    checkpoint_dir: Path, *, stage_dir: str = "stage1"
) -> list[ExtractedRecord]:
    """读取 ``<checkpoint_dir>/items/<stage_dir>/item_*.json`` 全部镜像。

    ``checkpoint_dir`` 为单文件 checkpoint 根（``<checkpoint>/<file_hash>``）。
    单个损坏文件跳过并告警，不中断整个 bench。返回按 item_id 升序。
    """
    items_dir = checkpoint_dir / "items" / stage_dir
    records: list[ExtractedRecord] = []
    if not items_dir.is_dir():
        logger.warning("bench_items_dir_missing", dir=str(items_dir))
        return records
    for path in sorted(items_dir.glob("item_*.json")):
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            records.append(_record_from_snapshot(snapshot))
        except Exception as exc:  # noqa: BLE001
            logger.warning("bench_item_load_failed", file=str(path), error=str(exc))
    records.sort(key=lambda r: r.item_id)
    return records
