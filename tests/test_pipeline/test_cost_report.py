"""WP-45 成本仪表盘——JSONL 解析 + 按 stage 聚合 + cache 命中率。"""

from __future__ import annotations

import json
from pathlib import Path

from sciforge.pipeline.cost_report import aggregate_cost, parse_metrics_from_jsonl


def _write_jsonl(tmp_path: Path, lines: list[dict]) -> Path:
    path = tmp_path / "pipeline.log"
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8"
    )
    return path


def _metric(stage: str, **overrides: object) -> dict:
    base = {
        "event": "llm_call_metrics",
        "stage": stage,
        "attempt": 1,
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    base.update(overrides)
    return base


# ── parse_metrics_from_jsonl ──────────────────────────────


def test_parses_only_metric_events(tmp_path: Path) -> None:
    path = _write_jsonl(
        tmp_path,
        [
            _metric("stage1_extract"),
            {"event": "stage1_genre_detected", "genre": "book"},  # 非 metric 事件，应跳过
        ],
    )
    metrics = parse_metrics_from_jsonl(path)
    assert len(metrics) == 1
    assert metrics[0].stage == "stage1_extract"


def test_skips_malformed_json_lines(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.log"
    path.write_text(
        "not json at all\n" + json.dumps(_metric("stage1_extract"), ensure_ascii=False),
        encoding="utf-8",
    )
    metrics = parse_metrics_from_jsonl(path)
    assert len(metrics) == 1


def test_empty_file_returns_empty_list(tmp_path: Path) -> None:
    path = tmp_path / "empty.log"
    path.write_text("", encoding="utf-8")
    assert parse_metrics_from_jsonl(path) == []


def test_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.log"
    path.write_text(
        "\n\n" + json.dumps(_metric("stage1_review"), ensure_ascii=False) + "\n\n",
        encoding="utf-8",
    )
    assert len(parse_metrics_from_jsonl(path)) == 1


# ── aggregate_cost ────────────────────────────────────────


def test_aggregates_by_stage(tmp_path: Path) -> None:
    metrics = parse_metrics_from_jsonl(
        _write_jsonl(
            tmp_path,
            [
                _metric("stage1_extract", input_tokens=1000, output_tokens=200),
                _metric("stage1_extract", input_tokens=500, output_tokens=100),
                _metric("stage1_review", input_tokens=300, output_tokens=50),
            ],
        )
    )
    summary = aggregate_cost(metrics)
    assert summary.total_calls == 3
    stages = {s.stage: s for s in summary.by_stage}
    assert stages["stage1_extract"].calls == 2
    assert stages["stage1_extract"].input_tokens == 1500
    assert stages["stage1_review"].calls == 1


def test_total_tokens_summed_across_stages(tmp_path: Path) -> None:
    metrics = parse_metrics_from_jsonl(
        _write_jsonl(
            tmp_path,
            [
                _metric("a", input_tokens=100, output_tokens=10),
                _metric("b", input_tokens=200, output_tokens=20),
            ],
        )
    )
    summary = aggregate_cost(metrics)
    assert summary.total_input_tokens == 300
    assert summary.total_output_tokens == 30


def test_retry_attempts_counted() -> None:
    from sciforge.pipeline.cost_report import LlmCallMetric

    metrics = [
        LlmCallMetric(stage="x", attempt=1, input_tokens=1, output_tokens=1),
        LlmCallMetric(stage="x", attempt=2, input_tokens=1, output_tokens=1),
        LlmCallMetric(stage="x", attempt=3, input_tokens=1, output_tokens=1),
    ]
    summary = aggregate_cost(metrics)
    assert summary.by_stage[0].retry_attempts == 2  # attempt 2 和 3 都算 retry


def test_empty_metrics_list() -> None:
    summary = aggregate_cost([])
    assert summary.total_calls == 0
    assert summary.by_stage == []


# ── cache_hit_rate ────────────────────────────────────────


def test_cache_hit_rate_none_without_data() -> None:
    summary = aggregate_cost([])
    assert summary.cache_hit_rate is None


def test_cache_hit_rate_computed() -> None:
    from sciforge.pipeline.cost_report import LlmCallMetric

    metrics = [
        LlmCallMetric(stage="x", input_tokens=100, cache_read_input_tokens=900),
    ]
    summary = aggregate_cost(metrics)
    assert summary.cache_hit_rate == 0.9


def test_cache_hit_rate_zero_when_no_cache_used() -> None:
    from sciforge.pipeline.cost_report import LlmCallMetric

    metrics = [LlmCallMetric(stage="x", input_tokens=100, cache_read_input_tokens=0)]
    summary = aggregate_cost(metrics)
    assert summary.cache_hit_rate == 0.0
