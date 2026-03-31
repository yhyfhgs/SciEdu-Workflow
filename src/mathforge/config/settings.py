"""应用全局配置"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Pipeline 配置
    max_concurrent_llm_calls: int = 3
    generation_max_rounds: int = 3
    generation_candidates_per_round: int = 3

    # PDF 批量处理
    pdf_batch_size: int = 20
    pdf_overlap_pages: int = 3
    pdf_max_span_pages: int = 5

    # 日志
    log_level: str = "INFO"
    log_format: str = "console"  # console | json

    model_config = SettingsConfigDict(
        env_prefix="MATHFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )
