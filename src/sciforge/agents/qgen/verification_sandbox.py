"""Policy-aware execution backends for QGen verification.

The existing :mod:`sciforge.core.sandbox` module is intentionally a trusted-code
subprocess wrapper.  This module adds the policy boundary needed by a generation
harness:

* generated or otherwise untrusted Python defaults to a restricted Docker/Podman
  container;
* local Python is available only after the caller explicitly marks the source as
  trusted;
* Lean checks use a container by default and never silently fall back to a host
  ``lean``/``lake`` executable.

The container profile materially reduces access to the host, but it is not a
formal hostile-code isolation guarantee.  High-risk deployments should run the
container runtime with an additional sandboxed runtime such as gVisor.  If no
container runtime is installed, this module reports ``unavailable`` instead of
pretending that a local subprocess has the same isolation properties.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Final

if TYPE_CHECKING:
    from sciforge.config.settings import AppConfig


class TrustPolicy(StrEnum):
    """Execution decision made by the caller.

    ``CONTAINER_REQUIRED`` is the safe default for LLM-generated or user supplied
    source.  ``TRUSTED_LOCAL`` is an explicit acknowledgement that the caller is
    willing to execute the source with the current user's host permissions.
    """

    CONTAINER_REQUIRED = "container_required"
    TRUSTED_LOCAL = "trusted_local"


class SandboxStatus(StrEnum):
    """Lifecycle result of one verification command."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    UNAVAILABLE = "unavailable"
    POLICY_DENIED = "policy_denied"


class IsolationLevel(StrEnum):
    """Isolation boundary actually used, not the boundary that was requested."""

    NONE = "none"
    TRUSTED_PROCESS = "trusted_process"
    RESTRICTED_CONTAINER = "restricted_container"


class ContainerRuntime(StrEnum):
    """Supported OCI command-line runtimes."""

    AUTO = "auto"
    DOCKER = "docker"
    PODMAN = "podman"


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Resource and output limits shared by verification runners."""

    timeout_seconds: float = 10.0
    memory_mb: int = 512
    cpus: float = 1.0
    pids_limit: int = 64
    tmpfs_mb: int = 64
    max_output_bytes: int = 64 * 1024
    cleanup_timeout_seconds: float = 5.0

    def __post_init__(self) -> None:
        for name in ("timeout_seconds", "cpus", "cleanup_timeout_seconds"):
            if float(getattr(self, name)) <= 0:
                raise ValueError(f"{name} must be positive")
        for name in ("memory_mb", "pids_limit", "tmpfs_mb", "max_output_bytes"):
            value = getattr(self, name)
            if isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class VerificationExecutionResult:
    """Auditable result shared by Python and Lean verification.

    ``reason`` describes infrastructure or policy failures.  Compiler diagnostics
    are kept separately so callers can display them in a verification panel or
    feed them back into the generation loop without parsing all stdout/stderr.
    """

    status: SandboxStatus
    backend: str
    isolation: IsolationLevel
    trust_policy: TrustPolicy
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    command: tuple[str, ...] = ()
    reason: str = ""
    diagnostics: tuple[str, ...] = ()
    output_truncated: bool = False

    @property
    def success(self) -> bool:
        return self.status is SandboxStatus.SUCCEEDED

    @property
    def available(self) -> bool:
        return self.status is not SandboxStatus.UNAVAILABLE

    @property
    def timed_out(self) -> bool:
        return self.status is SandboxStatus.TIMED_OUT

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-compatible representation for run events/checkpoints."""

        return {
            "status": self.status.value,
            "backend": self.backend,
            "isolation": self.isolation.value,
            "trust_policy": self.trust_policy.value,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "command": list(self.command),
            "reason": self.reason,
            "diagnostics": list(self.diagnostics),
            "output_truncated": self.output_truncated,
        }


_OCI_IMAGE_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@:+-]{0,254}$")
_SAFE_FILENAME_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LEAN_DIAGNOSTIC_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\berror\b|\bwarning\b|unsolved goals|declaration uses ['\"]?sorry)",
    re.IGNORECASE,
)
_LEAN_ADMISSION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:declaration uses ['\"]?sorry|\bunsolved goals\b|\badmitted\b)",
    re.IGNORECASE,
)


