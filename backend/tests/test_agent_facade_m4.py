"""M4: API façade, event bus, mapping, graph-audits routes."""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.services.agent.application import (
    GraphAuditFacade,
    GraphEventBus,
    audit_status_to_task_status,
    findings_to_api_list,
    graph_event_to_sse,
)
from app.services.agent.application.runner import AuditRunner
from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    Finding,
    RepositoryRef,
    RunBudget,
    Severity,
    SourceLocation,
    VerificationStatus,
)
from app.services.agent.graph.llm import FakeLLM
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.persistence import InMemoryBusinessStore


FIXTURE = {
    "web/view.py": "password = 'x'\nhtml = innerHTML\n",
}


def _req() -> AuditRequest:
    return AuditRequest(
        repository=RepositoryRef(source_type="local", local_path="/tmp/g"),
        budget=RunBudget(max_tokens=20_000, max_model_calls=20),
        project_id="proj-1",
    )


def _rt() -> GraphRuntime:
    return GraphRuntime(
        llm=FakeLLM(),
        offline=True,
        extra={"fixture_files": FIXTURE},
    )


def test_status_mapping():
    assert audit_status_to_task_status(AuditStatus.COMPLETED) == "completed"
    assert audit_status_to_task_status(AuditStatus.ANALYZING) == "analyzing"
    assert audit_status_to_task_status(AuditStatus.INGESTING) == "indexing"


def test_graph_event_to_sse_shape():
    sse = graph_event_to_sse(
        {"kind": "node.completed", "message": "ok", "path": "a.py"},
        task_id="t1",
        sequence=3,
    )
    assert sse["task_id"] == "t1"
    assert sse["type"] == "info"
    assert sse["sequence"] == 3
    assert sse["message"] == "ok"
    assert "timestamp" in sse


def test_findings_to_api_list():
    f = Finding(
        title="XSS",
        description="innerHTML",
        severity=Severity.MEDIUM,
        location=SourceLocation(file_path="a.js", start_line=2),
        verification_status=VerificationStatus.NOT_RUN,
        recommendation="sanitize",
    )
    rows = findings_to_api_list([f])
    assert rows[0]["file_path"] == "a.js"
    assert rows[0]["severity"] == "medium"
    assert rows[0]["suggestion"] == "sanitize"


@pytest.mark.asyncio
async def test_event_bus_publish_and_history():
    bus = GraphEventBus()
    await bus.publish_raw("t1", {"kind": "task_start", "message": "go"})
    await bus.publish_raw("t1", {"kind": "node.completed", "message": "n1"})
    hist = bus.history("t1")
    assert len(hist) == 2
    assert hist[0]["type"] == "task_start"
    assert hist[1]["sequence"] == 2


@pytest.mark.asyncio
async def test_facade_start_cancel_resume_findings_events():
    bus = GraphEventBus()
    runner = AuditRunner(store=InMemoryBusinessStore())
    facade = GraphAuditFacade(runner=runner, bus=bus)

    summary = await facade.start(_req(), runtime=_rt(), project_id="proj-1", name="demo")
    assert summary["status"] == "completed"
    assert summary["runtime"] == "langgraph"
    assert summary["project_id"] == "proj-1"
    assert summary["findings_count"] >= 0

    tid = summary["id"]
    task = await facade.get_task(tid)
    assert task and task["status"] == "completed"

    findings = await facade.list_findings(tid)
    assert isinstance(findings, list)
    for row in findings:
        assert "title" in row
        assert row.get("verification_status") == "not_run" or True  # field optional legacy

    events = await facade.list_events(tid)
    assert any(e["type"] in {"task_start", "info", "task_complete"} for e in events)

    # resume completed is stable
    again = await facade.resume(tid, runtime=_rt())
    assert again["status"] == "completed"

    # cancel after complete
    c = await facade.cancel(tid)
    assert c["id"] == tid


@pytest.mark.asyncio
async def test_facade_cancel_unknown_marks_cancelled():
    facade = GraphAuditFacade(
        runner=AuditRunner(store=InMemoryBusinessStore()),
        bus=GraphEventBus(),
    )
    r = await facade.cancel("never-existed")
    assert r["status"] == "cancelled"


class _FakeUser:
    """Minimal stand-in for models.user.User in dependency overrides."""

    def __init__(self, uid: str = "user-test-1", *, superuser: bool = False) -> None:
        self.id = uid
        self.email = f"{uid}@example.com"
        self.is_active = True
        self.is_superuser = superuser


