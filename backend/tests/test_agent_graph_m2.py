"""M2: LangGraph audit skeleton + FakeLLM tests."""

from __future__ import annotations

import json

import pytest

from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    RepositoryRef,
    RunBudget,
    Severity,
    VerificationStatus,
)
from app.services.agent.graph import (
    FakeLLM,
    compile_audit_graph,
    empty_audit_state,
)
from app.services.agent.graph.llm import LLMMessage
from app.services.agent.graph.runtime import GraphRuntime, reset_runtime, set_runtime


FIXTURE_FILES = {
    "app/auth.py": (
        "def login(user, password):\n"
        "    # bad: hardcoded\n"
        "    password = 'supersecret'\n"
        "    q = 'SELECT * FROM users WHERE name=' + user\n"
        "    return q\n"
    ),
    "app/utils.py": (
        "import os\n"
        "def run(cmd):\n"
        "    os.system(cmd)\n"
        "    return True\n"
    ),
    "README.md": "# not source\n",
}


def _request(**kwargs) -> AuditRequest:
    defaults = dict(
        repository=RepositoryRef(source_type="local", local_path="/tmp/fixture-repo"),
        languages=["python"],
        budget=RunBudget(max_tokens=50_000, max_model_calls=50, max_files=20),
        enable_verification=False,
    )
    defaults.update(kwargs)
    return AuditRequest(**defaults)


def _runtime(llm: FakeLLM | None = None, files: dict | None = None) -> GraphRuntime:
    return GraphRuntime(
        llm=llm or FakeLLM(),
        offline=True,
        extra={"fixture_files": files if files is not None else FIXTURE_FILES},
    )


async def _ainvoke(app, state, runtime: GraphRuntime, thread_id: str):
    token = set_runtime(runtime)
    try:
        return await app.ainvoke(
            state,
            {
                "configurable": {
                    "thread_id": thread_id,
                    "runtime": runtime,
                }
            },
        )
    finally:
        reset_runtime(token)


@pytest.mark.asyncio
async def test_fake_llm_queue():
    llm = FakeLLM(responses=['{"ok":1}', '{"ok":2}'])
    r1 = await llm.complete([LLMMessage(role="user", content="a")])
    r2 = await llm.complete([LLMMessage(role="user", content="b")])
    r3 = await llm.complete([LLMMessage(role="user", content="c")])
    assert r1.content == '{"ok":1}'
    assert r2.content == '{"ok":2}'
    assert "ok" in r3.content
    assert len(llm.calls) == 3


@pytest.mark.asyncio
async def test_graph_end_to_end_with_fake_llm():
    llm = FakeLLM(
        responses=[
            '{"strategy":"sequential"}',
            json.dumps(
                [
                    {
                        "title": "SQL Injection",
                        "description": "string concat SQL",
                        "severity": "high",
                        "line": 4,
                    }
                ]
            ),
            json.dumps(
                [
                    {
                        "title": "Command Injection",
                        "description": "os.system",
                        "severity": "high",
                        "line": 3,
                    }
                ]
            ),
        ]
    )
    runtime = _runtime(llm)
    app = compile_audit_graph()
    req = _request()
    state = empty_audit_state(audit_id=req.id, request=req)

    result = await _ainvoke(app, state, runtime, req.id)

    assert result["status"] is AuditStatus.COMPLETED
    assert result.get("report") is not None
    findings = result["normalized_findings"]
    assert len(findings) >= 1
    for f in findings:
        assert f.verification_status is VerificationStatus.NOT_RUN
    assert result["report"].status is AuditStatus.COMPLETED
    assert "not_run" in result["report"].summary.lower()
    # fixtures: 2 source files (+ README ignored); tolerate filter edge cases
    assert result["manifest"].stats["selected"] >= 2
    kinds = [e["kind"] for e in result.get("events") or []]
    assert "node.completed" in kinds
    assert result["budget"].files_analyzed >= 1
    assert result["usage"].call_count >= 1


