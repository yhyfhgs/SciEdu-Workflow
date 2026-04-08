"""PipelineMetrics — 管线执行指标收集

各阶段记录指标，支持 structlog 输出和 JSON 持久化。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import structlog

logger = structlog.get_logger()


@dataclass
class StageMetric:
    """单阶段指标"""

    items_in: int = 0
    items_out: int = 0
    items_skipped: int = 0
    items_quarantined: int = 0
    errors: int = 0
    elapsed_sec: float = 0.0


@dataclass
class PipelineMetrics:
    """管线执行指标收集器"""

    # 各阶段指标
    extraction: StageMetric = field(default_factory=StageMetric)
    processing: StageMetric = field(default_factory=StageMetric)
    unification: StageMetric = field(default_factory=StageMetric)
    relations: StageMetric = field(default_factory=StageMetric)
    storage: StageMetric = field(default_factory=StageMetric)

    # 提取策略分布
    extraction_strategies: dict[str, int] = field(default_factory=dict)

    # 质量分布
    quality_distribution: dict[str, int] = field(default_factory=dict)

    # 去重统计
    duplicates_found: int = 0
    duplicates_exact: int = 0
    duplicates_near: int = 0

    # 时间
    start_time: float = 0.0
    end_time: float = 0.0

    def start(self) -> None:
        self.start_time = time.monotonic()

    def finish(self) -> None:
        self.end_time = time.monotonic()

    def record_extraction(self, strategy: str, items: int, score: float) -> None:
        """记录提取结果"""
        self.extraction.items_out += items
        self.extraction_strategies[strategy] = self.extraction_strategies.get(strategy, 0) + 1

    def record_quality(self, grade: str) -> None:
        """记录质量等级"""
        self.quality_distribution[grade] = self.quality_distribution.get(grade, 0) + 1

    def record_gate_decision(self, action: str) -> None:
        """记录门控决策"""
        if action == "quarantine":
            self.processing.items_quarantined += 1
        elif action == "skip":
            self.processing.items_skipped += 1

    def record_dedup(self, is_exact: bool) -> None:
        """记录去重"""
        self.duplicates_found += 1
        if is_exact:
            self.duplicates_exact += 1
        else:
            self.duplicates_near += 1

    @property
    def total_elapsed(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.monotonic() - self.start_time

    def summary(self) -> dict:
        """聚合指标摘要"""
        return {
            "total_elapsed_sec": round(self.total_elapsed, 2),
            "extraction": {
                "items": self.extraction.items_out,
                "strategies": self.extraction_strategies,
            },
            "processing": {
                "quarantined": self.processing.items_quarantined,
                "skipped": self.processing.items_skipped,
            },
            "quality": self.quality_distribution,
            "dedup": {
                "total": self.duplicates_found,
                "exact": self.duplicates_exact,
                "near": self.duplicates_near,
            },
            "storage": {
                "items_stored": self.storage.items_out,
                "errors": self.storage.errors,
            },
        }

    def log_summary(self) -> None:
        """输出指标摘要到 structlog"""
        logger.info("pipeline_metrics", **self.summary())

    def to_json(self) -> str:
        """完整指标 JSON"""
        return json.dumps(self.summary(), ensure_ascii=False, indent=2)

    def save(self, path: Path) -> None:
        """持久化到文件"""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
