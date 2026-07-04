"""WP-42 config doctor CLI 子命令——capability 自检（mock LLMClient，0 真实调用）。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from sciforge.cli.config_cmd import config_app

runner = CliRunner()


def _write_toml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "sciforge.toml"
    path.write_text(content, encoding="utf-8")
    return path


_ANTHROPIC_TOML = """
[providers.anthropic_official]
type = "anthropic"
api_key = "sk-ant-test"
default_model = "claude-sonnet-4-20250514"

[tasks.extract_and_classify]
provider_ref = "anthropic_official"
"""

_OPENAI_TOML = """
[providers.openai_official]
type = "openai"
api_key = "sk-test"
default_model = "gpt-4o"

[tasks.extract_and_classify]
provider_ref = "openai_official"
"""


def test_doctor_nonexistent_config_file_errors(tmp_path):
    result = runner.invoke(
        config_app,
        ["doctor", "--llm-config", str(tmp_path / "does_not_exist.toml")],
    )
    assert result.exit_code == 1


def test_doctor_anthropic_reports_multiturn_supported(tmp_path):
    toml_path = _write_toml(tmp_path, _ANTHROPIC_TOML)
    with (
        patch(
            "sciforge.llm.client.LLMClient.generate_with_tools",
            new=AsyncMock(return_value={"tool_calls": []}),
        ),
        patch(
            "sciforge.llm.client.LLMClient.generate",
            new=AsyncMock(return_value="{}"),
        ),
        patch("sciforge.llm.backends.anthropic.AnthropicBackend.__init__", return_value=None),
    ):
        result = runner.invoke(
            config_app,
            ["doctor", "--task", "extract_and_classify", "--llm-config", str(toml_path)],
        )
    assert result.exit_code == 0
    assert "multiturn" in result.output
    assert "支持" in result.output


def test_doctor_openai_reports_multiturn_unsupported(tmp_path):
    toml_path = _write_toml(tmp_path, _OPENAI_TOML)
    with (
        patch(
            "sciforge.llm.client.LLMClient.generate_with_tools",
            new=AsyncMock(return_value={"tool_calls": []}),
        ),
        patch(
            "sciforge.llm.client.LLMClient.generate",
            new=AsyncMock(return_value="{}"),
        ),
        patch("sciforge.llm.backends.openai_backend.OpenAIBackend.__init__", return_value=None),
    ):
        result = runner.invoke(
            config_app,
            ["doctor", "--task", "extract_and_classify", "--llm-config", str(toml_path)],
        )
    assert result.exit_code == 0
    assert "不支持" in result.output  # multiturn 一行


def test_doctor_reports_tool_use_failure(tmp_path):
    toml_path = _write_toml(tmp_path, _ANTHROPIC_TOML)
    from sciforge.core.exceptions import ToolCallError

    with (
        patch(
            "sciforge.llm.client.LLMClient.generate_with_tools",
            new=AsyncMock(side_effect=ToolCallError("not supported")),
        ),
        patch(
            "sciforge.llm.client.LLMClient.generate",
            new=AsyncMock(return_value="{}"),
        ),
        patch("sciforge.llm.backends.anthropic.AnthropicBackend.__init__", return_value=None),
    ):
        result = runner.invoke(
            config_app,
            ["doctor", "--task", "extract_and_classify", "--llm-config", str(toml_path)],
        )
    assert result.exit_code == 0
    assert "不支持" in result.output
    assert "not supported" in result.output
