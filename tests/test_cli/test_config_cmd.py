"""config show / validate CLI 子命令测试"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from sciforge.cli.config_cmd import _mask_key, config_app

runner = CliRunner()


def _write_toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "sciforge.toml"
    path.write_text(content, encoding="utf-8")
    return path


# ── _mask_key 辅助 ──────────────────────────────────────


class TestMaskKey:
    def test_long_key_masked(self):
        assert _mask_key("sk-ant-api03-0123456789abcdef") == "sk-a...cdef"

    def test_short_key_full_mask(self):
        assert _mask_key("shorty") == "***"

    def test_empty_key(self):
        assert _mask_key(None) == "(未设置)"
        assert _mask_key("") == "(未设置)"


# ── config show 命令 ────────────────────────────────────


class TestConfigShow:
    def test_show_hides_api_keys(self, tmp_path):
        toml_path = _write_toml(
            tmp_path,
            """
[providers.anthropic_official]
type = "anthropic"
api_key = "sk-ant-api03-0123456789abcdefghijk"

[providers.openrouter]
type = "openai_compatible"
api_key = "sk-or-v1-totallysecretvaluehere"
base_url = "https://openrouter.ai/api/v1"

[tasks.generate]
provider_ref = "anthropic_official"
""",
        )
        result = runner.invoke(
            config_app, ["show", "--llm-config", str(toml_path)]
        )
        assert result.exit_code == 0
        # 完整 API key 不出现在输出里
        assert "sk-ant-api03-0123456789abcdefghijk" not in result.output
        assert "sk-or-v1-totallysecretvaluehere" not in result.output
        # 但表头和 provider 别名出现
        assert "anthropic_official" in result.output
        assert "openrouter" in result.output

    def test_show_nonexistent_file_errors(self, tmp_path):
        result = runner.invoke(
            config_app,
            ["show", "--llm-config", str(tmp_path / "does_not_exist.toml")],
        )
        assert result.exit_code == 1

    def test_show_with_tasks_extras(self, tmp_path):
        toml_path = _write_toml(
            tmp_path,
            """
[providers.x]
type = "anthropic"
api_key = "test-key-1234-5678"

[tasks.solve_deep]
provider_ref = "x"
thinking = { type = "enabled", budget_tokens = 8192 }
top_p = 0.9
""",
        )
        result = runner.invoke(
            config_app, ["show", "--llm-config", str(toml_path)]
        )
        assert result.exit_code == 0
        assert "solve_deep" in result.output
        assert "thinking" in result.output
        assert "top_p" in result.output


# ── config validate 命令 ────────────────────────────────


class TestConfigValidate:
    def test_validate_all_known_tasks_success(self, tmp_path):
        toml_path = _write_toml(
            tmp_path,
            """
default_provider_ref = "openrouter"

[providers.openrouter]
type = "openai_compatible"
api_key = "sk-or-test"
base_url = "https://openrouter.ai/api/v1"
default_model = "anthropic/claude-sonnet-4"
""",
        )
        result = runner.invoke(
            config_app, ["validate", "--llm-config", str(toml_path)]
        )
        assert result.exit_code == 0
        # 所有 KNOWN_TASKS 都应 OK
        from sciforge.config.settings import KNOWN_TASKS

        for task in KNOWN_TASKS:
            assert task in result.output

    def test_validate_missing_provider_ref_errors(self, tmp_path):
        """task 引用不存在的 provider_ref → exit 1"""
        toml_path = _write_toml(
            tmp_path,
            """
[providers.only_one]
type = "anthropic"
api_key = "test"

[tasks.generate]
provider_ref = "nonexistent_provider"
""",
        )
        result = runner.invoke(
            config_app, ["validate", "--llm-config", str(toml_path)]
        )
        assert result.exit_code == 1
        assert "ERR" in result.output
        assert "nonexistent_provider" in result.output

    def test_validate_nonexistent_file_errors(self, tmp_path):
        result = runner.invoke(
            config_app,
            [
                "validate",
                "--llm-config",
                str(tmp_path / "does_not_exist.toml"),
            ],
        )
        assert result.exit_code == 1

    def test_validate_shows_resolved_provider_and_model(self, tmp_path):
        toml_path = _write_toml(
            tmp_path,
            """
[providers.anthropic_official]
type = "anthropic"
api_key = "sk-ant-test"
default_model = "claude-sonnet-4-20250514"

[tasks.generate]
provider_ref = "anthropic_official"
""",
        )
        result = runner.invoke(
            config_app, ["validate", "--llm-config", str(toml_path)]
        )
        assert result.exit_code == 0
        assert "anthropic" in result.output
        assert "claude-sonnet-4-20250514" in result.output