@pytest.mark.asyncio
async def test_graph_validation_failure_without_request():
    app = compile_audit_graph()
    result = await _ainvoke(
        app,
        {"audit_id": "x", "errors": [], "events": [], "candidate_findings": []},
        _runtime(),
        "x",
    )
    assert result["status"] is AuditStatus.FAILED
    assert result.get("errors")


@pytest.mark.asyncio
async def test_graph_empty_manifest():
    runtime = _runtime(files={})
    app = compile_audit_graph()
    req = _request()
    state = empty_audit_state(audit_id=req.id, request=req)
    result = await _ainvoke(app, state, runtime, req.id)
    assert result["status"] is AuditStatus.COMPLETED
    assert result["normalized_findings"] == []
    assert result["report"].findings == []


@pytest.mark.asyncio
async def test_include_exclude_paths():
    runtime = _runtime()
    app = compile_audit_graph()
    req = _request(include_paths=["app/"], exclude_paths=["app/utils.py"])
    state = empty_audit_state(audit_id=req.id, request=req)
    result = await _ainvoke(app, state, runtime, req.id)
    paths = result["manifest"].paths()
    assert paths == ["app/auth.py"]


@pytest.mark.asyncio
async def test_heuristic_findings_without_llm_json():
    runtime = _runtime(FakeLLM())
    app = compile_audit_graph()
    req = _request()
    state = empty_audit_state(audit_id=req.id, request=req)
    result = await _ainvoke(app, state, runtime, req.id)
    titles = {f.title for f in result["normalized_findings"]}
    assert any(
        "Command" in t or "SQL" in t or "secret" in t.lower() or "Injection" in t
        for t in titles
    )


@pytest.mark.asyncio
async def test_dedupe_merges_duplicate_fingerprints():
    llm = FakeLLM(
        responses=[
            "{}",
            json.dumps(
                [
                    {
                        "title": "OS Command Injection",
                        "description": "dup",
                        "severity": "high",
                        "line": 3,
                    }
                ]
            ),
            "[]",
        ]
    )
    runtime = _runtime(llm)
    app = compile_audit_graph()
    req = _request()
    state = empty_audit_state(audit_id=req.id, request=req)
    result = await _ainvoke(app, state, runtime, req.id)
    fps = [f.fingerprint for f in result["normalized_findings"]]
    assert len(fps) == len(set(fps))


@pytest.mark.asyncio
async def test_prioritize_orders_by_severity():
    llm = FakeLLM(
        responses=[
            "{}",
            json.dumps(
                [
                    {"title": "Low noise", "description": "x", "severity": "low", "line": 1},
                    {
                        "title": "Crit path",
                        "description": "y",
                        "severity": "critical",
                        "line": 2,
                    },
                ]
            ),
            "[]",
        ]
    )
    runtime = _runtime(llm)
    app = compile_audit_graph()
    req = _request()
    state = empty_audit_state(audit_id=req.id, request=req)
    result = await _ainvoke(app, state, runtime, req.id)
    findings = result["normalized_findings"]
    assert findings
    ranks = {
        Severity.CRITICAL: 5,
        Severity.HIGH: 4,
        Severity.MEDIUM: 3,
        Severity.LOW: 2,
        Severity.INFO: 1,
    }
    scores = [ranks[f.severity] for f in findings]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_memory_checkpointer_resume_thread():
    runtime = _runtime()
    app = compile_audit_graph()
    req = _request()
    state = empty_audit_state(audit_id=req.id, request=req)
    token = set_runtime(runtime)
    try:
        cfg = {"configurable": {"thread_id": "thread-resume-1", "runtime": runtime}}
        result = await app.ainvoke(state, cfg)
        assert result["status"] is AuditStatus.COMPLETED
        snap = await app.aget_state(cfg)
        assert snap.values.get("audit_id") == req.id
        assert snap.values.get("status") is AuditStatus.COMPLETED
    finally:
        reset_runtime(token)
