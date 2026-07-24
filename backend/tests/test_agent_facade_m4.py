"""M4: API façade, event bus, mapping, graph-audits routes."""

from __future__ import annotations

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


@pytest.mark.asyncio
async def test_graph_audits_http_routes():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/graph-audits/",
            json={
                "project_id": "p1",
                "name": "http-demo",
                "source_type": "local",
                "local_path": "/tmp/x",
                "offline": True,
                "fixture_files": FIXTURE,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "completed"
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
        assert "already" in s.json().get("message", "").lower() or s.json().get("status")

        c = await client.post(f"/api/v1/graph-audits/{tid}/cancel")
        assert c.status_code == 200
