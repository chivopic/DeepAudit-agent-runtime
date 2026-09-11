"""Sandbox worker boundary (ADR-003 #6).

The worker exists so the API does not hold docker.sock. These tests pin down
the properties that make that worth doing: the client cannot choose the image,
the mount, the network or the privileges, and it cannot escape the workspace.
"""

from __future__ import annotations

import os

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.sandbox_worker.contract import (
    SandboxExecRequest,
    SandboxToolExecRequest,
)
from app.sandbox_worker.executor import (
    DockerExecutor,
    WorkspaceJailError,
    resolve_workspace,
)
from app.sandbox_worker.main import app as worker_app
from app.services.agent.tools.sandbox_backend import (
    RemoteWorkerBackend,
    build_backend,
)

# ---------------------------------------------------------------------------
# The client cannot influence how the container is built
# ---------------------------------------------------------------------------


def test_request_has_no_image_or_volume_fields():
    """If these ever appear, the worker stops being a boundary."""
    forbidden = {"image", "volumes", "privileged", "user", "cap_drop", "pid_mode"}
    assert forbidden.isdisjoint(SandboxExecRequest.model_fields)
    assert forbidden.isdisjoint(SandboxToolExecRequest.model_fields)


def test_container_config_comes_from_settings_not_the_request(monkeypatch):
    from app.sandbox_worker import executor as ex

    monkeypatch.setattr(ex.settings, "SANDBOX_IMAGE", "pinned/image:1", raising=False)
    config = DockerExecutor()._container_config(
        SandboxExecRequest(command="echo hi"), temp_dir="/tmp/ws"
    )

    assert config["image"] == "pinned/image:1"
    assert config["network_mode"] == "none"
    assert config["read_only"] is True
    assert config["privileged"] is False
    assert config["security_opt"] == ["no-new-privileges:true"]
    assert config["volumes"] == {"/tmp/ws": {"bind": "/workspace", "mode": "rw"}}


@pytest.mark.parametrize(
    "name",
    ["LD_PRELOAD", "PATH", "PYTHONPATH", "http_proxy", "HTTPS_PROXY", "NODE_OPTIONS"],
)
def test_loader_and_proxy_env_are_refused(name):
    with pytest.raises(ValidationError):
        SandboxExecRequest(command="echo hi", env={name: "x"})


def test_benign_env_is_accepted():
    assert SandboxExecRequest(command="echo hi", env={"MY_FLAG": "1"}).env == {
        "MY_FLAG": "1"
    }


@pytest.mark.parametrize("bad", ["/etc", "relative/path", "/workspace/../etc"])
def test_working_dir_must_stay_in_workspace(bad):
    with pytest.raises(ValidationError):
        SandboxExecRequest(command="echo hi", working_dir=bad)


@pytest.mark.parametrize("bad", ["relative", "/tmp/../etc"])
def test_tool_workdir_must_be_absolute_and_untraversed(bad):
    with pytest.raises(ValidationError):
        SandboxToolExecRequest(command="ls", workdir=bad)


@pytest.mark.parametrize("bad", ["host", "container:x", "NONE "])
def test_network_is_an_allowlist(bad):
    if bad.strip().lower() in {"none", "bridge"}:
        return
    with pytest.raises(ValidationError):
        SandboxToolExecRequest(command="ls", workdir="/w", network=bad)


# ---------------------------------------------------------------------------
# Workspace jail
# ---------------------------------------------------------------------------


def test_workspace_jail_allows_paths_inside_root(tmp_path, monkeypatch):
    from app.sandbox_worker import executor as ex

    monkeypatch.setattr(
        ex.settings, "SANDBOX_WORKSPACE_ROOT", str(tmp_path), raising=False
    )
    inner = tmp_path / "proj"
    inner.mkdir()
    assert resolve_workspace(str(inner)) == os.path.realpath(str(inner))


def test_workspace_jail_refuses_paths_outside_root(tmp_path, monkeypatch):
    from app.sandbox_worker import executor as ex

    monkeypatch.setattr(
        ex.settings, "SANDBOX_WORKSPACE_ROOT", str(tmp_path), raising=False
    )
    with pytest.raises(WorkspaceJailError):
        resolve_workspace("/etc")


def test_workspace_jail_refuses_symlink_escape(tmp_path, monkeypatch):
    """A symlink planted inside the workspace must not redirect the mount."""
    from app.sandbox_worker import executor as ex

    monkeypatch.setattr(
        ex.settings, "SANDBOX_WORKSPACE_ROOT", str(tmp_path), raising=False
    )
    link = tmp_path / "escape"
    os.symlink("/etc", link)
    with pytest.raises(WorkspaceJailError):
        resolve_workspace(str(link))


