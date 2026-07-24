"""Sandbox executor protocol + hardened docker policy (M6).

Phase 1: no untrusted execution on happy path.
Phase 2: allowlisted high-level actions only (no raw shell from model).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from app.services.agent.domain import ExecutionStatus


# High-level actions the model / verification may request (never raw shell)
ALLOWED_ACTIONS = frozenset(
    {
        "run_static_analyzer",
        "run_selected_test",
        "read_file_snippet",
        "list_workspace",
        "noop",
        "echo",
    }
)


@dataclass
class SandboxPolicy:
    """Hardened defaults (ADR-003)."""

    network: str = "none"
    read_only_root: bool = True
    user: str = "nobody"
    cap_drop: list[str] = field(default_factory=lambda: ["ALL"])
    no_new_privileges: bool = True
    memory_mb: int = 512
    cpu_quota: float = 1.0
    pids_limit: int = 64
    timeout_seconds: int = 60
    max_output_bytes: int = 256_000
    allow_install_scripts: bool = False
    allow_network: bool = False
    allow_raw_shell: bool = False
    allowed_actions: frozenset[str] = field(default_factory=lambda: ALLOWED_ACTIONS)

    def assert_action_allowed(self, action: str) -> None:
        if action not in self.allowed_actions:
            raise PermissionError(f"action not allowlisted: {action}")
        if action in {"shell", "exec", "bash"} and not self.allow_raw_shell:
            raise PermissionError("raw shell forbidden by policy")


@dataclass
class SandboxRequest:
    action: str
    args: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: Optional[int] = None
    workspace_hint: Optional[str] = None


@dataclass
class SandboxResult:
    status: ExecutionStatus
    action: str
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    duration_ms: int = 0
    policy_denied: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SandboxExecutor(Protocol):
    async def execute(self, request: SandboxRequest) -> SandboxResult: ...


class NullSandboxExecutor:
    """Phase-1 default: never runs untrusted code."""

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        try:
            self.policy.assert_action_allowed(request.action)
        except PermissionError as e:
            return SandboxResult(
                status=ExecutionStatus.POLICY_DENIED,
                action=request.action,
                stderr=str(e),
                policy_denied=True,
            )
        if request.action == "noop":
            return SandboxResult(
                status=ExecutionStatus.SUCCEEDED,
                action=request.action,
                stdout="noop",
                exit_code=0,
                duration_ms=0,
            )
        # All other actions: not implemented in Phase 1 null executor.
        # SKIPPED (not SUCCEEDED) so verification never treats this as confirmed.
        return SandboxResult(
            status=ExecutionStatus.SKIPPED,
            action=request.action,
            stdout="",
            stderr="null sandbox: execution skipped (phase1)",
            exit_code=0,
            details={"skipped": True, "reason": "phase1_null"},
        )


class LocalAllowlistExecutor:
    """Dev/test executor for allowlisted actions without Docker.

    Still forbids raw shell. ``echo`` and ``read_file_snippet`` are simulated.
    """

    def __init__(
        self,
        policy: Optional[SandboxPolicy] = None,
        *,
        files: Optional[dict[str, str]] = None,
    ) -> None:
        self.policy = policy or SandboxPolicy()
        self.files = files or {}

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        t0 = time.perf_counter()
        try:
            self.policy.assert_action_allowed(request.action)
        except PermissionError as e:
            return SandboxResult(
                status=ExecutionStatus.POLICY_DENIED,
                action=request.action,
                stderr=str(e),
                policy_denied=True,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        timeout = request.timeout_seconds or self.policy.timeout_seconds
        try:
            return await asyncio.wait_for(
                self._dispatch(request, t0), timeout=timeout
            )
        except asyncio.TimeoutError:
            return SandboxResult(
                status=ExecutionStatus.TIMED_OUT,
                action=request.action,
                stderr="timeout",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

    async def _dispatch(self, request: SandboxRequest, t0: float) -> SandboxResult:
        action = request.action
        if action == "noop":
            out = "noop"
        elif action == "echo":
            out = str(request.args.get("message") or "")
        elif action == "list_workspace":
            out = "\n".join(sorted(self.files.keys()))
        elif action == "read_file_snippet":
            path = str(request.args.get("path") or "").replace("\\", "/")
            if (
                not path
                or path.startswith("/")
                or ".." in path.split("/")
                or (len(path) > 1 and path[1] == ":")
            ):
                return SandboxResult(
                    status=ExecutionStatus.POLICY_DENIED,
                    action=action,
                    stderr=f"unsafe path: {path}",
                    policy_denied=True,
                    exit_code=1,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )
            if path not in self.files:
                return SandboxResult(
                    status=ExecutionStatus.FAILED,
                    action=action,
                    stderr=f"file not found: {path}",
                    exit_code=1,
                    duration_ms=int((time.perf_counter() - t0) * 1000),
                )
            content = self.files[path]
            start = int(request.args.get("start_line") or 1)
            end = int(request.args.get("end_line") or start + 20)
            lines = content.splitlines()
            snippet = "\n".join(lines[max(0, start - 1) : end])
            out = snippet[: self.policy.max_output_bytes]
        elif action == "run_static_analyzer":
            # Simulated analyzer: report pattern hits
            path = str(request.args.get("path") or "")
            content = self.files.get(path, "")
            hits = []
            for needle in ("eval(", "os.system", "innerHTML", "SELECT *"):
                if needle in content:
                    hits.append(needle)
            out = "hits=" + ",".join(hits) if hits else "clean"
        elif action == "run_selected_test":
            out = "tests skipped (local allowlist simulator)"
        else:
            return SandboxResult(
                status=ExecutionStatus.POLICY_DENIED,
                action=action,
                stderr="unhandled action",
                policy_denied=True,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

        return SandboxResult(
            status=ExecutionStatus.SUCCEEDED,
            action=action,
            stdout=out[: self.policy.max_output_bytes],
            exit_code=0,
            duration_ms=int((time.perf_counter() - t0) * 1000),
        )


class DockerSandboxExecutor:
    """Placeholder for worker-side Docker execution (M6 direction).

    Does **not** mount docker.sock from the API process in production targets.
    Raises if docker client is unavailable; tests use LocalAllowlistExecutor.
    """

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()

    async def execute(self, request: SandboxRequest) -> SandboxResult:
        try:
            self.policy.assert_action_allowed(request.action)
        except PermissionError as e:
            return SandboxResult(
                status=ExecutionStatus.POLICY_DENIED,
                action=request.action,
                stderr=str(e),
                policy_denied=True,
            )
        # Explicit: API process should not open docker.sock here.
        return SandboxResult(
            status=ExecutionStatus.FAILED,
            action=request.action,
            stderr=(
                "DockerSandboxExecutor requires sandbox worker (ADR-003); "
                "use LocalAllowlistExecutor or NullSandboxExecutor in API process"
            ),
            exit_code=-1,
            details={"worker_required": True},
        )


def default_sandbox(phase: int = 1) -> SandboxExecutor:
    if phase <= 1:
        return NullSandboxExecutor()
    return LocalAllowlistExecutor()
