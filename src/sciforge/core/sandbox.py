"""数值验证沙箱 — 在独立子进程中执行受信任的验证代码。

本模块为 QGen / Solve 等 Agent 提供一个**最小可用**的代码执行隔离层，
专门用于运行由 LLM 产出的 ``sympy`` / ``numpy`` 数值验证片段（例如代入检验、
等式数值核对）。它**不是**通用的不可信代码沙箱。

隔离级别路线图：
    - **P1（本实现）**：子进程级隔离 —— 用 ``subprocess`` + ``sys.executable``
      在独立解释器进程里跑代码，配合 ``resource.setrlimit`` 限制 CPU 时间与
      地址空间，并用临时目录作为 cwd。进程崩溃 / 死循环 / OOM 不会拖垮主进程。
    - **P3（升级方向）**：容器级隔离 —— 迁移到 gVisor / Docker（或 nsjail）做
      文件系统、网络、系统调用层面的强隔离，方可执行**不受信任**的任意代码。

安全边界（务必知悉）：
    本沙箱**仅供执行受信任来源的 sympy/numpy 数值验证代码**。子进程仍能访问
    网络、文件系统（cwd 之外）与全部 Python 标准库；P1 隔离只防"误伤"
    （失控资源占用、崩溃），不防"恶意"。切勿用它执行任意用户输入的代码。
"""

from __future__ import annotations

import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Final

import structlog

from sciforge.core.models import CodeExecutionResult

logger = structlog.get_logger()

# 是否为非 Windows 平台（POSIX）。仅在此类平台启用 resource.setrlimit 资源限制。
_IS_POSIX: Final[bool] = sys.platform != "win32"

# RLIMIT_AS（地址空间上限）是否可用。macOS/Darwin 上对 RLIMIT_AS 的支持名存实亡：
# Python 解释器自身在 arm64 启动时即映射 > 数 GiB 虚拟地址空间，任何"有意义"的
# AS 软限都会让子进程 exec 阶段直接失败（preexec_fn 抛错）。因此 AS 限制只在
# Linux 上施加；其余 POSIX 平台仅靠 RLIMIT_CPU + 墙钟超时兜底。
_AS_LIMIT_SUPPORTED: Final[bool] = sys.platform.startswith("linux")

# 地址空间（虚拟内存）上限，单位字节。2 GiB 对 sympy/numpy 的数值验证足够宽裕，
# 同时能挡住失控的内存分配。设得过小会让 numpy 等库的导入直接失败。
_ADDRESS_SPACE_LIMIT_BYTES: Final[int] = 2 * 1024 * 1024 * 1024

# CPU 时间软限在 wall-clock timeout 之上额外留的秒数：
# RLIMIT_CPU 计的是消耗的 CPU 秒，subprocess timeout 计的是墙钟秒。
# 给 RLIMIT_CPU 多留一点，保证墙钟 timeout 在正常情况下先触发，
# RLIMIT_CPU 只作为兜底（防止子进程忽略 SIGTERM 后仍空转 CPU）。
_CPU_LIMIT_SLACK_SECONDS: Final[int] = 1


def _make_rlimit_preexec(cpu_seconds: int) -> Callable[[], None]:
    """构造子进程 ``preexec_fn``，在 fork 后、exec 前施加资源软/硬限制。

    仅在 POSIX 平台调用；Windows 不支持 ``preexec_fn`` 与 ``RLIMIT_*``。
    """

    def _apply_limits() -> None:  # pragma: no cover - 运行在子进程，覆盖率工具不可见
        # CPU 时间（秒）：超限时内核先发 SIGXCPU，硬限到达发 SIGKILL。
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        # 地址空间（字节）：超额分配触发 MemoryError / 分配失败。
        # 仅在支持的平台（Linux）施加；macOS 上低 AS 限会让子进程 exec 直接失败。
        # 即便如此，单独 try 兜底——AS 软限设置失败绝不能拖垮一次合法执行，
        # 此时退化为仅 CPU 限制 + 墙钟超时。
        if _AS_LIMIT_SUPPORTED:
            try:
                resource.setrlimit(
                    resource.RLIMIT_AS,
                    (_ADDRESS_SPACE_LIMIT_BYTES, _ADDRESS_SPACE_LIMIT_BYTES),
                )
            except (ValueError, OSError):
                pass

    return _apply_limits


def run_sandboxed(code: str, timeout_s: int = 10) -> CodeExecutionResult:
    """在独立子进程中执行 ``code``，返回结构化执行结果。

    实现要点：
        - 用 ``sys.executable -c`` 启一个全新解释器进程跑 ``code``，绝不在当前
          进程内 ``exec`` / ``eval``。
        - cwd 设为一次性临时目录，函数返回前自动清理。
        - 墙钟超时由 ``subprocess`` 的 ``timeout`` 控制；超时后 kill 子进程并
          返回 ``timeout=True`` / ``success=False``。
        - POSIX 平台经 ``preexec_fn`` 用 ``resource.setrlimit`` 限制 CPU 时间
          （``RLIMIT_CPU``）与地址空间（``RLIMIT_AS``）；Windows 跳过。
        - 捕获 stdout / stderr，记录 ``execution_time_ms``（``time.perf_counter`` 墙钟差）。

    Args:
        code: 待执行的 Python 源码（受信任的 sympy/numpy 数值验证片段）。
        timeout_s: 墙钟超时秒数；非正值会被夹到至少 1 秒。

    Returns:
        填充好 stdout/stderr/success/timeout/execution_time_ms 的
        :class:`~sciforge.core.models.CodeExecutionResult`。
    """
    effective_timeout = max(1, timeout_s)
    cpu_limit_seconds = effective_timeout + _CPU_LIMIT_SLACK_SECONDS

    # POSIX 才施加 rlimit；Windows 传 None（subprocess 要求 preexec_fn 仅 POSIX 可用）。
    preexec_fn = _make_rlimit_preexec(cpu_limit_seconds) if _IS_POSIX else None

    start = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="sciforge_sandbox_") as workdir:
        try:
            completed = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=workdir,
                preexec_fn=preexec_fn,  # noqa: PLW1509 - POSIX-only rlimit, gated by _IS_POSIX
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # subprocess.run 在超时时已 kill 子进程并回收；其捕获到的部分输出
            # 以 bytes/str 形式挂在异常上（text=True 时为 str，可能为 None）。
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            stdout = _decode_stream(exc.stdout)
            stderr = _decode_stream(exc.stderr)
            logger.warning(
                "sandbox_timeout",
                timeout_s=effective_timeout,
                execution_time_ms=elapsed_ms,
            )
            return CodeExecutionResult(
                code=code,
                stdout=stdout,
                stderr=stderr,
                success=False,
                timeout=True,
                execution_time_ms=elapsed_ms,
            )

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    success = completed.returncode == 0
    if not success:
        logger.debug(
            "sandbox_nonzero_exit",
            returncode=completed.returncode,
            execution_time_ms=elapsed_ms,
        )
    return CodeExecutionResult(
        code=code,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        success=success,
        timeout=False,
        execution_time_ms=elapsed_ms,
    )


def _decode_stream(stream: str | bytes | None) -> str:
    """把 subprocess 捕获到的流（str / bytes / None）统一成 str。"""
    if stream is None:
        return ""
    if isinstance(stream, bytes):
        return stream.decode("utf-8", errors="replace")
    return stream