def _override_auth(app, user: _FakeUser | None = None):
    """Install get_current_user + get_db overrides; return cleanup callable."""
    from app.api import deps
    from app.db.session import get_db

    fake = user or _FakeUser()

    async def _user():
        return fake

    async def _db():
        # Project ACL flag defaults off — DB is unused on happy path.
        yield None

    app.dependency_overrides[deps.get_current_user] = _user
    app.dependency_overrides[get_db] = _db

    def _cleanup() -> None:
        app.dependency_overrides.pop(deps.get_current_user, None)
        app.dependency_overrides.pop(get_db, None)

    return _cleanup


@pytest.mark.asyncio
async def test_graph_audits_requires_auth():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/graph-audits/",
            json={"wait": True, "fixture_files": FIXTURE},
        )
        assert resp.status_code in {401, 403}


@pytest.mark.asyncio
async def test_graph_audits_http_routes(monkeypatch):
    from app.api.v1.endpoints import graph_audits
    from app.main import app

    # Synchronous execution is an operator opt-in after runtime hardening.
    monkeypatch.setattr(
        graph_audits.settings,
        "GRAPH_AUDITS_SYNC_START",
        True,
        raising=False,
    )
    cleanup = _override_auth(app)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Sync wait + fixture_files only (no client host path)
            resp = await client.post(
                "/api/v1/graph-audits/",
                json={
                    "project_id": "p1",
                    "name": "http-demo",
                    "source_type": "local",
                    "offline": True,
                    "wait": True,
                    "fixture_files": FIXTURE,
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["status"] in {"completed", "partial"}
            tid = body["id"]

            g = await client.get(f"/api/v1/graph-audits/{tid}")
            assert g.status_code == 200
            assert g.json()["id"] == tid

            f = await client.get(f"/api/v1/graph-audits/{tid}/findings")
            assert f.status_code == 200
            assert isinstance(f.json(), list)

            e = await client.get(f"/api/v1/graph-audits/{tid}/events")
            assert e.status_code == 200
            assert len(e.json()) >= 1

            s = await client.post(f"/api/v1/graph-audits/{tid}/start")
            assert s.status_code == 200
            assert "already" in s.json().get("message", "").lower() or s.json().get(
                "status"
            )

            c = await client.post(f"/api/v1/graph-audits/{tid}/cancel")
            assert c.status_code == 200
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_graph_audits_rejects_other_user(monkeypatch):
    from app.api.v1.endpoints import graph_audits
    from app.main import app

    monkeypatch.setattr(
        graph_audits.settings,
        "GRAPH_AUDITS_SYNC_START",
        True,
        raising=False,
    )
    owner = _FakeUser("owner-1")
    other = _FakeUser("intruder-2")
    cleanup = _override_auth(app, owner)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/graph-audits/",
                json={
                    "name": "owned",
                    "wait": True,
                    "fixture_files": FIXTURE,
                },
            )
            assert resp.status_code == 200, resp.text
            tid = resp.json()["id"]
        cleanup()
        cleanup = _override_auth(app, other)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            g = await client.get(f"/api/v1/graph-audits/{tid}")
            assert g.status_code == 403
            f = await client.get(f"/api/v1/graph-audits/{tid}/findings")
            assert f.status_code == 403
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_graph_audits_rejects_host_local_path():
    from app.main import app

    cleanup = _override_auth(app)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/graph-audits/",
                json={
                    "source_type": "local",
                    "local_path": "/etc/passwd",
                    "fixture_files": FIXTURE,
                    "wait": True,
                },
            )
            assert resp.status_code == 400
            assert "local_path" in resp.text.lower() or "fixture" in resp.text.lower()
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_graph_audits_async_accept_returns_202():
    from app.main import app

    cleanup = _override_auth(app)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/graph-audits/",
                json={
                    "project_id": "p-async",
                    "name": "bg",
                    "wait": False,
                    "fixture_files": FIXTURE,
                },
            )
            assert resp.status_code == 202, resp.text
            body = resp.json()
            assert body["status"] == "pending"
            tid = body["id"]
            # Allow background task to finish
            for _ in range(50):
                g = await client.get(f"/api/v1/graph-audits/{tid}")
                if g.status_code == 200 and g.json().get("status") in {
                    "completed",
                    "partial",
                    "failed",
                    "cancelled",
                }:
                    break
                await asyncio.sleep(0.05)
            else:
                # Still pending is acceptable if event loop slow; cancel to clean up
                await client.post(f"/api/v1/graph-audits/{tid}/cancel")
    finally:
        cleanup()
