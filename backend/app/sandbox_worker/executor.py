"""Container execution. This module is the only thing that touches docker.sock.

It runs inside the sandbox worker container, never inside the API (ADR-003 #6).
Every security-relevant container setting is decided here from server config;
nothing in :class:`SandboxExecRequest` can change it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any, Optional

from app.core.config import settings
from app.sandbox_worker.contract import (
    SandboxExecRequest,
    SandboxExecResponse,
    SandboxHealth,
    SandboxToolExecRequest,
)

logger = logging.getLogger(__name__)

STDOUT_LIMIT = 10_000
STDERR_LIMIT = 2_000

# Proxy variables are blanked so a proxy injected into the worker's environment
# cannot give an offline container a route out.
_NO_PROXY_ENV = {
    "HTTP_PROXY": "",
    "HTTPS_PROXY": "",
    "http_proxy": "",
    "https_proxy": "",
    "NO_PROXY": "*",
    "no_proxy": "*",
}


def _cap_drop() -> list[str]:
    raw = str(getattr(settings, "SANDBOX_CAP_DROP", "ALL") or "ALL")
    if raw.strip().upper() == "NONE":
        return []
    return [c.strip() for c in raw.split(",") if c.strip()]


class WorkspaceJailError(PermissionError):
    """Requested workdir is outside the configured workspace root."""


def resolve_workspace(path: str) -> str:
    """Resolve ``path`` and prove it stays inside SANDBOX_WORKSPACE_ROOT.

    Symlinks are resolved before the comparison, so a symlink planted inside
    the workspace cannot point the mount at the host filesystem.
    """
    root = str(getattr(settings, "SANDBOX_WORKSPACE_ROOT", "/tmp/deepaudit") or "")
    real_root = os.path.realpath(root)
    real_path = os.path.realpath(path)
    if real_path != real_root and not real_path.startswith(real_root + os.sep):
        raise WorkspaceJailError(
            f"workdir is outside the sandbox workspace root ({root})"
        )
    if not os.path.isdir(real_path):
        raise WorkspaceJailError("workdir does not exist in the sandbox worker")
    return real_path


class DockerExecutor:
    """Owns the Docker client and enforces the container policy."""

    def __init__(self) -> None:
        self._client: Optional[Any] = None
        self._error: Optional[str] = None

    def _ensure_client(self) -> Optional[Any]:
        if self._client is not None:
            return self._client
        try:
            import docker

            client = docker.from_env()
            client.ping()
            self._client = client
            self._error = None
        except Exception as exc:  # noqa: BLE001 — surfaced through /health
            self._error = str(exc)
            logger.warning("sandbox worker cannot reach docker: %s", exc)
        return self._client

    def health(self) -> SandboxHealth:
        client = self._ensure_client()
        if client is None:
            return SandboxHealth(available=False, detail=self._error)
        try:
            version = client.version().get("Version")
        except Exception as exc:  # noqa: BLE001
            return SandboxHealth(available=False, detail=str(exc))
        return SandboxHealth(
            available=True,
            docker_version=version,
            image=settings.SANDBOX_IMAGE,
        )

    def _container_config(self, req: SandboxExecRequest, temp_dir: str) -> dict[str, Any]:
        """Build the container spec. Only command/working_dir/env come from the
        request; everything security-relevant comes from server settings."""
        env = {**_NO_PROXY_ENV, **dict(req.env)}
        config: dict[str, Any] = {
            "image": settings.SANDBOX_IMAGE,
            "command": ["sh", "-c", req.command],
            "detach": True,
            "mem_limit": settings.SANDBOX_MEMORY_LIMIT,
            "cpu_period": 100_000,
            "cpu_quota": int(100_000 * float(settings.SANDBOX_CPU_LIMIT)),
            "network_mode": settings.SANDBOX_NETWORK_MODE,
            "user": getattr(settings, "SANDBOX_USER", "1000:1000"),
            "read_only": True,
            "privileged": False,
            "pid_mode": None,
            "volumes": {temp_dir: {"bind": "/workspace", "mode": "rw"}},
            "tmpfs": {
                "/home/sandbox": "rw,size=100m,mode=1777",
                "/tmp": "rw,size=100m,mode=1777",
            },
            "working_dir": req.working_dir or "/workspace",
            "environment": env,
        }
        caps = _cap_drop()
        if caps:
            config["cap_drop"] = caps
        if getattr(settings, "SANDBOX_NO_NEW_PRIVILEGES", True):
            config["security_opt"] = ["no-new-privileges:true"]
        return config

    async def _run(
        self, client: Any, config: dict[str, Any], timeout: int
    ) -> SandboxExecResponse:
        """Start the container, collect its output, and always remove it."""
        container = await asyncio.to_thread(client.containers.run, **config)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(container.wait), timeout=timeout
            )
            stdout = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
            stderr = await asyncio.to_thread(container.logs, stdout=False, stderr=True)
            code = int(result.get("StatusCode", -1))
            return SandboxExecResponse(
                success=code == 0,
                stdout=stdout.decode("utf-8", errors="ignore")[:STDOUT_LIMIT],
                stderr=stderr.decode("utf-8", errors="ignore")[:STDERR_LIMIT],
                exit_code=code,
            )
        except asyncio.TimeoutError:
            await asyncio.to_thread(container.kill)
            return SandboxExecResponse(
                error=f"执行超时 ({timeout}秒)", details={"timeout": timeout}
            )
        finally:
            await asyncio.to_thread(container.remove, force=True)

    async def execute(self, req: SandboxExecRequest) -> SandboxExecResponse:
        client = self._ensure_client()
        if client is None:
            return SandboxExecResponse(
                error=f"docker unavailable in sandbox worker: {self._error}",
            )

        timeout = int(req.timeout or getattr(settings, "SANDBOX_TIMEOUT", 60))
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                config = self._container_config(req, temp_dir)
                return await self._run(client, config, timeout)
        except Exception as exc:  # noqa: BLE001
            logger.error("sandbox worker execution error: %s", exc)
            return SandboxExecResponse(error=str(exc))

    async def execute_tool(self, req: SandboxToolExecRequest) -> SandboxExecResponse:
        """Run a tool command against a jailed workspace directory."""
        client = self._ensure_client()
        if client is None:
            return SandboxExecResponse(
                error=f"docker unavailable in sandbox worker: {self._error}",
            )

        try:
            host_workdir = resolve_workspace(req.workdir)
        except WorkspaceJailError as exc:
            logger.warning("sandbox worker refused workdir %r: %s", req.workdir, exc)
            return SandboxExecResponse(error=str(exc), details={"policy_denied": True})

        if req.network != "none" and not bool(
            getattr(settings, "SANDBOX_ALLOW_NETWORK", False)
        ):
            return SandboxExecResponse(
                error="network access requires SANDBOX_ALLOW_NETWORK (ADR-003 #8)",
                details={"policy_denied": True},
            )

        timeout = int(req.timeout or getattr(settings, "SANDBOX_TIMEOUT", 60))
        # Belt and braces: strip proxy vars inside the container too.
        unset = (
            "unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY "
            "all_proxy 2>/dev/null; "
        )

        try:
            config = self._container_config(
                SandboxExecRequest(
                    command=unset + req.command,
                    timeout=req.timeout,
                    env=req.env,
                    label=req.label,
                ),
                temp_dir=host_workdir,
            )
            # Project code is mounted read-only; scratch space stays tmpfs.
            config["volumes"] = {host_workdir: {"bind": "/workspace", "mode": "ro"}}
            config["network_mode"] = req.network
            return await self._run(client, config, timeout)
        except Exception as exc:  # noqa: BLE001
            logger.error("sandbox worker tool execution error: %s", exc)
            return SandboxExecResponse(error=str(exc))
