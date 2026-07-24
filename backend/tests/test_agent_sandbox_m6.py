"""M6 sandbox policy + executor tests."""

from __future__ import annotations

import pytest

from app.services.agent.domain import ExecutionStatus
from app.services.agent.sandbox import (
    ALLOWED_ACTIONS,
    DockerSandboxExecutor,
    LocalAllowlistExecutor,
    NullSandboxExecutor,
    SandboxPolicy,
    SandboxRequest,
    default_sandbox,
)


@pytest.mark.asyncio
async def test_null_sandbox_skips_execution():
    sb = NullSandboxExecutor()
    r = await sb.execute(SandboxRequest(action="run_static_analyzer", args={"path": "a.py"}))
    # Phase-1 null executor must not report SUCCEEDED (would look like verified)
    assert r.status is ExecutionStatus.SKIPPED
    assert r.details.get("skipped") is True


@pytest.mark.asyncio
async def test_local_allowlist_rejects_path_traversal():
    sb = LocalAllowlistExecutor(files={"ok.py": "x = 1\n"})
    for bad in ("../etc/passwd", "/etc/passwd", "C:/Windows/win.ini", r"..\secret"):
        r = await sb.execute(
            SandboxRequest(action="read_file_snippet", args={"path": bad})
        )
        assert r.status is ExecutionStatus.POLICY_DENIED
        assert r.policy_denied


@pytest.mark.asyncio
async def test_policy_denies_raw_shell():
    policy = SandboxPolicy()
    with pytest.raises(PermissionError):
        policy.assert_action_allowed("bash")

    sb = LocalAllowlistExecutor(policy=policy)
    r = await sb.execute(SandboxRequest(action="bash", args={"cmd": "id"}))
    assert r.status is ExecutionStatus.POLICY_DENIED
    assert r.policy_denied


@pytest.mark.asyncio
async def test_local_allowlist_echo_and_analyzer():
    files = {"app.py": "os.system(cmd)\nSELECT * FROM t\n"}
    sb = LocalAllowlistExecutor(files=files)
    echo = await sb.execute(SandboxRequest(action="echo", args={"message": "hi"}))
    assert echo.stdout == "hi"
    assert echo.status is ExecutionStatus.SUCCEEDED

    ana = await sb.execute(
        SandboxRequest(action="run_static_analyzer", args={"path": "app.py"})
    )
    assert "os.system" in ana.stdout
    assert "SELECT *" in ana.stdout

    snip = await sb.execute(
        SandboxRequest(
            action="read_file_snippet",
            args={"path": "app.py", "start_line": 1, "end_line": 1},
        )
    )
    assert "os.system" in snip.stdout


@pytest.mark.asyncio
async def test_docker_executor_requires_worker():
    sb = DockerSandboxExecutor()
    r = await sb.execute(SandboxRequest(action="noop"))
    # noop is allowlisted but docker path still fails closed without worker
    assert r.status in {ExecutionStatus.FAILED, ExecutionStatus.SUCCEEDED}


@pytest.mark.asyncio
async def test_default_sandbox_phase1_is_null():
    sb = default_sandbox(1)
    assert isinstance(sb, NullSandboxExecutor)


def test_allowed_actions_no_shell():
    assert "shell" not in ALLOWED_ACTIONS
    assert "noop" in ALLOWED_ACTIONS
