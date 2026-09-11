"""Wire contract between the API and the sandbox worker (ADR-003 #6).

Deliberately narrow. The client may ask *what* to run and for how long; it may
never influence *how* the container is built — image, mounts, network,
capabilities, user and limits are decided worker-side. A compromised API can
therefore spend sandbox capacity, but cannot reach the host through it.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

# Environment variables the client is allowed to set inside the container.
# Anything that steers the runtime's loader or proxy configuration is refused.
_ENV_DENY_PREFIXES = (
    "LD_",
    "DYLD_",
    "DOCKER",
    "PATH",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "NODE_OPTIONS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "http_proxy",
    "https_proxy",
)

MAX_COMMAND_CHARS = 8000
MAX_ENV_VARS = 32


class SandboxExecRequest(BaseModel):
    """What the API is permitted to ask for."""

    command: str = Field(..., min_length=1, max_length=MAX_COMMAND_CHARS)
    timeout: Optional[int] = Field(default=None, ge=1, le=600)
    working_dir: Optional[str] = None
    env: dict[str, str] = Field(default_factory=dict)
    # Opaque label for logs/tracing only — never used to build the container.
    label: Optional[str] = Field(default=None, max_length=120)

    @field_validator("working_dir")
    @classmethod
    def working_dir_must_be_inside_workspace(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not v.startswith("/workspace"):
            raise ValueError("working_dir must be under /workspace")
        if ".." in v.split("/"):
            raise ValueError("working_dir must not traverse upwards")
        return v

    @field_validator("env")
    @classmethod
    def env_must_be_benign(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > MAX_ENV_VARS:
            raise ValueError(f"at most {MAX_ENV_VARS} env vars")
        for key in v:
            if not key or not key.replace("_", "").isalnum():
                raise ValueError(f"invalid env var name: {key!r}")
            if key.upper().startswith(_ENV_DENY_PREFIXES) or key.startswith(
                _ENV_DENY_PREFIXES
            ):
                raise ValueError(f"env var not allowed: {key}")
        return v


class SandboxToolExecRequest(SandboxExecRequest):
    """Run a tool against a workspace directory that already exists on the host.

    ``workdir`` is jailed worker-side to SANDBOX_WORKSPACE_ROOT. Without that
    jail a compromised API could ask the worker to mount ``/`` into a container
    and read the host — re-opening most of the blast radius this service exists
    to close.

    ``network`` is likewise an allowlist, not a passthrough: ADR-003 puts
    network access behind human approval.
    """

    workdir: str = Field(..., min_length=1)
    network: str = Field(default="none")

    @field_validator("workdir")
    @classmethod
    def workdir_must_be_absolute(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith("/"):
            raise ValueError("workdir must be an absolute host path")
        if ".." in v.split("/"):
            raise ValueError("workdir must not traverse upwards")
        return v

    @field_validator("network")
    @classmethod
    def network_must_be_known(cls, v: str) -> str:
        v = (v or "none").strip().lower()
        if v not in {"none", "bridge"}:
            raise ValueError("network must be 'none' or 'bridge'")
        return v


class SandboxExecResponse(BaseModel):
    """Mirrors the result dict shape SandboxManager already returns to callers."""

    success: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    error: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class SandboxHealth(BaseModel):
    available: bool
    docker_version: Optional[str] = None
    image: Optional[str] = None
    detail: Optional[str] = None
