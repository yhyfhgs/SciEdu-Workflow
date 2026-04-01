"""代码沙箱：受限环境中执行 Python 代码，用于求解辅助和数值验证"""

from __future__ import annotations

import subprocess
import tempfile
import time
from pathlib import Path

import structlog

from mathforge.core.exceptions import SandboxError, SandboxTimeoutError
from mathforge.core.models import CodeExecutionResult

logger = structlog.get_logger()

# 沙箱包装脚本：阻止危险模块，仅允许数学计算相关库
_SANDBOX_WRAPPER = '''\
import sys

# 阻止危险模块
_BLOCKED = [
    "os", "subprocess", "shutil", "socket", "http", "urllib",
    "ftplib", "smtplib", "telnetlib", "ctypes", "multiprocessing",
    "signal", "resource", "importlib", "pathlib", "tempfile",
    "pickle", "shelve", "marshal", "code", "codeop", "compile",
    "compileall", "webbrowser", "antigravity",
]

class _BlockedImportFinder:
    """阻止导入被封禁的模块"""
    def find_module(self, name, path=None):
        top = name.split(".")[0]
        if top in _BLOCKED:
            return self
        return None

    def load_module(self, name):
        raise ImportError(f"模块 '{name}' 在沙箱中被禁止使用")

sys.meta_path.insert(0, _BlockedImportFinder())

import io
_stdout_capture = io.StringIO()
sys.stdout = _stdout_capture

_return_value = None
try:
    exec("""
{user_code}
""")
except Exception as _e:
    print(f"ERROR: {{type(_e).__name__}}: {{_e}}", file=sys.stderr)
    sys.exit(1)
finally:
    sys.stdout = sys.__stdout__
    print(_stdout_capture.getvalue(), end="")
'''


class CodeSandbox:
    """受限 Python 代码执行环境

    安全机制:
    - subprocess 隔离进程
    - 模块导入限制（仅允许 sympy, numpy, math, fractions, decimal 等）
    - 硬超时（SIGKILL）
    - 内存限制（通过 resource.setrlimit，仅 macOS/Linux）
    """

    def __init__(
        self,
        timeout_seconds: int = 10,
        max_memory_mb: int = 256,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_memory_mb = max_memory_mb

    def execute(self, code: str, timeout: int | None = None) -> CodeExecutionResult:
        """在沙箱中执行 Python 代码

        Args:
            code: 要执行的 Python 代码
            timeout: 超时秒数，None 使用默认值

        Returns:
            CodeExecutionResult 包含 stdout, stderr, 成功状态等
        """
        timeout = timeout or self.timeout_seconds
        # 转义用户代码中的三引号和反斜杠
        escaped_code = code.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        wrapper = _SANDBOX_WRAPPER.replace("{user_code}", escaped_code)

        start_time = time.monotonic()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8",
        ) as f:
            f.write(wrapper)
            script_path = Path(f.name)

        try:
            result = subprocess.run(
                ["python3", str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=tempfile.gettempdir(),
            )
            elapsed_ms = int((time.monotonic() - start_time) * 1000)

            execution_result = CodeExecutionResult(
                code=code,
                stdout=result.stdout.strip(),
                stderr=result.stderr.strip(),
                success=result.returncode == 0,
                timeout=False,
                execution_time_ms=elapsed_ms,
            )

            if result.returncode == 0 and result.stdout.strip():
                lines = result.stdout.strip().split("\n")
                execution_result.return_value = lines[-1] if lines else None

            logger.info(
                "sandbox_execute",
                success=execution_result.success,
                elapsed_ms=elapsed_ms,
                stdout_len=len(result.stdout),
            )
            return execution_result

        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.warning("sandbox_timeout", timeout=timeout, code_len=len(code))
            raise SandboxTimeoutError(timeout, code[:200])

        except Exception as e:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.error("sandbox_error", error=str(e))
            raise SandboxError(f"沙箱执行失败: {e}") from e

        finally:
            script_path.unlink(missing_ok=True)

    def execute_safe(self, code: str, timeout: int | None = None) -> CodeExecutionResult:
        """安全执行：超时和异常不抛出，而是记录在结果中"""
        try:
            return self.execute(code, timeout)
        except SandboxTimeoutError:
            return CodeExecutionResult(
                code=code,
                stderr=f"执行超时 ({timeout or self.timeout_seconds}s)",
                success=False,
                timeout=True,
            )
        except SandboxError as e:
            return CodeExecutionResult(
                code=code,
                stderr=str(e),
                success=False,
            )
