"""How the API reaches the sandbox (ADR-003 #6).

Two backends behind one interface:

* :class:`LocalDockerBackend` — opens docker.sock in this process. Development
  only: it is exactly the arrangement ADR-003 calls unacceptable in production,
  because a compromised API then holds host Docker control.
* :class:`RemoteWorkerBackend` — sends the work to the sandbox worker over the
  internal network. The API holds no Docker client at all.

:func:`build_backend` picks the remote one whenever ``SANDBOX_WORKER_URL`` is
configured, which is the default in the shipped compose files.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


def _error(message: str) -> dict[str, Any]:
    return {
        "success": False,
        "error": message,
        "stdout": "",
        "stderr": "",
        "exit_code": -1,
    }


class SandboxBackend(Protocol):
    """What SandboxManager needs, regardless of where containers actually run."""

    name: str

    async def probe(self) -> tuple[bool, Optional[str]]:
        """Return (available, error) without executing anything."""
        ...

    async def execute(
        self,
        command: str,
        *,
        working_dir: Optional[str],
        env: Optional[dict[str, str]],
        timeout: Optional[int],
    ) -> dict[str, Any]: ...

    async def execute_tool(
        self,
        command: str,
        *,
        host_workdir: str,
        env: Optional[dict[str, str]],
        timeout: Optional[int],
        network_mode: str,
    ) -> dict[str, Any]: ...


class RemoteWorkerBackend:
    """Talks to the sandbox worker. Opens no Docker socket."""

    name = "worker"

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        self.base_url = (base_url or settings.SANDBOX_WORKER_URL or "").rstrip("/")
        self.token = token or settings.SANDBOX_WORKER_TOKEN
        self.timeout = int(getattr(settings, "SANDBOX_WORKER_TIMEOUT", 660))

    def _headers(self) -> dict[str, str]:
        return {"X-Sandbox-Token": self.token or ""}

    async def probe(self) -> tuple[bool, Optional[str]]:
        if not self.base_url:
            return False, "SANDBOX_WORKER_URL is not configured"
        if not self.token:
            # Fail closed: an unauthenticated worker call would be rejected
            # anyway, and running without a token is a misconfiguration.
            return False, "SANDBOX_WORKER_TOKEN is not configured"
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/health")
            if resp.status_code != 200:
                return False, f"worker health returned {resp.status_code}"
            body = resp.json()
            if not body.get("available"):
                return False, body.get("detail") or "worker reports docker unavailable"
            return True, None
        except Exception as exc:  # noqa: BLE001
            return False, f"sandbox worker unreachable: {exc}"

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    f"{self.base_url}{path}", json=payload, headers=self._headers()
                )
        except Exception as exc:  # noqa: BLE001
            logger.error("sandbox worker request failed: %s", exc)
            return _error(f"sandbox worker unreachable: {exc}")

        if resp.status_code != 200:
            detail = resp.text[:300]
            logger.warning("sandbox worker rejected request: %s %s", resp.status_code, detail)
            return _error(f"sandbox worker returned {resp.status_code}: {detail}")

        body = resp.json()
        return {
            "success": bool(body.get("success")),
            "stdout": body.get("stdout") or "",
            "stderr": body.get("stderr") or "",
            "exit_code": int(body.get("exit_code", -1)),
            "error": body.get("error"),
        }

    async def execute(
        self,
        command: str,
        *,
        working_dir: Optional[str] = None,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> dict[str, Any]:
        return await self._post(
            "/execute",
            {
                "command": command,
                "working_dir": working_dir,
                "env": env or {},
                "timeout": timeout,
            },
        )

    async def execute_tool(
        self,
        command: str,
        *,
        host_workdir: str,
        env: Optional[dict[str, str]] = None,
        timeout: Optional[int] = None,
        network_mode: str = "none",
    ) -> dict[str, Any]:
        return await self._post(
            "/execute-tool",
            {
                "command": command,
                "workdir": host_workdir,
                "env": env or {},
                "timeout": timeout,
                "network": network_mode,
            },
        )


def build_backend() -> Optional[SandboxBackend]:
    """Return the remote backend when a worker is configured, else None.

    ``None`` means "fall back to the in-process Docker client", which
    SandboxManager keeps for local development.
    """
    if getattr(settings, "SANDBOX_WORKER_URL", None):
        return RemoteWorkerBackend()
    return None
