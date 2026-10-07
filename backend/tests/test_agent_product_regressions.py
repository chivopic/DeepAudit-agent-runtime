"""Regressions from the 2026-10-02 product acceptance report."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest

from app.services.agent.application import product_audit
from app.services.agent.application.project_source import (
    ProjectSourceError,
    load_authorized_snapshot,
)
from app.services.agent.domain import ModelUsage
from app.services.agent.event_manager import AgentEventEmitter, EventManager
from app.services.agent.graph.llm import LLMResponse
from app.services.agent.persistence.control import FileControlPlane, LeaseBusy


class Gateway:
    provider = "test"
    model = "test"

    def __init__(self):
        self.paths = []
        self.active = self.peak = 0

    async def complete(self, messages, **kwargs):
        body = json.loads(messages[-1].content)
        if "path" in body:
            self.paths.append(body["path"])
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0)
            self.active -= 1
        return LLMResponse(
            content="[]",
            usage=ModelUsage(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                call_count=1,
            ),
        )


@pytest.fixture
def gateway(monkeypatch):
    instance = Gateway()
    monkeypatch.setattr(product_audit, "_gateway_from_user_config", lambda config: (instance, None))
    return instance


def test_snapshot_rejects_file_and_directory_symlinks(tmp_path):
    root, outside = tmp_path / "project", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "outside.py").write_text("fixture_marker = 'outside'\n")
    (root / "ok.py").write_text("ok = True\n")
    (root / "linked.py").symlink_to(outside / "outside.py")
    (root / "linked_dir").symlink_to(outside, target_is_directory=True)
    snapshot = load_authorized_snapshot(root)
    assert snapshot.files == {"ok.py": "ok = True\n"}
    assert {issue.reason for issue in snapshot.issues} == {"symlink", "symlink_directory"}
    selected = load_authorized_snapshot(root, target_files=["linked_dir/outside.py"])
    assert selected.files == {}
    assert selected.issues[0].reason == "symlink"


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("*.py", {"asset.js"}),
        ("**/*.py", {"asset.js"}),
        ("src", {"root.py", "asset.js"}),
        ("src/", {"root.py", "asset.js"}),
        ("src/**", {"root.py", "asset.js"}),
        ("src/a.py", {"root.py", "src/b.py", "asset.js"}),
        ("src/?.py", {"root.py", "asset.js"}),
    ],
)
def test_snapshot_exclude_patterns(tmp_path, pattern, expected):
    (tmp_path / "src").mkdir()
    for rel in ("root.py", "src/a.py", "src/b.py", "asset.js"):
        (tmp_path / rel).write_text("x = 1\n")
    snapshot = load_authorized_snapshot(tmp_path, exclude_patterns=[pattern])
    assert set(snapshot.files) == expected
    assert snapshot.discovered_files == len(expected)
    assert not snapshot.issues


def test_snapshot_reports_limits_missing_files_and_encoding(tmp_path):
    for rel in ("a.py", "b.py", "c.py"):
        (tmp_path / rel).write_text("x = 1\n")
    capped = load_authorized_snapshot(tmp_path, max_files=2)
    assert capped.discovered_files == 3
    assert capped.issues[0].model_dump() == {"path": "c.py", "reason": "file_budget"}
    capped = load_authorized_snapshot(tmp_path, max_bytes=7)
    assert capped.discovered_files == 3
    assert len(capped.files) == 1
    assert {issue.reason for issue in capped.issues} == {"byte_budget"}
    missing = load_authorized_snapshot(tmp_path, target_files=["missing.py"])
    assert missing.files == {}
    assert missing.discovered_files == 1
    assert missing.issues[0].reason == "missing_file"
    empty = load_authorized_snapshot(tmp_path, target_files=[])
    assert empty.files == {} and empty.discovered_files == 0
    (tmp_path / "bad.py").write_bytes(b"\xff\xfe")
    invalid = load_authorized_snapshot(tmp_path, target_files=["bad.py"])
    assert invalid.files == {}
    assert invalid.issues[0].reason == "invalid_encoding"


@pytest.mark.parametrize("path", ["../a.py", "/tmp/a.py", "C:\\a.py", ".", ""])
def test_snapshot_rejects_invalid_targets(tmp_path, path):
    with pytest.raises(ProjectSourceError):
        load_authorized_snapshot(tmp_path, target_files=[path])


@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "limit"), [(3, 2), (101, 100)])
async def test_product_reports_true_scope_when_capped(tmp_path, gateway, count, limit):
    root = tmp_path / "project"
    root.mkdir()
    for index in range(count):
        (root / f"file_{index:03}.py").write_text("x = 1\n")
    summary = await product_audit.run_product_graph_audit(
        task_id="capped",
        project_root=root,
        user_config={},
        state_dir=tmp_path / "state",
        max_files=limit,
    )
    assert summary["status"] == "partial"
    assert summary["files_total"] == count
    assert summary["files_analyzed"] == limit
    assert len(summary["coverage"]["omitted_units"]) == count - limit
    assert "incomplete" in summary["summary"]
    assert gateway.peak == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("targets", [[], ["missing.py"]])
async def test_empty_snapshot_never_falls_back_to_checkout(tmp_path, gateway, targets):
    root = tmp_path / "project"
    root.mkdir()
    (root / "unselected.py").write_text("x = eval(user_input)\n")
    summary = await product_audit.run_product_graph_audit(
        task_id="empty",
        project_root=root,
        user_config={},
        state_dir=tmp_path / "state",
        target_files=targets,
    )
    assert summary["files_analyzed"] == 0
    assert summary["findings"] == [] and gateway.paths == []
    if targets:
        assert summary["status"] == "partial"
        assert summary["coverage"]["omitted_units"][0]["reason"] == "missing_file"


@pytest.mark.asyncio
async def test_product_byte_limit_and_configuration_files(tmp_path, gateway):
    root = tmp_path / "project"
    root.mkdir()
    (root / "a.json").write_text('{"a":1}')
    (root / "b.py").write_text("x = 'too large'\n")
    summary = await product_audit.run_product_graph_audit(
        task_id="bytes",
        project_root=root,
        user_config={},
        state_dir=tmp_path / "state",
        max_bytes=8,
    )
    assert summary["status"] == "partial"
    assert summary["files_total"] == 2 and summary["files_analyzed"] == 1
    assert gateway.paths == ["a.json"]
    assert summary["coverage"]["omitted_units"][0]["reason"] == "byte_budget"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        'def constant():\n    return eval("1 + 1")\n',
        'cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))\n',
    ],
)
async def test_static_patterns_are_never_confirmed(tmp_path, gateway, content):
    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text(content)
    summary = await product_audit.run_product_graph_audit(
        task_id="patterns",
        project_root=root,
        user_config={},
        state_dir=tmp_path / "state",
        graph_verification=True,
    )
    assert summary["findings"]
    assert all(not item["is_verified"] for item in summary["findings"])
    assert "0 confirmed" in summary["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "settings",
    [
        {"checkpoint_backend": "postgres"},
        {"control_backend": "redis"},
        {"checkpoint_backend": "memory"},
        {"control_backend": "invalid"},
    ],
)
async def test_product_honors_backend_settings(tmp_path, gateway, settings):
    with pytest.raises(RuntimeError, match="AGENT_"):
        await product_audit.run_product_graph_audit(
            task_id="settings",
            project_root=tmp_path,
            user_config={},
            state_dir=tmp_path / "state",
            **settings,
        )
    assert not (tmp_path / "state").exists()
    assert gateway.paths == []


_WORKER = r"""
import asyncio, json, subprocess, sys
from pathlib import Path
from app.services.agent.application import product_audit
from app.services.agent.application.runner import AuditRunner
from app.services.agent.domain import ModelUsage
from app.services.agent.graph.llm import LLMResponse
from app.services.agent.persistence.control import FileControlPlane
root, state, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
seen, progress = [], []
class Gateway:
    async def complete(self, messages, **kwargs):
        payload = json.loads(messages[-1].content)
        if "path" in payload:
            seen.append(payload)
            if mode == "cancel":
                subprocess.run([sys.executable, "-c",
                    "import sys; from app.services.agent.persistence.control import FileControlPlane; "
                    "FileControlPlane(sys.argv[1]).request_cancel('recovery')",
                    str(state / "control")], check=True, timeout=10)
        return LLMResponse(content="[]", usage=ModelUsage(input_tokens=1, output_tokens=1,
                           total_tokens=2, call_count=1))
