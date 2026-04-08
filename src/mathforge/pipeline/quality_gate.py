"""QualityGate — 可配置的管线阶段间质量门控

在 MetaExtractor → ItemProcessor → Storage 之间拦截低质量条目。
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import BaseModel

from mathforge.core.models import ExtractionReport

logger = structlog.get_logger()


class QualityGateConfig(BaseModel):
    """质量门控阈值配置"""

    min_extraction_coverage: float = 0.3
    min_extraction_items: int = 1
    max_quality_grade_for_store: str = "D"  # "F" 级别进隔离区
    min_completeness_for_store: float = 0.3
    enable_quarantine: bool = True


class GateDecision(BaseModel):
    """质量门控判定结果"""

    action: Literal["proceed", "retry", "quarantine", "skip"] = "proceed"
    reason: str = ""


# 质量等级排序
_GRADE_ORDER = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


class QualityGate:
    """管线阶段间质量门控"""

    def __init__(self, config: QualityGateConfig | None = None) -> None:
        self.config = config or QualityGateConfig()

    def check_extraction(self, report: ExtractionReport) -> GateDecision:
        """MetaExtractor 后：检查提取质量"""
        if report.items_found < self.config.min_extraction_items:
            return GateDecision(
                action="retry",
                reason=f"提取条目数 {report.items_found} < 阈值 {self.config.min_extraction_items}",
            )
        if report.coverage_ratio < self.config.min_extraction_coverage:
            return GateDecision(
                action="retry",
                reason=(
                    f"覆盖率 {report.coverage_ratio:.2f} "
                    f"< 阈值 {self.config.min_extraction_coverage}"
                ),
            )
        return GateDecision(action="proceed")

    def check_processing(self, quality_grade: str, completeness: float) -> GateDecision:
        """ItemProcessor 后：检查校准质量"""
        max_grade = self.config.max_quality_grade_for_store
        # 规范化 grade 为单字母（处理 LLM 返回的 "B+"、"A-" 等变体）
        normalized_grade = quality_grade[0].upper() if quality_grade else "F"
        if normalized_grade not in _GRADE_ORDER:
            logger.warning(
                "unknown_quality_grade",
                grade=quality_grade,
            )
            normalized_grade = "F"
        if _GRADE_ORDER[normalized_grade] < _GRADE_ORDER.get(max_grade, 0):
            if self.config.enable_quarantine:
                return GateDecision(
                    action="quarantine",
                    reason=f"质量等级 {quality_grade} 低于阈值 {max_grade}",
                )
            return GateDecision(
                action="skip",
                reason=f"质量等级 {quality_grade} 低于阈值 {max_grade}",
            )
        if completeness < self.config.min_completeness_for_store:
            return GateDecision(
                action="quarantine",
                reason=f"完整度 {completeness:.2f} < 阈值 {self.config.min_completeness_for_store}",
            )
        return GateDecision(action="proceed")
