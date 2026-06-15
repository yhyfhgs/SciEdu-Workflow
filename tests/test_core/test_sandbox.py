"""数值验证沙箱 (core.sandbox) 单元测试。

覆盖：正常执行、墙钟超时、运行时异常、非零退出（非超时）。
所有超时用例均设 1 秒以保持测试快速；在 macOS / POSIX 上可稳定通过。
"""

from __future__ import annotations

import sys

import pytest

from sciforge.core.models import CodeExecutionResult
from sciforge.core.sandbox import run_sandboxed


def test_normal_execution_captures_stdout() -> None:
    """正常执行：stdout 含输出、success=True、未超时。"""
    result = run_sandboxed("print('PASS')", timeout_s=5)

    assert isinstance(result, CodeExecutionResult)
    assert "PASS" in result.stdout
    assert result.success is True
    assert result.timeout is False
    assert result.stderr == ""
    assert result.code == "print('PASS')"
    assert result.execution_time_ms >= 0


def test_timeout_is_flagged() -> None:
    """超时：sleep 远超 timeout_s → timeout=True、success=False。"""
    result = run_sandboxed("import time; time.sleep(5)", timeout_s=1)

    assert result.timeout is True
    assert result.success is False


def test_runtime_exception_marks_failure() -> None:
    """运行时异常：非零退出 → success=False，stderr 含 traceback。"""
    result = run_sandboxed("raise ValueError('boom')", timeout_s=5)

    assert result.success is False
    assert result.timeout is False
    assert result.stderr != ""
    assert "ValueError" in result.stderr


def test_nonzero_exit_without_exception() -> None:
    """显式非零退出码（非超时）也算失败。"""
    result = run_sandboxed("import sys; sys.exit(3)", timeout_s=5)

    assert result.success is False
    assert result.timeout is False


def test_runs_in_isolated_subprocess() -> None:
    """代码在独立子进程执行：其 PID 必与当前进程不同。"""
    result = run_sandboxed("import os; print(os.getpid())", timeout_s=5)

    assert result.success is True
    child_pid = int(result.stdout.strip())
    assert child_pid != __import__("os").getpid()


def test_numeric_verification_style_snippet() -> None:
    """典型数值验证片段：在子进程里算并打印结果。"""
    code = "vals = [i * i for i in range(4)]; print(sum(vals))"
    result = run_sandboxed(code, timeout_s=5)

    assert result.success is True
    assert result.stdout.strip() == "14"


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="RLIMIT_AS 只在 Linux 上可靠生效（macOS 对其支持名存实亡）",
)
def test_address_space_limit_blocks_huge_allocation() -> None:
    """Linux：超额内存分配被 RLIMIT_AS 拦截（不拖垮主进程）。"""
    # 尝试一次性分配远超 2 GiB 上限的 bytearray → 子进程应失败而非 OOM 主机。
    code = "x = bytearray(8 * 1024 * 1024 * 1024)"
    result = run_sandboxed(code, timeout_s=5)

    assert result.success is False
    assert result.timeout is False