class Sink:
    async def emit_info(self, *args):
        pass
    async def emit_progress(self, current, total, message):
        progress.append(current)
product_audit._gateway_from_user_config = lambda config: (Gateway(), None)
if mode == "start":
    original = AuditRunner.run
    async def pause(self, *args, **kwargs):
        kwargs["stop_when"] = lambda values: getattr(values.get("budget"), "files_analyzed", 0) >= 1
        return await original(self, *args, **kwargs)
    AuditRunner.run = pause
async def main():
    result = await product_audit.run_product_graph_audit(task_id="recovery", project_root=root,
        user_config={}, state_dir=state, resume=mode != "start", event_sink=Sink())
    print(json.dumps({"result": result, "seen": seen, "progress": progress}))
asyncio.run(main())
"""


def run_worker(root, state, mode):
    completed = subprocess.run(
        [sys.executable, "-c", _WORKER, str(root), str(state), mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("mode", ["resume", "cancel"])
def test_separate_process_resume_uses_saved_source_and_streams(tmp_path, mode):
    root, state = tmp_path / "project", tmp_path / "state"
    root.mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (root / name).write_text("marker = 'REVISION_A'\n")
    first = run_worker(root, state, "start")
    assert first["result"]["status"] == "paused"
    assert [item["path"] for item in first["seen"]] == ["a.py"]
    for path in root.iterdir():
        path.write_text("marker = 'REVISION_B'\n")
    continued = run_worker(root, state, mode)
    assert continued["progress"]
    assert continued["result"]["source_snapshot_hash"] == first["result"]["source_snapshot_hash"]
    assert all("REVISION_A" in item["content"] for item in continued["seen"])
    expected = ["b.py"] if mode == "cancel" else ["b.py", "c.py"]
    assert [item["path"] for item in continued["seen"]] == expected
    assert continued["result"]["status"] == ("cancelled" if mode == "cancel" else "completed")


def test_resume_does_not_require_a_live_checkout(tmp_path):
    root, state = tmp_path / "project", tmp_path / "state"
    root.mkdir()
    for name in ("a.py", "b.py"):
        (root / name).write_text("x = 1\n")
    run_worker(root, state, "start")
    for path in root.iterdir():
        path.unlink()
    root.rmdir()
    continued = run_worker(root, state, "resume")
    assert continued["result"]["status"] == "completed"
    assert [item["path"] for item in continued["seen"]] == ["b.py"]


def test_live_owner_cannot_be_stolen_after_expiry(tmp_path):
    plane = FileControlPlane(tmp_path)
    plane.acquire("owned", "first", ttl_seconds=-1)
    script = """
