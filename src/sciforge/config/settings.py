"""应用全局配置

v2 schema：单一 TOML 文件统一管理所有运行时配置
- [providers.*]  — 命名 LLM 连接池（type/api_key/base_url/default_model/...）
- [tasks.*]      — 任务级 LLM 配置（provider_ref + 覆写参数）
- [pipeline]     — 流水线参数（pages_per_batch / max_concurrent_llm_calls / ...）
- [logging]      — 日志参数（log_level / log_format / llm_log_dir / ...）
- default_provider_ref — 顶层字段，未指定 provider_ref 的 task 默认走此 provider

TOML 支持 `${ENV_VAR}` 占位符，从 os.environ 解析（递归处理嵌套 dict/list）。
启动时检测残留的 MATHFORGE_* 环境变量并打 deprecation warning。
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

import structlog
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sciforge.llm.config import ProviderConfig, TaskLLMConfig

logger = structlog.get_logger()

# 已知的任务名列表，用于环境变量解析 SCIFORGE_TASK_<TASK>_<FIELD>
KNOWN_TASKS: list[str] = [
    "generate",
    "solve",
    "solve_deep",
    "review",
    "annotate_review",
    "tag",
    "repair",
    "extract_and_classify",
    "improve",
    "numerical_verify",
    "vlm_convert",
    "split",
    "process_item",
    "infer_relations",
]

# TaskLLMConfig 中需要类型转换的字段
_FLOAT_FIELDS = {"temperature", "top_p", "frequency_penalty", "presence_penalty"}
_INT_FIELDS = {"max_tokens", "top_k", "seed", "timeout", "max_retries"}
_LIST_FIELDS = {"stop_sequences"}

# ${VAR_NAME} 占位符
_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def _coerce_field(field_name: str, value: str) -> Any:
    """将环境变量字符串值转换为 TaskLLMConfig 字段的正确类型"""
    if field_name in _FLOAT_FIELDS:
        return float(value)
    if field_name in _INT_FIELDS:
        return int(value)
    if field_name in _LIST_FIELDS:
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _interpolate_env(value: Any) -> Any:
    """递归把 ${VAR_NAME} 占位符从 os.environ 解析成实际值

    - str: 查找所有 ${VAR} 并替换；未定义的变量保持原样
    - dict / list: 递归处理元素
    - 其他类型: 原样返回
    """
    if isinstance(value, str):

        def _sub(m: re.Match[str]) -> str:
            var_name = m.group(1)
            return os.environ.get(var_name, m.group(0))

        return _ENV_VAR_PATTERN.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(item) for item in value]
    return value


class AppConfig(BaseSettings):
    """应用全局配置（v2 schema）"""

    # ── 存储路径 ────────────────────────────────────────
    data_dir: Path = Path("data")
    raw_data_dir: Path = Path("Raw Data")
    sqlite_db_path: Path = Path("data/sciforge.db")
    checkpoint_dir: Path = Path("data/checkpoint")

    # ── LLM v2 schema ───────────────────────────────────
    providers: dict[str, ProviderConfig] = {}
    task_llm: dict[str, TaskLLMConfig] = {}
    default_provider_ref: str | None = None
    llm_config_file: Path | None = None

    # ── Pipeline: Producer (Task0) — 文档识别 ───────────
    pages_per_batch: int = 5
    vlm_dpi: int = 200
    vlm_max_concurrent: int = 3
    vlm_max_retries: int = 3
    # 文本层探测（REC-01）：backend=vlm 时，若 PDF 已含可提取文字层（电子版教材）
    # 则自动降级走 0-LLM 的 PyMuPdfRecognizer，VLM 仅留给扫描件/图表密集件。
    # CLI --force-vlm 可关闭本探测，强制全页走 VLM（适合脏文字层的扫描件）。
    vlm_auto_text_detect: bool = True
    # 文字层判定阈值：抽样页平均字符数 ≥ 此值且非乱码 → 认为有可用文字层。
    vlm_text_layer_min_chars_per_page: int = 100
    # 文字层探测抽样页数（从首页起）。
    vlm_text_layer_sample_pages: int = 5

    # ── Pipeline: Stage1Extract — 单条滑动窗口 ──────────
    extract_window_lines: int = 500  # 初始窗口行数（旧默认 200）
    extract_max_window_lines: int = 3000  # 最大窗口行数（扩展上限，旧 1000）
    extract_window_expand_step: int = 500  # 每次扩窗口的步长
    extract_empty_force_limit: int = 2  # 达到 max 后连续 N 次空提取触发 force consume
    extract_retry_base_delay: float = 2.0
    max_extract_retries: int = 3  # 老别名（保留兼容）
    stage1_extract_max_retries: int = 3

    # ── Pipeline: Stage1Review / Stage2Review 开关 ──────
    stage1_review_enabled: bool = True
    # REC-05：默认关闭 Stage2Review。它逐条 LLM 复核的学科根/tag 复用/taxonomy 合法性
    # 已由 Stage3 确定性规则（_ensure_discipline_root + canonicalize_path + validate）
    # 完整覆盖，重叠且结论会与规则抖动。关闭后这一轮 LLM 调用归零（一本书约省 N 次），
    # 标准化全交给 Stage3 确定性后处理。需要 LLM 二次复核时显式置 true 重新启用（opt-in）。
    stage2_review_enabled: bool = False
    stage1_review_max_retries: int = 2
    stage2_annotate_max_retries: int = 3
    stage2_review_max_retries: int = 2
    # 老别名（与新开关 AND 生效，用于兼容既有 TOML）
    extract_review_enabled: bool = True
    extract_stage1_review_enabled: bool = True
    annotate_stage2_review_enabled: bool = True
    task2_max_retries: int = 3

    # ── retry with feedback ─────────────────────────────
    retry_feedback_enabled: bool = True

    # ── Pipeline: 各 stage 间 queue 大小 ─────────────────
    pipeline_stage_queue_size: int = 8  # REC-03：加深以平滑 LLM 延迟抖动（原 2 易阻塞上游）
    pipeline_annotate_queue_size: int = 4  # 保留兼容老 TOML

    # Tag 树注入到 prompt 的限制
    tag_tree_max_depth: int = 4
    tag_tree_max_nodes: int = 200
    # REC-09：Stage2 读 tag_tree 只读快照；live 较上次快照增长 ≥ 此值才刷新快照。
    # 越大越省（Stage2 序列化缓存更稳、prompt 前缀更稳），代价是 tag 复用提示更滞后。
    tag_tree_snapshot_refresh_gap: int = 16

    # ── Pipeline: Debug ──────────────────────────────────
    pipeline_debug: bool = False

    # ── Pipeline: Producer-Consumer 通信 ────────────────
    pipeline_queue_size: int = 4
    checkpoint_flush_interval: float = 3.0

    # ── Pipeline: 目录模式 ─────────────────────────────
    # 并发建议：≤16GB RAM → 4-6；32GB → 8-12；64GB+ → 16-24
    # 过高会挤占 WASM 工具（VS Code SQLite 插件等）的内存分配
    max_concurrent_files: int = 8
    # REC-03：≥ Stage2 并发总和 + 其余 stage，否则并发被全局闸门钳死（原 5 不够 4 路打标）
    max_concurrent_llm_calls: int = 8

    # ── Pipeline: per-model 并发限流 ────────────────────
    # 防止被 provider 按 model 维度限流（如 Anthropic Token Plan 的 429）。
    # key 为 LLM model id（与 TaskLLMConfig.model 或 Provider.default_model 对齐），
    # value 为该 model 允许的最大并发在途请求数。未列出的 model 仅受全局
    # max_concurrent_llm_calls 约束。
    # TOML 示例：
    #   [pipeline.llm_model_concurrency]
    #   "claude-sonnet-4-20250514" = 2
    #   "anthropic/claude-sonnet-4" = 3
    llm_model_concurrency: dict[str, int] = {}

    # ── 进度守护 ────────────────────────────────────────
    progress_log_interval: int = 15

    # ── 日志 ────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "console"  # console | json
    llm_log_interval: int = 10
    llm_log_dir: Path = Path("data/llm_logs")

    # ── 生成引擎参数 ────────────────────────────────────
    generation_max_rounds: int = 3
    generation_candidates_per_round: int = 3
    qgen_retriever_max_kps: int = 8
    qgen_retriever_max_refs: int = 8
    qgen_retriever_max_negatives: int = 5
    qgen_lightweight_prefilter: bool = True
    # Phase 6c: verifier 在 verdict=PASS 时顺带返回 10 维难度评分，
    # 省一次独立的 annotator LLM 调用。默认开启（review.txt 已支持新格式）。
    qgen_merge_difficulty_into_verify: bool = True
    pipeline_parallel: bool = False
    evolution_enabled: bool = False
    evolution_max_improvement_rounds: int = 2
    solver_max_turns: int = 5
    solver_enable_tools: bool = True
    sandbox_timeout: int = 10
    enable_numerical_verification: bool = True

    # ── Pipeline 优化开关（Phase 2.5 / 4 / 5）──────────────
    empty_window_fast_skip: bool = True
    # 结构感知 fast-skip（默认 True，高召回）：无强标记时还须无弱信号（裸编号/选项/
    # 解答/章节/填空）才跳窗 → 杜绝静默丢条目（#8）。设 False 退回仅强标记预检。
    extract_fast_skip_strict: bool = True
    review_conditional_enabled: bool = True
    # 跨章节题答回填（#3）：入库后把章末/附录答案并回题干 solution_content
    answer_linking_enabled: bool = True
    # 结构化关系推断（#6 知识图谱）：入库后推断 corollary_of/tests/successor 边写 item_relations
    relation_inference_enabled: bool = True
    # 文档级 reconciliation 复核（#7）：入库后检测重复条目/类型误判，写报告
    reconciliation_enabled: bool = True
    # REC-15：reconciliation 高置信结论自动纠正回改 DB（仅 kp_type_mismatch，首标记强规则）。
    # 默认 False 保守（只报告）；category 跨表迁移/近重复合并仍只报告不自动改。
    reconciliation_autofix: bool = False
    # 单文档内并行提取（#1 Phase 6）：按 section 分段并发提取 + 段序保序。默认关（流式串行）。
    intra_doc_parallel_extract: bool = False
    intra_doc_segment_concurrency: int = 4
    # 过小 section 合并阈值（行数）：相邻小段前向累积到 ≥ 此值才闭合，避免碎片化。
    intra_doc_min_segment_lines: int = 40
    # Prompt caching（#9）：各 stage 把静态指令放 system（稳定可缓存前缀），动态输入放 user。
    # 默认 True；设 False 退回"全部塞 user message"的合并路径（应急回滚用）。
    pipeline_prompt_cache_enabled: bool = True
    # REC-03：默认开 Stage2 并发（REC-09 只读快照已消除共享 tag_tree 竞争前提）。
    # ReorderBuffer 保证并发后按 item_id 保序入库；受 max_concurrent_llm_calls 全局闸门兜底。
    stage2_annotate_concurrency: int = 4
    stage2_review_concurrency: int = 2
    # REC-04：Stage2Annotate 批量打标（同来源 batch 的 N 条合 1 次 LLM 调用，单文档最大
    # 调用压降点 800→~180）。1:1 长度校验 + 单条失败降级逐条；关闭则退回逐条打标。
    stage2_annotate_batch: bool = True
    stage2_annotate_batch_size: int = 8  # 单次批量打标最多合并多少条 item
    # REC-10：Stage3 攒小批再一次 save_batch+commit（WAL fsync N→N/批），去重计数批量预查。
    stage3_persist_batch_size: int = 16

    # ── Pipeline v11 开关（全部默认关 = 行为与 v10 完全一致）──
    # §E.0（M1）：format/genre profile——extended 体裁探测（含 problem_set）+
    # 密度自适应窗口 + halo/carry 先验。关闭时体裁探测走 REC-14 白名单路径。
    format_profile_enabled: bool = False
    # III.1（M1）：marker ledger 覆盖率审计——对每个已消费窗口段做标记台账对账，
    # 只写 structlog 指标 + ExtractedBatch.ledger_audit 附件，**不改任何提取行为**。
    # M1 使命：在真实文档上度量 T4 真值质量（误报/漏报率），M4 才注入 harness prompt。
    marker_ledger_enabled: bool = False

    # ── 429 / 速率限制处理 ────────────────────────────────
    # 遇 LLMRateLimitError 时的基础睡眠秒数（指数退避起点）
    # minimax 5h/4500 次场景建议 300s；其他 provider 可调小
    rate_limit_base_sleep: float = 300.0
    # 单次 429 最长睡眠秒数（上限，避免 Retry-After 给出过大值）
    rate_limit_max_sleep: float = 1800.0
    # 单个 LLM 请求累计等 429 的总预算秒数（超过放弃）
    # 6 小时足以覆盖 minimax 的 5h 配额窗口
    rate_limit_total_budget: float = 21600.0

    model_config = SettingsConfigDict(
        env_prefix="SCIFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @model_validator(mode="after")
    def _post_init(self) -> AppConfig:
        """启动时依次执行：加载 dotenv → 加载 TOML → 加载 env overrides → 检测 legacy 前缀"""
        from dotenv import load_dotenv

        load_dotenv(override=False)
        self._load_from_toml()
        self._load_from_env()
        self._warn_legacy_env_vars()
        return self

    # ── TOML 加载 ─────────────────────────────────────────

    def _load_from_toml(self) -> None:
        """加载 v2 schema TOML 配置

        段落：
        - [providers.*]  → self.providers
        - [tasks.*]      → self.task_llm
        - [pipeline]     → setattr 到同名字段
        - [logging]      → setattr 到同名字段
        - default_provider_ref 顶层字段 → self.default_provider_ref
        """
        if not self.llm_config_file or not self.llm_config_file.exists():
            return

        with open(self.llm_config_file, "rb") as f:
            data = tomllib.load(f)

        # [providers.*]
        providers_section = data.get("providers", {})
        for name, cfg in providers_section.items():
            if not isinstance(cfg, dict):
                continue
            interpolated = _interpolate_env(cfg)
            try:
                self.providers[name] = ProviderConfig(**interpolated)
            except Exception as exc:
                logger.warning("invalid_provider_config", name=name, error=str(exc))

        # default_provider_ref (顶层)
        if "default_provider_ref" in data and self.default_provider_ref is None:
            self.default_provider_ref = data["default_provider_ref"]

        # [tasks.*]
        tasks_section = data.get("tasks", {})
        for task_name, task_cfg in tasks_section.items():
            if not isinstance(task_cfg, dict):
                continue
            interpolated = _interpolate_env(task_cfg)
            try:
                if task_name not in self.task_llm:
                    self.task_llm[task_name] = TaskLLMConfig(**interpolated)
                else:
                    # 已有条目（如构造时传入），仅填充 None 字段
                    existing = self.task_llm[task_name]
                    for key, value in interpolated.items():
                        if hasattr(existing, key) and getattr(existing, key) is None:
                            setattr(existing, key, value)
            except Exception as exc:
                logger.warning("invalid_task_config", task=task_name, error=str(exc))

        # [pipeline]
        pipeline_section = data.get("pipeline", {})
        self._apply_section(pipeline_section, "pipeline")

        # [logging]
        logging_section = data.get("logging", {})
        self._apply_section(logging_section, "logging")

        logger.debug(
            "toml_config_loaded",
            file=str(self.llm_config_file),
            providers=list(self.providers.keys()),
            tasks=list(self.task_llm.keys()),
        )

    def _apply_section(self, section: dict[str, Any], section_name: str) -> None:
        """把一个 TOML 段落的 key=value 对 setattr 到 AppConfig 同名字段上

        仅当 AppConfig 定义了该字段时才赋值；未知字段记 warning。
        Path 类字段自动从 str 转 Path。
        """
        for key, value in section.items():
            if key not in type(self).model_fields:
                logger.warning(
                    "unknown_config_field",
                    section=section_name,
                    field=key,
                )
                continue
            field_info = type(self).model_fields[key]
            # 自动把 str 转 Path（若字段类型是 Path）
            if field_info.annotation is Path and isinstance(value, str):
                value = Path(value)
            try:
                setattr(self, key, value)
            except Exception as exc:
                logger.warning(
                    "invalid_section_field",
                    section=section_name,
                    field=key,
                    error=str(exc),
                )

    # ── 环境变量覆盖（高优先级）──────────────────────────

    def _load_from_env(self) -> None:
        """从 SCIFORGE_TASK_{TASK}_{FIELD} 环境变量加载（优先于 TOML）"""
        prefix = "SCIFORGE_TASK_"

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

    # ── Legacy 前缀检测 ───────────────────────────────────

    def _warn_legacy_env_vars(self) -> None:
        """检测 MATHFORGE_* 环境变量残留并打 deprecation warning"""
        legacy_vars = sorted(key for key in os.environ if key.startswith("MATHFORGE_"))
        if legacy_vars:
            logger.warning(
                "deprecated_env_prefix",
                message=(
                    "检测到 MATHFORGE_* 环境变量，这些变量已被废弃且不会被加载。"
                    "请改用 SCIFORGE_* 前缀或对应的标准 vendor 变量 "
                    "(ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY / "
                    "OPENROUTER_API_KEY 等)。"
                ),
                legacy_vars=legacy_vars,
            )