class ContainerCommandRunner:
    """Run one source file with a fixed command in a restricted OCI container."""

    def __init__(
        self,
        *,
        image: str,
        runtime: ContainerRuntime | str = ContainerRuntime.AUTO,
        limits: SandboxLimits | None = None,
    ) -> None:
        if not _OCI_IMAGE_RE.fullmatch(image):
            raise ValueError("image must be a valid non-option OCI image reference")
        self.image = image
        self.runtime = ContainerRuntime(runtime)
        self.limits = limits or SandboxLimits()

    def run_source(
        self,
        source: str,
        *,
        filename: str,
        command: Sequence[str],
    ) -> VerificationExecutionResult:
        """Write ``source`` read-only and execute ``command`` inside the container.

        ``command`` must reference the mounted file as ``/workspace/<filename>``.
        It is passed directly as an argv list; no shell performs interpolation.
        """

        _validate_filename(filename)
        inner_command = _validate_command(command)
        runtime_path, runtime_name = _resolve_runtime(self.runtime)
        backend = f"container:{runtime_name or self.runtime.value}"
        if runtime_path is None:
            requested = self.runtime.value
            return _unavailable_result(
                backend=backend,
                policy=TrustPolicy.CONTAINER_REQUIRED,
                reason=f"container runtime unavailable (requested={requested})",
            )

        container_name = f"sciforge-verify-{uuid.uuid4().hex[:20]}"
        with tempfile.TemporaryDirectory(prefix="sciforge_verify_container_") as raw_workdir:
            workdir = Path(raw_workdir)
            source_path = workdir / filename
            source_path.write_text(source, encoding="utf-8")
            # The container runs as nobody.  The bind mount and root filesystem are
            # read-only, while /tmp is the only writable location.
            os.chmod(workdir, 0o755)
            os.chmod(source_path, 0o444)

            argv = self._container_argv(
                runtime_path=runtime_path,
                container_name=container_name,
                workdir=workdir,
                inner_command=inner_command,
            )
            result = _run_command(
                argv,
                backend=backend,
                isolation=IsolationLevel.RESTRICTED_CONTAINER,
                policy=TrustPolicy.CONTAINER_REQUIRED,
                limits=self.limits,
                on_timeout=lambda: _force_remove_container(
                    runtime_path,
                    container_name,
                    self.limits.cleanup_timeout_seconds,
                ),
            )

        # Docker and Podman use 125/126/127 for daemon/image/entrypoint setup
        # failures.  Those mean the requested backend is not currently usable,
        # rather than that the theorem or Python check returned a negative result.
        if result.status is SandboxStatus.FAILED and result.exit_code in {125, 126, 127}:
            reason = _last_nonempty_line(result.stderr) or "container failed to start"
            return replace(result, status=SandboxStatus.UNAVAILABLE, reason=reason)
        return result

    def _container_argv(
        self,
        *,
        runtime_path: str,
        container_name: str,
        workdir: Path,
        inner_command: tuple[str, ...],
    ) -> list[str]:
        limits = self.limits
        mount = f"type=bind,source={workdir},target=/workspace,readonly"
        tmpfs = f"/tmp:rw,noexec,nosuid,nodev,size={limits.tmpfs_mb}m,mode=1777"
        return [
            runtime_path,
            "run",
            "--rm",
            "--name",
            container_name,
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(limits.pids_limit),
            "--memory",
            f"{limits.memory_mb}m",
            "--cpus",
            format(limits.cpus, "g"),
            "--ipc",
            "none",
            "--ulimit",
            "nofile=64:64",
            "--user",
            "65534:65534",
            "--hostname",
            "sciforge-verifier",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--tmpfs",
            tmpfs,
            "--mount",
            mount,
            self.image,
            *inner_command,
        ]


class ContainerPythonRunner:
    """Python verification backend for untrusted/generated source."""

    def __init__(
        self,
        *,
        image: str = "python:3.11-slim",
        runtime: ContainerRuntime | str = ContainerRuntime.AUTO,
        python_command: Sequence[str] = ("python",),
        limits: SandboxLimits | None = None,
    ) -> None:
        self._runner = ContainerCommandRunner(image=image, runtime=runtime, limits=limits)
        self._python_command = _validate_command(python_command)

    def run(self, code: str) -> VerificationExecutionResult:
        command = (*self._python_command, "-I", "-B", "/workspace/verify.py")
        return self._runner.run_source(code, filename="verify.py", command=command)


