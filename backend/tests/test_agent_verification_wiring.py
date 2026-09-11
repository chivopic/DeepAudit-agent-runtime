"""The M7 verification subgraph, actually reachable from the audit graph.

It was built and tested at M7, but nothing in the audit graph ever called it:
`enable_verification` was a field no code read, so every finding came back
NOT_RUN no matter what the request asked for. These pin the wiring down.
"""

from __future__ import annotations

import pytest

from app.services.agent.application.facade import GraphAuditFacade
from app.services.agent.application.runner import AuditRunner
from app.services.agent.domain import (
    AuditRequest,
    RepositoryRef,
    RunBudget,
    VerificationStatus,
)
from app.services.agent.graph.builder import build_audit_graph
from app.services.agent.graph.llm import FakeLLM
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.persistence import InMemoryBusinessStore

FIXTURE = {
    "app.py": "import os\nimport pickle\nos.system('ping ' + h)\npickle.loads(b)\n"
}


def _request(*, enable: bool) -> AuditRequest:
    return AuditRequest(
        repository=RepositoryRef(source_type="local", local_path="fixture://t"),
        budget=RunBudget(max_tokens=50_000, max_model_calls=10),
        enable_verification=enable,
    )


async def _statuses(*, enable: bool, offline: bool) -> set[str]:
    runtime = GraphRuntime(
        llm=FakeLLM(), offline=offline, extra={"fixture_files": FIXTURE}
    )
    facade = GraphAuditFacade(runner=AuditRunner(store=InMemoryBusinessStore()))
    req = _request(enable=enable)
    await facade.start(req, runtime=runtime)
    findings = await facade.list_findings(req.id)
    assert findings, "fixture should produce findings"
    return {f["verification_status"] for f in findings}


def test_the_node_is_in_the_graph():
    """Regression: the subgraph existed for a milestone without being reachable."""
    assert "verify_findings" in build_audit_graph().nodes


@pytest.mark.asyncio
async def test_disabled_keeps_phase_1_invariant():
    """Invariant 1: findings are NOT_RUN unless verification is asked for."""
    assert await _statuses(enable=False, offline=True) == {
        VerificationStatus.NOT_RUN.value
    }


@pytest.mark.asyncio
async def test_enabled_offline_runs_the_static_path_without_executing():
    """ADR-003: the default path executes no untrusted code, so offline must
    not become an execution run just because verification was requested."""
    assert await _statuses(enable=True, offline=True) == {
        VerificationStatus.SKIPPED.value
    }


@pytest.mark.asyncio
async def test_enabled_online_attempts_execution():
    statuses = await _statuses(enable=True, offline=False)
    assert VerificationStatus.NOT_RUN.value not in statuses


@pytest.mark.asyncio
async def test_verification_failure_does_not_sink_the_run(monkeypatch):
    """A broken verifier must cost the verification, not the whole audit."""
    from app.services.agent.graph import subgraphs

    async def boom(*_a, **_kw):
        raise RuntimeError("sandbox exploded")

    monkeypatch.setattr(subgraphs, "verify_findings", boom)

    runtime = GraphRuntime(
        llm=FakeLLM(), offline=True, extra={"fixture_files": FIXTURE}
    )
    facade = GraphAuditFacade(runner=AuditRunner(store=InMemoryBusinessStore()))
    req = _request(enable=True)
    summary = await facade.start(req, runtime=runtime)

    assert summary["status"] in {"completed", "partial"}
    assert await facade.list_findings(req.id)


@pytest.mark.asyncio
async def test_the_node_reports_what_it_did():
    from app.services.agent.graph.nodes import verify_findings_node

    out = await verify_findings_node({"normalized_findings": [], "request": None})
    assert out["events"][0]["message"].startswith("verify_findings skipped")
