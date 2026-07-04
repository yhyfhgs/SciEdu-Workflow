"""WP-45 成本仪表盘——聚合 pipeline 运行日志里的 ``llm_call_metrics`` 事件。

数据源：structlog JSON Lines 日志（``[logging] log_format = "json"``）。
``retry.call_llm_json_with_schema_retry_with_feedback`` 每次成功调用都会打
一条 ``llm_call_metrics`` 事件（含 stage/token 用量/cache 命中），本模块
纯离线解析聚合，不需要额外埋点。
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_METRIC_EVENT = "llm_call_metrics"


class LlmCallMetric(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stage: str = ""
    attempt: int = 1
    prompt_chars: int = 0
    output_chars: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    rate_limit_hits: int = 0


class StageCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    retry_attempts: int = 0  # attempt > 1 的调用次数


class CostSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_creation_tokens: int = 0
    by_stage: list[StageCost] = Field(default_factory=list)

    @property
    def cache_hit_rate(self) -> float | None:
        """cache_read / (cache_read + input_tokens 中非命中部分的估算)。

        近似口径：cache_read_input_tokens / (cache_read_input_tokens +
        total_input_tokens)——input_tokens 通常已不含 cache 命中部分
        （由 Anthropic usage 语义决定），两者相加近似为"总输入需求量"。
        无 token 数据时返回 None（而非误导性的 0）。
        """
        denom = self.total_cache_read_tokens + self.total_input_tokens
        if denom == 0:
            return None
        return self.total_cache_read_tokens / denom


def parse_metrics_from_jsonl(path: Path) -> list[LlmCallMetric]:
    """解析 JSON Lines 日志，提取全部 ``llm_call_metrics`` 事件。

    非 JSON 行（如 console 格式日志的普通文本行）静默跳过，不报错——
    允许日志文件混杂少量非结构化内容。
    """
    metrics: list[LlmCallMetric] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or data.get("event") != _METRIC_EVENT:
            continue
        metrics.append(LlmCallMetric.model_validate(data))
    return metrics


def aggregate_cost(metrics: list[LlmCallMetric]) -> CostSummary:
    """按 stage 分组聚合 token 用量与 retry 次数。"""
    by_stage: dict[str, StageCost] = {}
    for m in metrics:
        stage_cost = by_stage.setdefault(m.stage, StageCost(stage=m.stage))
        stage_cost.calls += 1
        stage_cost.input_tokens += m.input_tokens or 0
        stage_cost.output_tokens += m.output_tokens or 0
        stage_cost.cache_read_tokens += m.cache_read_input_tokens or 0
        stage_cost.cache_creation_tokens += m.cache_creation_input_tokens or 0
        if m.attempt > 1:
            stage_cost.retry_attempts += 1

    return CostSummary(
        total_calls=len(metrics),
        total_input_tokens=sum(s.input_tokens for s in by_stage.values()),
        total_output_tokens=sum(s.output_tokens for s in by_stage.values()),
        total_cache_read_tokens=sum(s.cache_read_tokens for s in by_stage.values()),
        total_cache_creation_tokens=sum(s.cache_creation_tokens for s in by_stage.values()),
        by_stage=sorted(by_stage.values(), key=lambda s: s.stage),
    )
