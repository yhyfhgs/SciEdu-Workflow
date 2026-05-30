"""config 命令组 — 配置诊断与校验

子命令:
- `sciforge config show [--llm-config PATH]`
    打印解析后的 AppConfig.providers / task_llm（API key 被脱敏）。

- `sciforge config validate [--llm-config PATH]`
    遍历 KNOWN_TASKS，对每个 task 调用 LLMConfig.from_app_config 并报告最终配置，
    若任意 task 解析失败则 exit 1。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from sciforge.cli.display import error, success

config_app = typer.Typer(name="config", help="LLM 配置诊断与校验")
# 固定宽度避免在测试 / 非 TTY 环境下表格被截断
console = Console(width=200)


def _load_config(llm_config_file: Path | None) -> Any:
    """构造 AppConfig 并校验 TOML 文件存在"""
    from sciforge.config.settings import AppConfig

    if llm_config_file and not llm_config_file.exists():
        error(f"配置文件不存在: {llm_config_file}")
        raise typer.Exit(1)
    return AppConfig(llm_config_file=llm_config_file)


def _mask_key(key: str | None) -> str:
    """API key 脱敏：保留前 4 和后 4，中间替换为星号"""
    if not key:
        return "(未设置)"
    if len(key) <= 12:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


@config_app.command("show")
def config_show(
    llm_config: Path | None = typer.Option(
        Path("config/sciforge.toml"),
        "--llm-config",
        help="LLM TOML 配置文件路径",
    ),
) -> None:
    """打印解析后的 AppConfig（providers / tasks / default_provider_ref，API key 脱敏）"""
    app = _load_config(llm_config)

    # Providers 表
    p_table = Table(title="Providers", show_lines=True)
    p_table.add_column("别名", style="cyan")
    p_table.add_column("类型", style="magenta")
    p_table.add_column("base_url")
    p_table.add_column("默认模型")
    p_table.add_column("api_key", style="dim")
    for name, cfg in app.providers.items():
        p_table.add_row(
            name,
            cfg.type.value,
            cfg.base_url or "(官方)",
            cfg.default_model or "-",
            _mask_key(cfg.api_key),
        )
    if app.providers:
        console.print(p_table)
    else:
        console.print("[yellow]未定义任何 provider[/]")

    console.print()
    console.print(f"[bold]default_provider_ref[/] = {app.default_provider_ref or '(未设置)'}")
    console.print()

    # Tasks 表
    t_table = Table(title="Tasks", show_lines=True)
    t_table.add_column("任务", style="cyan")
    t_table.add_column("provider_ref", style="magenta")
    t_table.add_column("model")
    t_table.add_column("temperature")
    t_table.add_column("max_tokens")
    t_table.add_column("其他")
    for name, cfg in app.task_llm.items():
        extras: list[str] = []
        for fld in (
            "top_p",
            "top_k",
            "seed",
            "stop_sequences",
            "response_format",
            "thinking",
            "reasoning_effort",
            "extra_body",
        ):
            val = getattr(cfg, fld, None)
            if val is not None:
                extras.append(f"{fld}={val}")
        t_table.add_row(
            name,
            cfg.provider_ref or "-",
            cfg.model or "-",
            str(cfg.temperature) if cfg.temperature is not None else "-",
            str(cfg.max_tokens) if cfg.max_tokens is not None else "-",
            "; ".join(extras) or "-",
        )
    if app.task_llm:
        console.print(t_table)
    else:
        console.print("[yellow]未定义任何 task（将走 default_provider_ref + PRESETS）[/]")


@config_app.command("validate")
def config_validate(
    llm_config: Path | None = typer.Option(
        Path("config/sciforge.toml"),
        "--llm-config",
        help="LLM TOML 配置文件路径",
    ),
) -> None:
    """遍历每个 KNOWN_TASK，尝试解析 LLMConfig，报告最终配置与错误"""
    from sciforge.config.settings import KNOWN_TASKS
    from sciforge.llm.config import LLMConfig

    app = _load_config(llm_config)

    table = Table(title="Task 配置校验", show_lines=True)
    table.add_column("Task", style="cyan")
    table.add_column("状态")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("错误/说明", style="yellow")

    all_ok = True
    for task_name in KNOWN_TASKS:
        try:
            cfg = LLMConfig.from_app_config(app, task_name)
        except Exception as exc:
            all_ok = False
            table.add_row(task_name, "[red]ERR[/]", "-", "-", str(exc))
            continue
        table.add_row(
            task_name,
            "[green]OK[/]",
            cfg.provider.value,
            cfg.model,
            "",
        )

    console.print(table)

    if not all_ok:
        error("部分 task 配置解析失败")
        raise typer.Exit(1)
    success("所有 task 配置解析成功")
