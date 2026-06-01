"""轻量编号连续性核对 — 嵌入 pipeline 的召回兜底（#2 #8）

入库完成后，按 ``(subject, seq_chapter, seq_section)`` 分组检查 ``seq_index``
连续性，报告疑似漏号（如组内出现 3.1、3.3 却缺 3.2 → 3.2 很可能被漏提取）。

定位：不阻断入库，只产出可观测信号（日志 + checkpoint 下 audit_report.json），
供人工复核或后续重跑。依赖 Phase 0 的结构化序号 seq_*（由 number 自动派生）。
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# 单组内疑似漏号的最大跨度保护：跨度过大多半是编号体系切换（换章/换卷），不报
_MAX_GAP_SPAN = 50


def audit_numbering(rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """核对编号连续性。

    Args:
        rows: ``(subject, seq_chapter, seq_section, seq_index, number_canonical, kind, id)`` 列表

    Returns:
        ``{"groups": int, "suspected_gaps": [{"subject","chapter","section",
        "present":[...], "missing":[...]}]}``
    """
    groups: dict[tuple[Any, Any, Any], list[int]] = defaultdict(list)
    for subject, ch, sec, idx, _number, _kind, _oid in rows:
        if idx is None:
            continue
        groups[(subject, ch, sec)].append(int(idx))

    suspected: list[dict[str, Any]] = []
    for (subject, ch, sec), idx_list in groups.items():
        indices = sorted(set(idx_list))
        if len(indices) < 2:
            continue
        lo, hi = indices[0], indices[-1]
        if hi - lo > _MAX_GAP_SPAN:
            continue  # 跨度过大，疑似编号体系切换，跳过
        present = set(indices)
        missing = [i for i in range(lo, hi + 1) if i not in present]
        if missing:
            suspected.append(
                {
                    "subject": subject,
                    "chapter": ch,
                    "section": sec,
                    "present": indices,
                    "missing": missing,
                }
            )
    return {"groups": len(groups), "suspected_gaps": suspected}


def write_numbering_report(
    report: dict[str, Any], checkpoint_dir: Path | str, file_hash: str
) -> None:
    """把核对报告写到 ``<checkpoint>/<hash>/audit_report.json``。"""
    if not file_hash:
        return
    target_dir = Path(checkpoint_dir) / file_hash
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "audit_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("numbering_audit_write_failed", error=str(exc))
