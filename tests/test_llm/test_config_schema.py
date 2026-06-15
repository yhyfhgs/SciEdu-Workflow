"""Schema 基础单元测试：扩展字段 + ProviderConfig + 能力矩阵 + from_app_config"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sciforge.llm.capabilities import (
    ALL_OPTIONAL_PARAMS,
    PROVIDER_CAPABILITIES,
    filter_params,
)
from sciforge.llm.config import (
    LLMConfig,
    LLMProvider,
    ProviderConfig,
    TaskLLMConfig,
)

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig


# ── LLMConfig 扩展字段 ─────────────────────────────────────


class TestLLMConfigExtendedFields:
    def test_all_new_fields_default_none(self):
        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test")
        for fld in (
            "top_p",
            "top_k",
            "stop_sequences",
            "seed",
            "response_format",
            "frequency_penalty",
            "presence_penalty",
            "thinking",
            "reasoning_effort",
            "service_tier",
            "metadata",
            "cache_control",
            "extra_body",
        ):
            assert getattr(cfg, fld) is None

    def test_fields_accept_values(self):
        cfg = LLMConfig(
            provider=LLMProvider.ANTHROPIC,
            api_key="test",
            top_p=0.9,
            top_k=40,
            stop_sequences=["<END>"],
            thinking={"type": "enabled", "budget_tokens": 16384},
            extra_body={"provider": {"order": ["z-ai"]}},
        )
        assert cfg.top_p == 0.9
        assert cfg.top_k == 40
        assert cfg.stop_sequences == ["<END>"]
        assert cfg.thinking == {"type": "enabled", "budget_tokens": 16384}
        assert cfg.extra_body == {"provider": {"order": ["z-ai"]}}

    def test_anthropic_compatible_enum_exists(self):
        assert LLMProvider.ANTHROPIC_COMPATIBLE.value == "anthropic_compatible"

    def test_max_tokens_upper_bound_raised_to_1m(self):
        # 65536 → 200_000 → 1_000_000（覆盖 MiniMax-M3 512K 输出区间）
        cfg = LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test", max_tokens=512_000)
        assert cfg.max_tokens == 512_000

    def test_max_tokens_rejects_above_1m(self):
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LLMConfig(provider=LLMProvider.ANTHROPIC, api_key="test", max_tokens=2_000_000)


# ── ProviderConfig ────────────────────────────────────────


class TestProviderConfig:
    def test_minimal_construction(self):
        pc = ProviderConfig(type=LLMProvider.ANTHROPIC)
        assert pc.type == LLMProvider.ANTHROPIC
        assert pc.api_key is None
        assert pc.base_url is None
        assert pc.timeout == 120
        assert pc.max_retries == 3

    def test_full_construction(self):
        pc = ProviderConfig(
            type=LLMProvider.OPENAI_COMPATIBLE,
            api_key="sk-or-xyz",
            base_url="https://openrouter.ai/api/v1",
            default_model="anthropic/claude-sonnet-4",
            timeout=180,
            max_retries=5,
            temperature=0.5,
            max_tokens=32768,
        )
        assert pc.base_url == "https://openrouter.ai/api/v1"
        assert pc.default_model == "anthropic/claude-sonnet-4"
        assert pc.temperature == 0.5

    def test_type_coerces_from_string(self):
        pc = ProviderConfig(type="anthropic_compatible")  # type: ignore[arg-type]
        assert pc.type == LLMProvider.ANTHROPIC_COMPATIBLE


# ── TaskLLMConfig 扩展字段 ─────────────────────────────────


class TestTaskLLMConfig:
    def test_provider_ref_field_exists(self):
        t = TaskLLMConfig(provider_ref="openrouter")
        assert t.provider_ref == "openrouter"

    def test_all_extended_params_optional(self):
        t = TaskLLMConfig()
        for fld in ALL_OPTIONAL_PARAMS:
            assert getattr(t, fld, None) is None

    def test_accepts_extended_params(self):
        t = TaskLLMConfig(
            provider_ref="openai_official",
            model="gpt-4o",
            seed=42,
            response_format={"type": "json_object"},
            frequency_penalty=0.1,
        )
        assert t.seed == 42
        assert t.response_format == {"type": "json_object"}


# ── 能力矩阵 ─────────────────────────────────────────────


class TestCapabilityMatrix:
    def test_all_providers_covered(self):
        for provider in LLMProvider:
            assert provider in PROVIDER_CAPABILITIES, f"{provider} 未在能力矩阵中"

    def test_temperature_and_max_tokens_universal(self):
        """所有 provider 都必须支持 temperature 和 max_tokens"""
        for caps in PROVIDER_CAPABILITIES.values():
            assert "temperature" in caps
            assert "max_tokens" in caps

    def test_filter_drops_unsupported(self):
        # Gemini 不支持 frequency_penalty
        accepted, dropped = filter_params(
            LLMProvider.GEMINI,
            {"temperature": 0.5, "frequency_penalty": 0.1},
        )
        assert accepted == {"temperature": 0.5}
        assert dropped == ["frequency_penalty"]

    def test_filter_keeps_supported(self):
        # Anthropic 支持 top_k
        accepted, dropped = filter_params(
            LLMProvider.ANTHROPIC,
            {"temperature": 0.5, "top_k": 40, "thinking": {"type": "enabled"}},
        )
        assert accepted["top_k"] == 40
        assert accepted["thinking"] == {"type": "enabled"}
        assert dropped == []

    def test_filter_ignores_none(self):
        accepted, dropped = filter_params(
            LLMProvider.OPENAI,
            {"temperature": 0.5, "top_p": None, "seed": None},
        )
        assert accepted == {"temperature": 0.5}
        assert dropped == []

    def test_anthropic_compatible_caps(self):
        """anthropic 兼容网关：放行 thinking + cache_control（REC-08，opt-in），仍不支持 metadata"""
        caps = PROVIDER_CAPABILITIES[LLMProvider.ANTHROPIC_COMPATIBLE]
        assert "thinking" in caps  # 现放行（经 extra_body 透传）
        assert "metadata" not in caps
        assert "cache_control" in caps  # REC-08：放行（是否生效由 supports_prompt_cache 决定）
        # 基础参数
        assert "top_p" in caps
        assert "extra_body" in caps

    def test_openai_compatible_excludes_service_tier(self):
        """service_tier 是 OpenAI 官方独有（不走中转）"""
        caps = PROVIDER_CAPABILITIES[LLMProvider.OPENAI_COMPATIBLE]
        assert "service_tier" not in caps

    def test_gemini_no_tool_calling_params(self):
        caps = PROVIDER_CAPABILITIES[LLMProvider.GEMINI]
        assert "frequency_penalty" not in caps
        assert "reasoning_effort" not in caps


# ── from_app_config 解析链 ─────────────────────────────────


def _make_app(**kwargs: Any) -> AppConfig:
    """构造一个不加载 TOML 的 AppConfig"""
    from sciforge.config.settings import AppConfig

    return AppConfig(llm_config_file=None, **kwargs)


class TestFromAppConfigV2:
    def test_no_providers_fallback_to_defaults(self):
        """无 providers + 无 overrides → 使用 LLMConfig 默认值"""
        app = _make_app()
        cfg = LLMConfig.from_app_config(app, "generate")
        assert cfg.provider == LLMProvider.ANTHROPIC
        assert cfg.model == "claude-sonnet-4-20250514"
        assert cfg.temperature == 0.7  # from PRESETS["generate"]

    def test_cli_override_provider_fallback_path(self):
        """无 providers 时 CLI --llm-provider=openai 直接生效"""
        app = _make_app()
        cfg = LLMConfig.from_app_config(app, "generate", provider="openai")
        assert cfg.provider == LLMProvider.OPENAI

    def test_provider_ref_resolves_base_url(self):
        """task_cfg.provider_ref 指向 providers dict，base_url 被继承"""
        app = _make_app(
            providers={
                "openrouter": ProviderConfig(
                    type=LLMProvider.OPENAI_COMPATIBLE,
                    api_key="sk-or-test",
                    base_url="https://openrouter.ai/api/v1",
                    default_model="anthropic/claude-sonnet-4",
                )
            },
            task_llm={"generate": TaskLLMConfig(provider_ref="openrouter")},
        )
        cfg = LLMConfig.from_app_config(app, "generate")
        assert cfg.provider == LLMProvider.OPENAI_COMPATIBLE
        assert cfg.base_url == "https://openrouter.ai/api/v1"
        assert cfg.api_key == "sk-or-test"
        assert cfg.model == "anthropic/claude-sonnet-4"

    def test_default_provider_ref_fallback(self):
        """无 task.provider_ref 时走 default_provider_ref"""
        app = _make_app(
            providers={
                "openrouter": ProviderConfig(
                    type=LLMProvider.OPENAI_COMPATIBLE,
                    api_key="sk-or-test",
                    base_url="https://openrouter.ai/api/v1",
                )
            },
            default_provider_ref="openrouter",
        )
        cfg = LLMConfig.from_app_config(app, "solve")
        assert cfg.base_url == "https://openrouter.ai/api/v1"

    def test_task_model_overrides_provider_default(self):
        """task.model 优先于 provider.default_model"""
        app = _make_app(
            providers={
                "anthropic_official": ProviderConfig(
                    type=LLMProvider.ANTHROPIC,
                    api_key="sk-ant-test",
                    default_model="claude-sonnet-4-20250514",
                )
            },
            task_llm={
                "solve": TaskLLMConfig(
                    provider_ref="anthropic_official",
                    model="claude-opus-4-20250805",
                )
            },
        )
        cfg = LLMConfig.from_app_config(app, "solve")
        assert cfg.model == "claude-opus-4-20250805"

    def test_preset_temperature_overrides_provider(self):
        """PRESETS['solve'].temperature=0.0 优先于 provider.temperature=0.9"""
        app = _make_app(
            providers={
                "x": ProviderConfig(
                    type=LLMProvider.ANTHROPIC,
                    api_key="test",
                    temperature=0.9,
                )
            },
            task_llm={"solve": TaskLLMConfig(provider_ref="x")},
        )
        cfg = LLMConfig.from_app_config(app, "solve")
        assert cfg.temperature == 0.0  # from PRESETS["solve"]

    def test_task_temperature_overrides_preset(self):
        """task_cfg.temperature 优先于 PRESETS"""
        app = _make_app(
            providers={"x": ProviderConfig(type=LLMProvider.ANTHROPIC, api_key="test")},
            task_llm={"solve": TaskLLMConfig(provider_ref="x", temperature=0.5)},
        )
        cfg = LLMConfig.from_app_config(app, "solve")
        assert cfg.temperature == 0.5

    def test_cli_override_model(self):
        app = _make_app(
            providers={
                "x": ProviderConfig(
                    type=LLMProvider.ANTHROPIC,
                    api_key="test",
                    default_model="claude-sonnet-4-20250514",
                )
            },
            task_llm={"generate": TaskLLMConfig(provider_ref="x")},
        )
        cfg = LLMConfig.from_app_config(app, "generate", model="claude-opus-4-20250805")
        assert cfg.model == "claude-opus-4-20250805"

    def test_extended_params_inherited_from_task(self):
        app = _make_app(
            providers={"x": ProviderConfig(type=LLMProvider.ANTHROPIC, api_key="test")},
            task_llm={
                "generate": TaskLLMConfig(
                    provider_ref="x",
                    top_p=0.95,
                    stop_sequences=["<END>"],
                )
            },
        )
        cfg = LLMConfig.from_app_config(app, "generate")
        assert cfg.top_p == 0.95
        assert cfg.stop_sequences == ["<END>"]

    def test_env_api_key_fallback(self, monkeypatch):
        """ProviderConfig.api_key=None + type=ANTHROPIC → 从 env 拿"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
        app = _make_app(
            providers={"x": ProviderConfig(type=LLMProvider.ANTHROPIC, api_key=None)},
            task_llm={"generate": TaskLLMConfig(provider_ref="x")},
        )
        cfg = LLMConfig.from_app_config(app, "generate")
        assert cfg.api_key == "sk-from-env"

    def test_compatible_no_env_fallback(self, monkeypatch):
        """ANTHROPIC_COMPATIBLE / OPENAI_COMPATIBLE 不 fallback 到标准 env"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-be-used")
        app = _make_app(
            providers={
                "zai": ProviderConfig(
                    type=LLMProvider.ANTHROPIC_COMPATIBLE,
                    api_key=None,
                    base_url="https://api.z.ai/...",
                )
            },
            task_llm={"generate": TaskLLMConfig(provider_ref="zai")},
        )
        cfg = LLMConfig.from_app_config(app, "generate")
        assert cfg.api_key is None

    def test_three_providers_coexist(self):
        """官方 Anthropic + OpenRouter + Ollama 可以同时存在"""
        app = _make_app(
            providers={
                "anthropic_official": ProviderConfig(
                    type=LLMProvider.ANTHROPIC,
                    api_key="sk-ant-x",
                ),
                "openrouter": ProviderConfig(
                    type=LLMProvider.OPENAI_COMPATIBLE,
                    api_key="sk-or-x",
                    base_url="https://openrouter.ai/api/v1",
                ),
                "ollama": ProviderConfig(
                    type=LLMProvider.OPENAI_COMPATIBLE,
                    api_key="dummy",
                    base_url="http://localhost:11434/v1",
                ),
            },
            task_llm={
                "generate": TaskLLMConfig(provider_ref="anthropic_official"),
                "solve": TaskLLMConfig(provider_ref="openrouter"),
                "tag": TaskLLMConfig(provider_ref="ollama"),
            },
        )
        gen_cfg = LLMConfig.from_app_config(app, "generate")
        solve_cfg = LLMConfig.from_app_config(app, "solve")
        tag_cfg = LLMConfig.from_app_config(app, "tag")

        assert gen_cfg.provider == LLMProvider.ANTHROPIC
        assert gen_cfg.base_url is None  # 官方无 base_url
        assert solve_cfg.base_url == "https://openrouter.ai/api/v1"
        assert tag_cfg.base_url == "http://localhost:11434/v1"
        # 三个互不干扰
        assert gen_cfg.api_key == "sk-ant-x"
        assert solve_cfg.api_key == "sk-or-x"
        assert tag_cfg.api_key == "dummy"


# ── KNOWN_TASKS 一致性 ────────────────────────────────────


class TestKnownTasksAlignment:
    def test_known_tasks_includes_all_presets(self):
        """KNOWN_TASKS 必须覆盖 LLMConfig.PRESETS 的所有 key"""
        from sciforge.config.settings import KNOWN_TASKS

        missing = set(LLMConfig.PRESETS.keys()) - set(KNOWN_TASKS)
        assert missing == set(), f"PRESETS 里这些 task 未在 KNOWN_TASKS 中: {missing}"


# ── for_task 兼容性（保留方法）─────────────────────────────


class TestForTaskHelper:
    def test_for_task_applies_preset(self):
        cfg = LLMConfig.for_task("solve", api_key="test")
        assert cfg.temperature == 0.0
        assert cfg.max_tokens == 16384

    def test_for_task_override_wins_over_preset(self):
        cfg = LLMConfig.for_task("solve", api_key="test", temperature=0.5)
        assert cfg.temperature == 0.5


# ── REC-13：thinking 默认值 ─────────────────────────────────


class TestThinkingPresets:
    def test_extract_preset_thinking_disabled(self) -> None:
        from sciforge.config.settings import AppConfig

        cfg = LLMConfig.from_app_config(AppConfig(), task="extract_and_classify")
        assert cfg.thinking == {"type": "disabled"}

    def test_tag_preset_thinking_small_budget(self) -> None:
        from sciforge.config.settings import AppConfig

        cfg = LLMConfig.from_app_config(AppConfig(), task="tag")
        assert cfg.thinking == {"type": "enabled", "budget_tokens": 512}

    def test_annotate_review_preset_thinking_present(self) -> None:
        from sciforge.config.settings import AppConfig

        cfg = LLMConfig.from_app_config(AppConfig(), task="annotate_review")
        assert cfg.thinking is not None and cfg.thinking["type"] == "enabled"

    def test_task_cfg_thinking_overrides_preset(self) -> None:
        from sciforge.config.settings import AppConfig
        from sciforge.llm.config import TaskLLMConfig

        app = AppConfig(
            task_llm={
                "extract_and_classify": TaskLLMConfig(
                    thinking={"type": "enabled", "budget_tokens": 99}
                )
            }
        )
        cfg = LLMConfig.from_app_config(app, task="extract_and_classify")
        assert cfg.thinking == {"type": "enabled", "budget_tokens": 99}

    def test_generate_preset_has_no_thinking_default(self) -> None:
        from sciforge.config.settings import AppConfig

        cfg = LLMConfig.from_app_config(AppConfig(), task="generate")
        assert cfg.thinking is None
