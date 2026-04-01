"""应用全局配置"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import structlog
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from mathforge.llm.config import TaskLLMConfig

logger = structlog.get_logger()

# 已知的任务名列表，用于环境变量解析
KNOWN_TASKS: list[str] = [
    "generate",
    "solve",
    "solve_deep",
    "review",
    "tag",
    "repair",
    "split",
    "process_item",
    "infer_relations",
    "improve",
    "numerical_verify",
]

# TaskLLMConfig 中需要类型转换的字段
_FLOAT_FIELDS = {"temperature"}
_INT_FIELDS = {"max_tokens", "timeout", "max_retries"}


def _coerce_field(field_name: str, value: str) -> Any:
    """将环境变量字符串值转换为 TaskLLMConfig 字段的正确类型"""
    if field_name in _FLOAT_FIELDS:
        return float(value)
    if field_name in _INT_FIELDS:
        return int(value)
    return value


class AppConfig(BaseSettings):
    """应用全局配置，支持环境变量和 .env 文件"""

    # 存储路径
    data_dir: Path = Path("data")
    raw_data_dir: Path = Path("Raw Data")

    # LLM 配置
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-20250514"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    llm_base_url: str | None = None
    llm_reasoning_effort: str | None = None

    # API Keys
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None

    # Per-task LLM 配置
    task_llm: dict[str, TaskLLMConfig] = {}
    llm_config_file: Path | None = None

    # Pipeline 配置
    max_concurrent_llm_calls: int = 3
    generation_max_rounds: int = 3
    generation_candidates_per_round: int = 3

    # PDF 批量处理
    pdf_batch_size: int = 20
    pdf_overlap_pages: int = 3
    pdf_max_span_pages: int = 5

    # 流水线 Pipeline
    pipeline_queue_size: int = 10
    pipeline_split_batch_lines: int = 150

    # LLM 日志
    llm_log_interval: int = 10
    llm_log_dir: Path = Path("data/llm_logs")

    # Checkpoint
    checkpoint_dir: Path = Path("data/checkpoint")

    # 生成流水线增强
    pipeline_parallel: bool = False
    evolution_enabled: bool = False
    evolution_max_improvement_rounds: int = 2
    solver_max_turns: int = 5
    solver_enable_tools: bool = True
    sandbox_timeout: int = 10
    enable_numerical_verification: bool = True

    # 日志
    log_level: str = "INFO"
    log_format: str = "console"  # console | json

    model_config = SettingsConfigDict(
        env_prefix="MATHFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @model_validator(mode="after")
    def _load_task_llm_config(self) -> AppConfig:
        """从 TOML 文件和 MATHFORGE_TASK_* 环境变量加载 per-task LLM 配置"""
        self._load_from_toml()
        self._load_from_env()
        return self

    def _load_from_toml(self) -> None:
        """从 TOML 配置文件加载 per-task LLM 配置"""
        if not self.llm_config_file or not self.llm_config_file.exists():
            return

        with open(self.llm_config_file, "rb") as f:
            data = tomllib.load(f)

        llm_section = data.get("llm", {})

        # [llm] 全局字段作为 fallback（低于环境变量，仅覆盖默认值）
        _toml_globals = {
            "provider": "llm_provider",
            "model": "llm_model",
            "base_url": "llm_base_url",
            "reasoning_effort": "llm_reasoning_effort",
        }
        for toml_key, attr_name in _toml_globals.items():
            if toml_key in llm_section:
                # 仅当环境变量未显式设置时才使用 TOML 值
                env_key = f"MATHFORGE_{attr_name.upper()}"
                if env_key not in os.environ:
                    setattr(self, attr_name, llm_section[toml_key])

        # [llm.tasks.*] per-task 覆写
        tasks_section = llm_section.get("tasks", {})
        for task_name, task_cfg in tasks_section.items():
            if not isinstance(task_cfg, dict):
                continue
            if task_name not in self.task_llm:
                self.task_llm[task_name] = TaskLLMConfig(**task_cfg)
            else:
                # 已有条目（例如从构造参数传入），仅填充 None 字段
                existing = self.task_llm[task_name]
                for k, v in task_cfg.items():
                    if hasattr(existing, k) and getattr(existing, k) is None:
                        setattr(existing, k, v)

        logger.debug(
            "toml_config_loaded",
            file=str(self.llm_config_file),
            tasks=list(tasks_section.keys()),
        )

    def _load_from_env(self) -> None:
        """从 MATHFORGE_TASK_{TASK}_{FIELD} 环境变量加载（优先于 TOML）"""
        prefix = "MATHFORGE_TASK_"

        for key, value in os.environ.items():
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :].lower()

            # 找到最长匹配的已知 task name
            matched_task: str | None = None
            for task_name in KNOWN_TASKS:
                normalized = task_name.replace("-", "_")
                if rest.startswith(normalized + "_"):
                    if matched_task is None or len(task_name) > len(matched_task):
                        matched_task = task_name

            if not matched_task:
                continue

            field_name = rest[len(matched_task.replace("-", "_")) + 1 :]
            if not field_name:
                continue

            if matched_task not in self.task_llm:
                self.task_llm[matched_task] = TaskLLMConfig()

            task_cfg = self.task_llm[matched_task]
            if hasattr(task_cfg, field_name):
                try:
                    setattr(task_cfg, field_name, _coerce_field(field_name, value))
                except (ValueError, TypeError):
                    logger.warning(
                        "invalid_task_env_var",
                        key=key,
                        value=value,
                        field=field_name,
                    )