class TrustedLocalPythonRunner:
    """Host subprocess backend that refuses non-trusted source by policy."""

    def __init__(self, *, limits: SandboxLimits | None = None) -> None:
        self.limits = limits or SandboxLimits()

    def run(
        self,
        code: str,
        *,
        trust_policy: TrustPolicy | str,
    ) -> VerificationExecutionResult:
        policy = TrustPolicy(trust_policy)
        if policy is not TrustPolicy.TRUSTED_LOCAL:
            return VerificationExecutionResult(
                status=SandboxStatus.POLICY_DENIED,
                backend="trusted-local:python",
                isolation=IsolationLevel.NONE,
                trust_policy=policy,
                reason="local Python execution requires explicit trusted_local policy",
            )

        with tempfile.TemporaryDirectory(prefix="sciforge_verify_local_") as raw_workdir:
            workdir = Path(raw_workdir)
            source_path = workdir / "verify.py"
            source_path.write_text(code, encoding="utf-8")
            os.chmod(source_path, 0o400)
            return _run_command(
                [sys.executable, "-I", "-B", str(source_path)],
                backend="trusted-local:python",
                isolation=IsolationLevel.TRUSTED_PROCESS,
                policy=policy,
                limits=self.limits,
                cwd=workdir,
                preexec_fn=_local_resource_limiter(self.limits),
            )


class LeanVerifier:
    """Compile a Lean theorem file in a container or explicitly trusted host tool.

    Container execution is the default.  Supplying ``trusted_local_command`` does
    not enable fallback: callers must additionally pass
    ``trust_policy=TRUSTED_LOCAL`` for every local verification request.
    """

    def __init__(
        self,
        *,
        container_image: str = "leanprover/lean4:latest",
        runtime: ContainerRuntime | str = ContainerRuntime.AUTO,
        container_command: Sequence[str] = ("lean",),
        trusted_local_command: Sequence[str] | None = None,
        trusted_local_cwd: Path | None = None,
        limits: SandboxLimits | None = None,
        reject_admitted_proofs: bool = True,
    ) -> None:
        self.limits = limits or SandboxLimits(timeout_seconds=30.0, memory_mb=1024)
        self._container = ContainerCommandRunner(
            image=container_image,
            runtime=runtime,
            limits=self.limits,
        )
        self._container_command = _validate_command(container_command)
        self._trusted_local_command = (
            _validate_command(trusted_local_command) if trusted_local_command is not None else None
        )
        self._trusted_local_cwd = trusted_local_cwd
        self.reject_admitted_proofs = reject_admitted_proofs

    def verify(
        self,
        theorem_source: str,
        *,
        trust_policy: TrustPolicy | str = TrustPolicy.CONTAINER_REQUIRED,
    ) -> VerificationExecutionResult:
        policy = TrustPolicy(trust_policy)
        if policy is TrustPolicy.CONTAINER_REQUIRED:
            command = (*self._container_command, "/workspace/Main.lean")
            result = self._container.run_source(
                theorem_source,
                filename="Main.lean",
                command=command,
            )
        else:
            result = self._verify_trusted_local(theorem_source)
        return self._attach_diagnostics(result)

    def _verify_trusted_local(self, theorem_source: str) -> VerificationExecutionResult:
        command = self._trusted_local_command
        if command is None:
            return _unavailable_result(
                backend="trusted-local:lean",
                policy=TrustPolicy.TRUSTED_LOCAL,
                reason="trusted local Lean command was not explicitly configured",
            )

        executable = _resolve_local_executable(command[0])
        if executable is None:
            return _unavailable_result(
                backend="trusted-local:lean",
                policy=TrustPolicy.TRUSTED_LOCAL,
                reason=f"trusted local Lean executable unavailable: {command[0]}",
            )

        with tempfile.TemporaryDirectory(prefix="sciforge_verify_lean_") as raw_workdir:
            workdir = Path(raw_workdir)
            source_path = workdir / "Main.lean"
            source_path.write_text(theorem_source, encoding="utf-8")
            os.chmod(source_path, 0o400)
            argv = [executable, *command[1:], str(source_path)]
            return _run_command(
                argv,
                backend="trusted-local:lean",
                isolation=IsolationLevel.TRUSTED_PROCESS,
                policy=TrustPolicy.TRUSTED_LOCAL,
                limits=self.limits,
                cwd=self._trusted_local_cwd or workdir,
                preexec_fn=_local_resource_limiter(self.limits),
            )

    def _attach_diagnostics(
        self, result: VerificationExecutionResult
    ) -> VerificationExecutionResult:
        diagnostics = _lean_diagnostics(result.stdout, result.stderr)
        updated = replace(result, diagnostics=diagnostics)
        if (
            self.reject_admitted_proofs
            and updated.status is SandboxStatus.SUCCEEDED
            and any(_LEAN_ADMISSION_RE.search(line) for line in diagnostics)
        ):
            return replace(
                updated,
                status=SandboxStatus.FAILED,
                reason="Lean output contains an admitted proof or unsolved goals",
            )
        return updated


