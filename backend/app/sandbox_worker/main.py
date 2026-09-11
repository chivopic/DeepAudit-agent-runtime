"""Sandbox worker service (ADR-003 #6).

Runs as its own container, holds docker.sock, and exposes exactly two routes.
The API talks to it over the internal compose network and keeps no Docker
client of its own, so compromising the API no longer yields host Docker
control — only the ability to spend sandbox capacity under this service's
policy.

Run with:  uvicorn app.sandbox_worker.main:app --host 0.0.0.0 --port 8900
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Depends, FastAPI, Header, HTTPException, status

from app.core.config import settings
from app.sandbox_worker.contract import (
    SandboxExecRequest,
    SandboxExecResponse,
    SandboxHealth,
    SandboxToolExecRequest,
)
from app.sandbox_worker.executor import DockerExecutor

logger = logging.getLogger(__name__)

app = FastAPI(
    title="DeepAudit Sandbox Worker",
    description="Owns docker.sock so the API does not have to (ADR-003).",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

_executor = DockerExecutor()


async def require_worker_token(
    x_sandbox_token: str | None = Header(default=None),
) -> None:
    """Shared-secret auth.

    The worker must never be reachable from outside the internal network, but
    defence in depth is cheap: without a configured token the service refuses
    to execute anything rather than running open.
    """
    expected = getattr(settings, "SANDBOX_WORKER_TOKEN", None)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sandbox worker has no SANDBOX_WORKER_TOKEN configured",
        )
    if not x_sandbox_token or not hmac.compare_digest(x_sandbox_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid sandbox worker token",
        )


@app.get("/health", response_model=SandboxHealth)
async def health() -> SandboxHealth:
    """Unauthenticated: reports reachability only, never executes anything."""
    return _executor.health()


@app.post(
    "/execute",
    response_model=SandboxExecResponse,
    dependencies=[Depends(require_worker_token)],
)
async def execute(req: SandboxExecRequest) -> SandboxExecResponse:
    logger.info("sandbox exec label=%s timeout=%s", req.label, req.timeout)
    return await _executor.execute(req)


@app.post(
    "/execute-tool",
    response_model=SandboxExecResponse,
    dependencies=[Depends(require_worker_token)],
)
async def execute_tool(req: SandboxToolExecRequest) -> SandboxExecResponse:
    """Run a tool against a workspace directory, jailed to the workspace root."""
    logger.info(
        "sandbox tool exec label=%s workdir=%s network=%s",
        req.label,
        req.workdir,
        req.network,
    )
    return await _executor.execute_tool(req)