# ---------------------------------------------------------------------------
# Worker routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_requires_a_token(monkeypatch):
    from app.sandbox_worker import main as worker_main

    monkeypatch.setattr(
        worker_main.settings, "SANDBOX_WORKER_TOKEN", "right", raising=False
    )
    transport = ASGITransport(app=worker_app)
    async with AsyncClient(transport=transport, base_url="http://w") as c:
        assert (await c.post("/execute", json={"command": "id"})).status_code == 401
        r = await c.post(
            "/execute", json={"command": "id"}, headers={"X-Sandbox-Token": "wrong"}
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_worker_refuses_to_run_without_a_configured_token(monkeypatch):
    """Fail closed: no token configured means no execution, not open access."""
    from app.sandbox_worker import main as worker_main

    monkeypatch.setattr(
        worker_main.settings, "SANDBOX_WORKER_TOKEN", None, raising=False
    )
    transport = ASGITransport(app=worker_app)
    async with AsyncClient(transport=transport, base_url="http://w") as c:
        r = await c.post(
            "/execute", json={"command": "id"}, headers={"X-Sandbox-Token": "anything"}
        )
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_health_needs_no_token_and_never_executes():
    transport = ASGITransport(app=worker_app)
    async with AsyncClient(transport=transport, base_url="http://w") as c:
        r = await c.get("/health")
    assert r.status_code == 200
    assert "available" in r.json()


# ---------------------------------------------------------------------------
# API-side backend selection
# ---------------------------------------------------------------------------


def test_backend_is_local_when_no_worker_configured(monkeypatch):
    from app.services.agent.tools import sandbox_backend as sb

    monkeypatch.setattr(sb.settings, "SANDBOX_WORKER_URL", None, raising=False)
    assert build_backend() is None


def test_backend_is_remote_when_worker_configured(monkeypatch):
    from app.services.agent.tools import sandbox_backend as sb

    monkeypatch.setattr(
        sb.settings, "SANDBOX_WORKER_URL", "http://sandbox-worker:8900", raising=False
    )
    assert isinstance(build_backend(), RemoteWorkerBackend)


@pytest.mark.asyncio
async def test_remote_backend_fails_closed_without_a_token(monkeypatch):
    from app.services.agent.tools import sandbox_backend as sb

    monkeypatch.setattr(sb.settings, "SANDBOX_WORKER_TOKEN", None, raising=False)
    available, error = await RemoteWorkerBackend(base_url="http://w:8900").probe()
    assert available is False
    assert "TOKEN" in (error or "")


@pytest.mark.asyncio
async def test_sandbox_manager_opens_no_docker_client_when_worker_configured(monkeypatch):
    """The whole point: with a worker configured the API holds no socket."""
    from app.services.agent.tools import sandbox_backend as sb
    from app.services.agent.tools.sandbox_tool import SandboxManager

    monkeypatch.setattr(
        sb.settings, "SANDBOX_WORKER_URL", "http://sandbox-worker:8900", raising=False
    )
    monkeypatch.setattr(sb.settings, "SANDBOX_WORKER_TOKEN", "t", raising=False)

    manager = SandboxManager()
    assert isinstance(manager._backend, RemoteWorkerBackend)

    async def unreachable():
        return False, "sandbox worker unreachable: refused"

    monkeypatch.setattr(manager._backend, "probe", unreachable)
    await manager.initialize()

    assert manager._docker_client is None
    assert manager.is_available is False
    assert "sandbox worker" in manager.get_diagnosis()


@pytest.mark.asyncio
async def test_execution_is_refused_when_the_worker_is_down(monkeypatch):
    """Fail closed rather than silently falling back to a local socket."""
    from app.services.agent.tools import sandbox_backend as sb
    from app.services.agent.tools.sandbox_tool import SandboxManager

    monkeypatch.setattr(
        sb.settings, "SANDBOX_WORKER_URL", "http://sandbox-worker:8900", raising=False
    )
    monkeypatch.setattr(sb.settings, "SANDBOX_WORKER_TOKEN", "t", raising=False)

    manager = SandboxManager()

    async def unreachable():
        return False, "down"

    monkeypatch.setattr(manager._backend, "probe", unreachable)
    await manager.initialize()

    result = await manager.execute_command("echo hi")
    assert result["success"] is False
    assert manager._docker_client is None