class VerificationSandbox:
    """Safe-default facade intended for :class:`VerificationPipeline` integration."""

    def __init__(
        self,
        *,
        local_python: TrustedLocalPythonRunner | None = None,
        container_python: ContainerPythonRunner | None = None,
        lean_verifier: LeanVerifier | None = None,
        limits: SandboxLimits | None = None,
    ) -> None:
        effective_limits = limits or SandboxLimits()
        self.local_python = local_python or TrustedLocalPythonRunner(limits=effective_limits)
        self.container_python = container_python or ContainerPythonRunner(limits=effective_limits)
        self.lean_verifier = lean_verifier or LeanVerifier(limits=effective_limits)

    def run_python(
        self,
        code: str,
        *,
        trust_policy: TrustPolicy | str = TrustPolicy.CONTAINER_REQUIRED,
    ) -> VerificationExecutionResult:
        policy = TrustPolicy(trust_policy)
        if policy is TrustPolicy.TRUSTED_LOCAL:
            return self.local_python.run(code, trust_policy=policy)
        return self.container_python.run(code)

    async def arun_python(
        self,
        code: str,
        *,
        trust_policy: TrustPolicy | str = TrustPolicy.CONTAINER_REQUIRED,
    ) -> VerificationExecutionResult:
        return await asyncio.to_thread(self.run_python, code, trust_policy=trust_policy)

    def verify_lean(
        self,
        theorem_source: str,
        *,
        trust_policy: TrustPolicy | str = TrustPolicy.CONTAINER_REQUIRED,
    ) -> VerificationExecutionResult:
        return self.lean_verifier.verify(theorem_source, trust_policy=trust_policy)

    async def averify_lean(
        self,
        theorem_source: str,
        *,
        trust_policy: TrustPolicy | str = TrustPolicy.CONTAINER_REQUIRED,
    ) -> VerificationExecutionResult:
        return await asyncio.to_thread(
            self.verify_lean,
            theorem_source,
            trust_policy=trust_policy,
        )


def build_verification_sandbox(config: AppConfig) -> VerificationSandbox:
    """由统一 AppConfig 构建 Python/Lean 共享安全策略，避免 CLI/API 漂移。"""
    limits = SandboxLimits(
        timeout_seconds=float(config.sandbox_timeout),
        memory_mb=int(config.sandbox_memory_mb),
        cpus=float(config.sandbox_cpus),
        pids_limit=int(config.sandbox_pids_limit),
        tmpfs_mb=int(config.sandbox_tmpfs_mb),
        max_output_bytes=int(config.sandbox_max_output_bytes),
    )
    trusted_lean = tuple(config.sandbox_trusted_local_lean_command) or None
    return VerificationSandbox(
        container_python=ContainerPythonRunner(
            image=config.sandbox_python_image,
            runtime=config.sandbox_container_runtime,
            limits=limits,
        ),
        lean_verifier=LeanVerifier(
            container_image=config.sandbox_lean_image,
            runtime=config.sandbox_container_runtime,
            trusted_local_command=trusted_lean,
            limits=limits,
        ),
    )


def _validate_filename(filename: str) -> None:
    if not _SAFE_FILENAME_RE.fullmatch(filename) or Path(filename).name != filename:
        raise ValueError("filename must be a safe basename")


def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
    argv = tuple(command)
    if not argv:
        raise ValueError("command must not be empty")
    if any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
        raise ValueError("command arguments must be non-empty strings without NUL bytes")
    return argv


def _resolve_runtime(runtime: ContainerRuntime) -> tuple[str | None, str | None]:
    candidates = (
        (ContainerRuntime.DOCKER, ContainerRuntime.PODMAN)
        if runtime is ContainerRuntime.AUTO
        else (runtime,)
    )
    for candidate in candidates:
        path = shutil.which(candidate.value)
        if path:
            return path, candidate.value
    return None, None


def _resolve_local_executable(executable: str) -> str | None:
    path = Path(executable)
    if path.is_absolute():
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    return shutil.which(executable)


