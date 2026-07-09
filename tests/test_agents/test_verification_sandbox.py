"""Policy and hardening tests for the QGen verification sandbox."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO, cast

import pytest

import sciforge.agents.qgen.verification_sandbox as sandbox_module
from sciforge.agents.qgen.verification_sandbox import (
    ContainerCommandRunner,
    ContainerPythonRunner,
    ContainerRuntime,
    IsolationLevel,
    LeanVerifier,
    SandboxLimits,
    SandboxStatus,
    TrustedLocalPythonRunner,
    TrustPolicy,
    VerificationSandbox,
)


def test_trusted_local_python_requires_explicit_policy() -> None:
    runner = TrustedLocalPythonRunner()

    result = runner.run("raise RuntimeError('must not run')", trust_policy="container_required")

    assert result.status is SandboxStatus.POLICY_DENIED
    assert result.isolation is IsolationLevel.NONE
    assert result.command == ()
    assert "explicit trusted_local" in result.reason


def test_trusted_local_python_executes_only_after_opt_in() -> None:
    runner = TrustedLocalPythonRunner(limits=SandboxLimits(timeout_seconds=3, memory_mb=512))

    result = runner.run("print(6 * 7)", trust_policy=TrustPolicy.TRUSTED_LOCAL)

    assert result.status is SandboxStatus.SUCCEEDED
    assert result.isolation is IsolationLevel.TRUSTED_PROCESS
    assert result.trust_policy is TrustPolicy.TRUSTED_LOCAL
    assert result.stdout.strip() == "42"
    assert result.command[1:3] == ("-I", "-B")


def test_container_runtime_absence_is_explicit_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_module.shutil, "which", lambda _name: None)

    def must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("subprocess must not run when no OCI runtime exists")

    monkeypatch.setattr(sandbox_module.subprocess, "run", must_not_run)
    result = ContainerPythonRunner().run("print('untrusted')")

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.available is False
    assert result.isolation is IsolationLevel.NONE
    assert result.backend == "container:auto"
    assert "runtime unavailable" in result.reason


def test_container_python_uses_restricted_argv_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        sandbox_module.shutil,
        "which",
        lambda name: "/opt/bin/docker" if name == "docker" else None,
    )

    def fake_run(
        argv: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append((list(argv), dict(kwargs)))
        cast(BinaryIO, kwargs["stdout"]).write(b"ALL_PASSED\n")
        return subprocess.CompletedProcess(list(argv), 0)

    monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
    limits = SandboxLimits(
        timeout_seconds=7,
        memory_mb=384,
        cpus=0.5,
        pids_limit=23,
        tmpfs_mb=17,
    )

    result = ContainerPythonRunner(
        image="registry.example/verify-python:2026.07",
        limits=limits,
    ).run("print('ALL_PASSED')")

    assert result.status is SandboxStatus.SUCCEEDED
    assert result.isolation is IsolationLevel.RESTRICTED_CONTAINER
    assert result.trust_policy is TrustPolicy.CONTAINER_REQUIRED
    assert result.stdout == "ALL_PASSED\n"
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert isinstance(argv, list)
    assert kwargs["shell"] is False
    assert _option(argv, "--network") == "none"
    assert "--read-only" in argv
    assert _option(argv, "--cap-drop") == "ALL"
    assert _option(argv, "--security-opt") == "no-new-privileges"
    assert _option(argv, "--pids-limit") == "23"
    assert _option(argv, "--memory") == "384m"
    assert _option(argv, "--cpus") == "0.5"
    assert _option(argv, "--user") == "65534:65534"
    assert _option(argv, "--pull") == "never"
    assert _option(argv, "--ipc") == "none"
    assert _option(argv, "--tmpfs").startswith("/tmp:rw,noexec,nosuid,nodev,size=17m")
    assert "readonly" in _option(argv, "--mount")
    image_index = argv.index("registry.example/verify-python:2026.07")
    assert argv[image_index + 1 :] == [
        "python",
        "-I",
        "-B",
        "/workspace/verify.py",
    ]


def test_container_timeout_attempts_force_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sandbox_module.shutil,
        "which",
        lambda name: "/opt/bin/podman" if name == "podman" else None,
    )

    def fake_run(
        argv: Sequence[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        command = list(argv)
        calls.append(command)
        if len(command) > 1 and command[1] == "run":
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
    runner = ContainerPythonRunner(
        runtime=ContainerRuntime.PODMAN,
        limits=SandboxLimits(timeout_seconds=1),
    )

    result = runner.run("while True: pass")

    assert result.status is SandboxStatus.TIMED_OUT
    assert result.timed_out is True
    assert result.isolation is IsolationLevel.RESTRICTED_CONTAINER
    assert len(calls) == 2
    container_name = _option(calls[0], "--name")
    assert calls[1] == ["/opt/bin/podman", "rm", "-f", container_name]


def test_container_setup_exit_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_module.shutil, "which", lambda _name: "/usr/bin/docker")

    def fake_run(
        argv: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        cast(BinaryIO, kwargs["stderr"]).write(b"image not present locally\n")
        return subprocess.CompletedProcess(list(argv), 125)

    monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)

    result = ContainerPythonRunner(runtime=ContainerRuntime.DOCKER).run("print(1)")

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.exit_code == 125
    assert result.isolation is IsolationLevel.RESTRICTED_CONTAINER
    assert result.reason == "image not present locally"


def test_container_runner_rejects_option_like_image_and_unsafe_filename() -> None:
    with pytest.raises(ValueError, match="OCI image"):
        ContainerCommandRunner(image="--privileged")

    runner = ContainerCommandRunner(image="python:3.11")
    with pytest.raises(ValueError, match="safe basename"):
        runner.run_source("x", filename="../escape.py", command=("python", "x"))


def test_lean_defaults_to_container_without_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(name: str) -> str | None:
        return "/usr/local/bin/lean" if name == "lean" else None

    monkeypatch.setattr(sandbox_module.shutil, "which", fake_which)

    def must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("configured local Lean must not be an implicit fallback")

    monkeypatch.setattr(sandbox_module.subprocess, "run", must_not_run)
    verifier = LeanVerifier(trusted_local_command=("lean",))

    result = verifier.verify("theorem identity (n : Nat) : n = n := rfl")

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.trust_policy is TrustPolicy.CONTAINER_REQUIRED
    assert result.isolation is IsolationLevel.NONE
    assert result.backend == "container:auto"


def test_lean_trusted_local_command_compiles_exact_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "theorem identity (n : Nat) : n = n := rfl"
    observed_source = ""
    observed_argv: list[str] = []
    monkeypatch.setattr(
        sandbox_module.shutil,
        "which",
        lambda name: "/opt/lean/bin/lean" if name == "lean" else None,
    )

    def fake_run(
        argv: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal observed_source, observed_argv
        observed_argv = list(argv)
        observed_source = Path(observed_argv[-1]).read_text(encoding="utf-8")
        cast(BinaryIO, kwargs["stderr"]).write(b"Main.lean:1:0: error: type mismatch\n")
        return subprocess.CompletedProcess(observed_argv, 1)

    monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
    verifier = LeanVerifier(trusted_local_command=("lean",))

    result = verifier.verify(source, trust_policy=TrustPolicy.TRUSTED_LOCAL)

    assert observed_source == source
    assert observed_argv[0] == "/opt/lean/bin/lean"
    assert observed_argv[-1].endswith("Main.lean")
    assert result.status is SandboxStatus.FAILED
    assert result.isolation is IsolationLevel.TRUSTED_PROCESS
    assert result.diagnostics == ("Main.lean:1:0: error: type mismatch",)


def test_lean_trusted_local_requires_configured_command() -> None:
    result = LeanVerifier().verify(
        "theorem identity (n : Nat) : n = n := rfl",
        trust_policy=TrustPolicy.TRUSTED_LOCAL,
    )

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.isolation is IsolationLevel.NONE
    assert "not explicitly configured" in result.reason


def test_lean_rejects_successful_compile_with_sorry_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sandbox_module.shutil,
        "which",
        lambda name: "/opt/lean/bin/lean" if name == "lean" else None,
    )

    def fake_run(
        argv: Sequence[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        cast(BinaryIO, kwargs["stderr"]).write(
            b"Main.lean:1:0: warning: declaration uses 'sorry'\n"
        )
        return subprocess.CompletedProcess(list(argv), 0)

    monkeypatch.setattr(sandbox_module.subprocess, "run", fake_run)
    verifier = LeanVerifier(trusted_local_command=("lean",))

    result = verifier.verify(
        "theorem bad : True := by sorry",
        trust_policy=TrustPolicy.TRUSTED_LOCAL,
    )

    assert result.exit_code == 0
    assert result.status is SandboxStatus.FAILED
    assert "admitted proof" in result.reason
    assert any("sorry" in line for line in result.diagnostics)


@pytest.mark.asyncio
async def test_facade_defaults_python_to_container_and_supports_async_trusted_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sandbox_module.shutil, "which", lambda _name: None)
    facade = VerificationSandbox()

    unavailable = await facade.arun_python("print('generated')")
    trusted = await facade.arun_python(
        "print('trusted')",
        trust_policy=TrustPolicy.TRUSTED_LOCAL,
    )

    assert unavailable.status is SandboxStatus.UNAVAILABLE
    assert unavailable.isolation is IsolationLevel.NONE
    assert trusted.status is SandboxStatus.SUCCEEDED
    assert trusted.stdout.strip() == "trusted"
    assert trusted.to_dict()["trust_policy"] == "trusted_local"


def test_limits_reject_nonpositive_values() -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        SandboxLimits(timeout_seconds=0)
    with pytest.raises(ValueError, match="pids_limit"):
        SandboxLimits(pids_limit=0)


def _option(argv: Sequence[str], name: str) -> str:
    return argv[argv.index(name) + 1]
