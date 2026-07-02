"""WP-13 marker ledger 观测式接入——flag 双态、前缀剥离、只审已消费段、失败降级。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from sciforge.config.settings import AppConfig
from sciforge.core.models import Discipline, ExtractedBatch
from sciforge.pipeline.llm_schemas import SingleItemExtractLLM
from sciforge.pipeline.stages.stage1_extract import Stage1Extract


def _stage1(**config_overrides: object) -> Stage1Extract:
    config = AppConfig(**config_overrides)  # type: ignore[arg-type]
    return Stage1Extract(
        llm_client=None,  # type: ignore[arg-type]
        config=config,
        checkpoint_root=Path("/tmp/nonexistent"),
        discipline=Discipline.MATH,
    )


def _annotated(lines: list[str], base: int = 1) -> list[str]:
    return [f"{base + i:>7d} | {ln}" for i, ln in enumerate(lines)]


_LINES = [
    "定理 3.1 连续函数有界。",  # 1
    "证明：显然。",  # 2
    "习题 3.1 求极限。",  # 3（将被"漏提"）
    "",  # 4
    "定理 3.2 一致连续。",  # 5（未消费区）
]


def _item(start: int, end: int) -> SingleItemExtractLLM:
    return SingleItemExtractLLM(
        category="knowledge",
        item_type="theorem",
        number="3.1",
        corrected_content="连续函数有界。",
        corrected_proof="显然。",
        solution_status="complete",
        start_line=start,
        end_line=end,
    )


def _payload(items: list[SingleItemExtractLLM]) -> SimpleNamespace:
    return SimpleNamespace(items=items)


def test_flag_off_returns_none() -> None:
    stage = _stage1()
    assert stage.ledger_enabled is False
    result = stage._audit_consumed_window(_annotated(_LINES), 1, 4, _payload([_item(1, 2)]), [10])
    assert result is None


def test_flag_on_detects_missed_marker_with_prefix_stripping() -> None:
    stage = _stage1(marker_ledger_enabled=True)
    result = stage._audit_consumed_window(_annotated(_LINES), 1, 4, _payload([_item(1, 2)]), [10])
    assert result is not None
    missed_lines = [m["global_line"] for m in result["missed_markers"]]
    assert missed_lines == [3]  # 习题 3.1 漏提被查出
    # 只数已消费段：定理(1) + 证明(2) + 习题(3)；行 5 的定理 3.2 未消费不计
    assert result["markers_total"] == 3


def test_only_consumed_region_audited() -> None:
    """尾部延迟条目（未消费）不得计为 missed。"""
    stage = _stage1(marker_ledger_enabled=True)
    result = stage._audit_consumed_window(_annotated(_LINES), 1, 5, _payload([_item(1, 2)]), [10])
    assert result is not None
    missed = [m["global_line"] for m in result["missed_markers"]]
    assert 5 in missed  # 消费到第 5 行时，定理 3.2 才进入对账范围
    partial = stage._audit_consumed_window(_annotated(_LINES), 1, 4, _payload([_item(1, 2)]), [10])
    assert partial is not None
    assert 5 not in [m["global_line"] for m in partial["missed_markers"]]


def test_clean_window_reports_clean() -> None:
    stage = _stage1(marker_ledger_enabled=True)
    items = [_item(1, 2)]
    result = stage._audit_consumed_window(_annotated(_LINES[:2]), 1, 2, _payload(items), [7])
    assert result is not None
    assert result["missed_markers"] == []
    assert result["markers_covered"] == result["markers_total"]
    item_intervals = [c for c in result["coverage_intervals"] if c["kind"] == "item"]
    assert [c["item_ref"] for c in item_intervals] == ["7"]


def test_audit_failure_degrades_to_none() -> None:
    """观测层异常不阻断流水线：item_ids 与 items 数量不匹配 → None + warning。"""
    stage = _stage1(marker_ledger_enabled=True)
    result = stage._audit_consumed_window(
        _annotated(_LINES), 1, 4, _payload([_item(1, 2)]), [10, 11]
    )
    assert result is None


def test_extracted_batch_accepts_ledger_audit_default_none() -> None:
    batch = ExtractedBatch()
    assert batch.ledger_audit is None
    batch2 = ExtractedBatch(ledger_audit={"markers_total": 0})
    assert batch2.ledger_audit == {"markers_total": 0}


def test_strip_line_prefix() -> None:
    assert Stage1Extract._strip_line_prefix("     12 | 定理 3.1") == "定理 3.1"
    assert Stage1Extract._strip_line_prefix("无前缀正文 | 含竖线") == "无前缀正文 | 含竖线"
    assert Stage1Extract._strip_line_prefix("") == ""