def _run_command(
    command: Sequence[str],
    *,
    backend: str,
    isolation: IsolationLevel,
    policy: TrustPolicy,
    limits: SandboxLimits,
    cwd: Path | None = None,
    on_timeout: Callable[[], None] | None = None,
    preexec_fn: Callable[[], None] | None = None,
) -> VerificationExecutionResult:
    """Execute argv directly while bounding captured output on read."""

    argv = list(command)
    started = time.perf_counter()
    with (
        tempfile.TemporaryFile(mode="w+b") as stdout_file,
        tempfile.TemporaryFile(mode="w+b") as stderr_file,
    ):
        try:
            completed = subprocess.run(  # noqa: S603 - argv only; shell is explicitly disabled
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                cwd=cwd,
                timeout=limits.timeout_seconds,
                check=False,
                shell=False,
                preexec_fn=preexec_fn,
            )
        except subprocess.TimeoutExpired:
            if on_timeout is not None:
                on_timeout()
            stdout, stdout_truncated = _read_output(stdout_file, limits.max_output_bytes)
            stderr, stderr_truncated = _read_output(stderr_file, limits.max_output_bytes)
            return VerificationExecutionResult(
                status=SandboxStatus.TIMED_OUT,
                backend=backend,
                isolation=isolation,
                trust_policy=policy,
                stdout=stdout,
                stderr=stderr,
                duration_ms=_elapsed_ms(started),
                command=tuple(argv),
                reason=f"execution exceeded {limits.timeout_seconds:g} seconds",
                output_truncated=stdout_truncated or stderr_truncated,
            )
        except (FileNotFoundError, PermissionError, OSError) as exc:
            return VerificationExecutionResult(
                status=SandboxStatus.UNAVAILABLE,
                backend=backend,
                isolation=IsolationLevel.NONE,
                trust_policy=policy,
                duration_ms=_elapsed_ms(started),
                command=tuple(argv),
                reason=f"execution backend unavailable: {exc}",
            )

        stdout, stdout_truncated = _read_output(stdout_file, limits.max_output_bytes)
        stderr, stderr_truncated = _read_output(stderr_file, limits.max_output_bytes)
        return VerificationExecutionResult(
            status=(SandboxStatus.SUCCEEDED if completed.returncode == 0 else SandboxStatus.FAILED),
            backend=backend,
            isolation=isolation,
            trust_policy=policy,
            stdout=stdout,
            stderr=stderr,
            exit_code=completed.returncode,
            duration_ms=_elapsed_ms(started),
            command=tuple(argv),
            output_truncated=stdout_truncated or stderr_truncated,
        )


def _read_output(stream: BinaryIO, limit: int) -> tuple[str, bool]:
    stream.flush()
    stream.seek(0)
    data = stream.read(limit + 1)
    truncated = len(data) > limit
    if truncated:
        data = data[:limit]
    text = data.decode("utf-8", errors="replace")
    if truncated:
        text += "\n[output truncated]"
    return text, truncated


def _force_remove_container(runtime_path: str, name: str, timeout: float) -> None:
    try:
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [runtime_path, "rm", "-f", name],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        # The primary result remains a timeout.  A production runtime monitor can
        # separately alert on failed cleanup without masking the verification event.
        pass


def _local_resource_limiter(limits: SandboxLimits) -> Callable[[], None] | None:
    if os.name != "posix":
        return None

    def apply_limits() -> None:  # pragma: no cover - executed between fork and exec
        import resource

        cpu_seconds = max(1, math.ceil(limits.timeout_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        # RLIMIT_AS is unreliable on macOS and can prevent the interpreter itself
        # from starting.  Linux provides useful enforcement for trusted helpers.
        if sys.platform.startswith("linux"):
            memory_bytes = limits.memory_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            except (OSError, ValueError):
                pass
        try:
            resource.setrlimit(
                resource.RLIMIT_FSIZE,
                (limits.max_output_bytes, limits.max_output_bytes),
            )
        except (OSError, ValueError):
            pass

    return apply_limits


def _lean_diagnostics(stdout: str, stderr: str) -> tuple[str, ...]:
    lines: list[str] = []
    for line in (*stdout.splitlines(), *stderr.splitlines()):
        cleaned = line.strip()
        if cleaned and _LEAN_DIAGNOSTIC_RE.search(cleaned):
            lines.append(cleaned)
        if len(lines) >= 100:
            break
    return tuple(lines)


def _unavailable_result(
    *,
    backend: str,
    policy: TrustPolicy,
    reason: str,
) -> VerificationExecutionResult:
    return VerificationExecutionResult(
        status=SandboxStatus.UNAVAILABLE,
        backend=backend,
        isolation=IsolationLevel.NONE,
        trust_policy=policy,
        reason=reason,
    )


def _last_nonempty_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


__all__ = [
    "ContainerCommandRunner",
    "ContainerPythonRunner",
    "ContainerRuntime",
    "IsolationLevel",
    "LeanVerifier",
    "SandboxLimits",
    "SandboxStatus",
    "TrustPolicy",
    "TrustedLocalPythonRunner",
    "VerificationExecutionResult",
    "VerificationSandbox",
    "build_verification_sandbox",
]