import sys
from app.services.agent.persistence.control import FileControlPlane, LeaseBusy
try:
    FileControlPlane(sys.argv[1]).acquire("owned", "second")
except LeaseBusy:
    sys.exit(0)
sys.exit(1)
"""
    try:
        child = subprocess.run([sys.executable, "-c", script, str(tmp_path)], timeout=10)
        assert child.returncode == 0
        with pytest.raises(LeaseBusy):
            FileControlPlane(tmp_path).acquire("owned", "first")
        plane.renew("owned", "first")
    finally:
        plane.release("owned", "first")
    second = FileControlPlane(tmp_path)
    second.acquire("owned", "second")
    second.release("owned", "second")


@pytest.mark.asyncio
async def test_shared_event_replay_keeps_sequence_across_runners(tmp_path):
    first = EventManager(event_log=FileControlPlane(tmp_path))
    await AgentEventEmitter("events", first).emit_progress(1, 3, "first")
    second = EventManager(event_log=FileControlPlane(tmp_path))
    emitter = AgentEventEmitter("events", second)
    await emitter.emit_progress(2, 3, "second")
    await emitter.emit_task_complete(0, 1000, "done")
    reader = EventManager(event_log=FileControlPlane(tmp_path))
    events = [event async for event in reader.stream_events("events", after_sequence=1)]
    assert [event["sequence"] for event in events] == [2, 3]
    assert events[0]["metadata"]["current"] == 2
    # Reconnecting after the last event must terminate rather than idle forever.
    resumed = [event async for event in reader.stream_events("events", after_sequence=3)]
    assert resumed == []


def test_event_log_recovers_an_interrupted_final_write(tmp_path):
    plane = FileControlPlane(tmp_path)
    plane.append_event("events", {"event_type": "info", "message": "saved"})
    with (tmp_path / "events" / "events.jsonl").open("a") as handle:
        handle.write('{"event_type":')
    assert len(plane.read_events("events")) == 1
    assert plane.append_event("events", {"event_type": "info", "message": "next"}) == 2
    assert [item["message"] for item in plane.read_events("events")] == ["saved", "next"]


@pytest.mark.asyncio
async def test_api_lease_contention_does_not_fail_the_owner(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.api.v1.endpoints import agent_tasks

    async def busy(**kwargs):
        raise LeaseBusy("already running")

    monkeypatch.setattr(product_audit, "run_product_graph_audit", busy)
    task = SimpleNamespace(
        agent_config={"engine": "graph"},
        target_files=None,
        exclude_patterns=None,
        token_budget=1000,
        status="running",
    )
    db, emitter = AsyncMock(), AsyncMock()
    await agent_tasks._execute_graph_product(
        db=db,
        task=task,
        task_id="busy",
        project_root=str(tmp_path),
        user_config={},
        event_emitter=emitter,
        resume=True,
    )
    assert task.status == "running"
    db.commit.assert_not_awaited()
    emitter.emit_error.assert_not_awaited()


@pytest.mark.asyncio
async def test_modified_source_artifact_is_rejected(tmp_path, gateway):
    root, state = tmp_path / "project", tmp_path / "state"
    root.mkdir()
    for name in ("a.py", "b.py"):
        (root / name).write_text("x = 1\n")
    run_worker(root, state, "start")
    artifact = next((state / "artifacts" / "recovery").glob("*.json"))
    artifact.write_text(artifact.read_text().replace("x = 1", "x = 2"))
    with pytest.raises(ProjectSourceError, match="快照校验失败"):
        await product_audit.run_product_graph_audit(
            task_id="recovery",
            project_root=root,
            user_config={},
            state_dir=state,
            resume=True,
        )
    assert gateway.paths == []


@pytest.mark.asyncio
async def test_missing_checkpoint_never_restarts_a_paused_audit(tmp_path, gateway):
    root, state = tmp_path / "project", tmp_path / "state"
    root.mkdir()
    for name in ("a.py", "b.py"):
        (root / name).write_text("x = 1\n")
    run_worker(root, state, "start")
    (state / "checkpoints" / "recovery.pkl").unlink()
    summary = await product_audit.run_product_graph_audit(
        task_id="recovery",
        project_root=root,
        user_config={},
        state_dir=state,
        resume=True,
    )
    assert summary["status"] == "failed"
    assert "checkpoint is missing" in summary["error"]
    assert summary["files_analyzed"] == 1
    assert gateway.paths == []


@pytest.mark.asyncio
async def test_unrelated_pattern_cannot_confirm_a_finding():
    from app.services.agent.domain import Finding, Severity, SourceLocation, VerificationRequest
    from app.services.agent.graph.subgraphs import verify_one
    from app.services.agent.sandbox import LocalAllowlistExecutor

    finding = Finding(
        title="SQL injection",
        description="unrelated finding",
        severity=Severity.HIGH,
        location=SourceLocation(file_path="app.py", start_line=1),
        confidence=0.4,
    )
    verified = await verify_one(
        finding,
        request=VerificationRequest(
            finding_id=finding.id, strategy="sandbox", allow_execution=True
        ),
        sandbox=LocalAllowlistExecutor(files={"app.py": 'eval("1 + 1")\n'}),
    )
    assert verified.verification_status.value == "inconclusive"
    assert verified.confidence == finding.confidence


@pytest.mark.asyncio
async def test_api_graph_cancel_is_shared_without_killing_other_agents(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.api.v1.endpoints import agent_tasks
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_STATE_DIR", str(tmp_path))
    task = SimpleNamespace(project_id="project", status="running", agent_config={"engine": "graph"})
    db = AsyncMock()
    db.get.side_effect = [task, SimpleNamespace(owner_id="user")]
    running = MagicMock()
    monkeypatch.setitem(agent_tasks._running_asyncio_tasks, "shared-cancel", running)
    try:
        await agent_tasks.cancel_agent_task("shared-cancel", db, SimpleNamespace(id="user"))
        assert FileControlPlane(tmp_path / "control").is_cancelled("shared-cancel")
        assert task.status == "cancelled"
        running.cancel.assert_not_called()
    finally:
        agent_tasks._cancelled_tasks.discard("shared-cancel")


@pytest.mark.asyncio
async def test_duplicate_worker_leaves_existing_state_and_stream_alone(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.api.v1.endpoints import agent_tasks
    from app.core.config import settings

    monkeypatch.setattr(settings, "AGENT_STATE_DIR", str(tmp_path))
    plane = FileControlPlane(tmp_path / "control")
    plane.acquire("busy-outer", "original")
    task = SimpleNamespace(
        project=SimpleNamespace(), status="running", agent_config={"engine": "graph"}
    )
    db = AsyncMock()
    db.get.return_value = task
    session = MagicMock()
    session.return_value.__aenter__.return_value = db
    monkeypatch.setattr(agent_tasks, "async_session_factory", session)
    original_stream = object()
    monkeypatch.setitem(agent_tasks._running_event_managers, "busy-outer", original_stream)
    try:
        await agent_tasks._execute_agent_task("busy-outer", resume=True)
        assert task.status == "running"
        assert agent_tasks._running_event_managers["busy-outer"] is original_stream
        db.commit.assert_not_awaited()
    finally:
        plane.release("busy-outer", "original")


@pytest.mark.asyncio
async def test_product_coverage_reaches_downloaded_reports(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.api.v1.endpoints import agent_tasks
    from app.models.agent_task import AgentTask

    coverage = {"omitted_units": [{"path": "missing.py", "reason": "file_budget"}]}
    result = {
        "status": "partial",
        "task_status": "partial",
        "findings": [],
        "files_total": 3,
        "files_analyzed": 2,
        "tokens": 10,
        "duration_ms": 20,
        "coverage": coverage,
        "source_snapshot_hash": "a" * 64,
        "summary": "partial",
    }
    monkeypatch.setattr(product_audit, "run_product_graph_audit", AsyncMock(return_value=result))
    monkeypatch.setattr(agent_tasks, "_save_findings", AsyncMock(return_value=0))
    task = AgentTask(
        id="report",
        project_id="project",
        status="running",
        agent_config={"engine": "graph"},
        total_iterations=0,
        tool_calls_count=0,
        tokens_used=0,
        token_budget=1000,
    )
    db = AsyncMock()
    await agent_tasks._execute_graph_product(
        db=db,
        task=task,
        task_id=task.id,
        project_root=str(tmp_path),
        user_config={},
        event_emitter=AsyncMock(),
        resume=True,
    )
    assert task.security_score is None
    assert task.agent_config["graph_result"]["coverage"] == coverage
    # Graph findings are saved against the pinned artifact, not a mutable checkout.
    agent_tasks._save_findings.assert_awaited_once_with(db, task.id, [])
    findings = MagicMock()
    findings.scalars.return_value.all.return_value = []
    db.execute.return_value = findings
    project, user = SimpleNamespace(owner_id="user", name="demo"), SimpleNamespace(id="user")
    db.get.side_effect = [task, project, task, project]
    exported = await agent_tasks.generate_audit_report(task.id, "json", db, user)
    assert exported["coverage"] == coverage
    assert exported["summary"]["total_files"] == 3
    assert exported["report_metadata"]["source_snapshot_hash"] == "a" * 64
    markdown = await agent_tasks.generate_audit_report(task.id, "markdown", db, user)
    text = markdown.body.decode()
    assert "missing.py" in text and "file_budget" in text
    assert "已分析 2 / 3" in text
    assert "本次审计未完整完成" in text
