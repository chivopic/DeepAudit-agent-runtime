"""Regression tests for runtime trust-boundary hardening."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import graph_audits
from app.services.agent.application.runner import AuditRunner
from app.services.agent.domain import AuditRequest, AuditStatus, RepositoryRef, RunBudget
from app.services.agent.path_security import (
    WorkspacePathError,
    normalize_relative_workspace_path,
    resolve_under_workspace,
)
from app.services.agent.tools.file_tool import FileReadTool, FileSearchTool, ListFilesTool
from app.services.agent.tools.run_code import ExtractFunctionTool


def test_relative_workspace_path_rejects_host_and_traversal_paths():
    bad = (
        "../secret.py",
        "a/../../secret.py",
        "/etc/passwd",
        "~/secret",
        "C:/Windows/win.ini",
        r"..\secret.py",
    )
    for candidate in bad:
        with pytest.raises(WorkspacePathError):
            normalize_relative_workspace_path(candidate)

    assert normalize_relative_workspace_path("./src/app.py") == "src/app.py"
    assert normalize_relative_workspace_path(".") == "."


def test_resolve_under_workspace_blocks_prefix_collision(tmp_path: Path):
    root = tmp_path / "repo"
    sibling = tmp_path / "repo-secret"
    root.mkdir()
    sibling.mkdir()
    (sibling / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")

    with pytest.raises(WorkspacePathError):
        resolve_under_workspace(root, "../repo-secret/secret.py")


def test_resolve_under_workspace_blocks_symlink_escape(tmp_path: Path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 1\n", encoding="utf-8")

    link = root / "linked.py"
    try:
        link.symlink_to(outside / "secret.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(WorkspacePathError):
        resolve_under_workspace(root, "linked.py")


@pytest.mark.asyncio
async def test_file_tools_fail_closed_on_workspace_escape(tmp_path: Path):
    root = tmp_path / "repo"
    sibling = tmp_path / "repo-secret"
    root.mkdir()
    sibling.mkdir()
    (root / "ok.py").write_text("needle = True\n", encoding="utf-8")
    (sibling / "secret.py").write_text("needle = 'secret'\n", encoding="utf-8")

    read = FileReadTool(str(root))
    read_bad = await read._execute(file_path="../repo-secret/secret.py")
    assert not read_bad.success
    assert "安全错误" in (read_bad.error or "")

    read_ok = await read._execute(file_path="ok.py")
    assert read_ok.success
    assert "needle" in str(read_ok.data)

    search = FileSearchTool(str(root))
    search_bad = await search._execute(
        keyword="needle",
        directory="../repo-secret",
    )
    assert not search_bad.success
    assert "安全错误" in (search_bad.error or "")

    listing = ListFilesTool(str(root))
    list_bad = await listing._execute(directory="../repo-secret")
    assert not list_bad.success
    assert "安全错误" in (list_bad.error or "")


@pytest.mark.asyncio
async def test_extract_function_cannot_read_outside_project(tmp_path: Path):
    root = tmp_path / "repo"
    sibling = tmp_path / "repo-secret"
    root.mkdir()
    sibling.mkdir()
    (root / "ok.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    (sibling / "secret.py").write_text(
        "def secret():\n    return 'secret'\n",
        encoding="utf-8",
    )

    tool = ExtractFunctionTool(project_root=str(root))
    denied = await tool._execute(
        file_path="../repo-secret/secret.py",
        function_name="secret",
    )
    assert not denied.success
    assert "安全错误" in (denied.error or "")

    allowed = await tool._execute(file_path="ok.py", function_name="ok")
    assert allowed.success
    assert "def ok" in str(allowed.data)


@pytest.mark.asyncio
async def test_graph_audit_rejects_client_local_path(monkeypatch):
    body = graph_audits.GraphAuditStartRequest(
        local_path="server/workspace",
        fixture_files={"app.py": "x = 1\n"},
    )
    user = SimpleNamespace(id="user-1", is_superuser=False)

    with pytest.raises(HTTPException) as exc:
        await graph_audits.start_graph_audit(body, current_user=user, db=SimpleNamespace())
    assert exc.value.status_code == 400
    assert "local_path" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_graph_audit_wait_respects_operator_flag(monkeypatch):
    monkeypatch.setattr(graph_audits.settings, "GRAPH_AUDITS_SYNC_START", False, raising=False)
    body = graph_audits.GraphAuditStartRequest(
        fixture_files={"app.py": "x = 1\n"},
        wait=True,
    )
    user = SimpleNamespace(id="user-1", is_superuser=False)

    with pytest.raises(HTTPException) as exc:
        await graph_audits.start_graph_audit(body, current_user=user, db=SimpleNamespace())
    assert exc.value.status_code == 400
    assert "GRAPH_AUDITS_SYNC_START=false" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_runner_enforces_wall_clock_budget():
    class SlowGraph:
        async def ainvoke(self, state, config):
            await asyncio.sleep(2)
            return {"status": AuditStatus.COMPLETED}

    request = AuditRequest(
        repository=RepositoryRef(
            source_type="local",
            local_path="fixture://runtime-security-test",
        ),
        budget=RunBudget(max_duration_seconds=1),
    )
    runner = AuditRunner()
    runner._compiled = SlowGraph()

    result = await runner.run(request)

    assert result.status is AuditStatus.FAILED
    assert any(event.get("kind") == "task.timeout" for event in result.events)


def test_default_compose_does_not_mount_docker_socket():
    repo_root = Path(__file__).resolve().parents[2]
    for name in (
        "docker-compose.yml",
        "docker-compose.prod.yml",
        "docker-compose.prod.cn.yml",
    ):
        text = (repo_root / name).read_text(encoding="utf-8")
        assert "/var/run/docker.sock" not in text
        assert "SANDBOX_ENABLED=false" in text

    legacy = (repo_root / "docker-compose.legacy-sandbox.yml").read_text(encoding="utf-8")
    assert "/var/run/docker.sock" in legacy
    assert "SECURITY WARNING" in legacy
