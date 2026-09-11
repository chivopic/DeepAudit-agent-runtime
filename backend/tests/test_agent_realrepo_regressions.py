"""Regressions found only by running the graph against a real repository.

Both bugs were invisible to fixture-based tests: fixtures hold a handful of
files and the budgets used in tests stop the loop early.
"""

from __future__ import annotations

import pytest

from app.services.agent.application import GraphAuditFacade
from app.services.agent.application.runner import (
    _GRAPH_FIXED_STEPS,
    AuditRunner,
    _recursion_limit,
    _serialize_snapshot,
)
from app.services.agent.domain import (
    AuditRequest,
    RepositoryRef,
    RepositoryManifest,
    RunBudget,
)
from app.services.agent.graph.llm import FakeLLM
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.persistence import InMemoryBusinessStore


# ---------------------------------------------------------------------------
# GraphRecursionError on any repository bigger than ~20 files
# ---------------------------------------------------------------------------


def test_recursion_limit_scales_past_langgraph_default():
    """Each analyzed file is a super-step; the default 25 caps an audit at ~20
    files, so a real repository died with GraphRecursionError."""
    limit = _recursion_limit(RunBudget(max_model_calls=200))
    assert limit > 25
    assert limit >= _GRAPH_FIXED_STEPS + 200


def test_recursion_limit_follows_max_files_when_it_is_the_binding_cap():
    assert _recursion_limit(RunBudget(max_files=500, max_model_calls=10)) >= 500


def test_recursion_limit_never_below_langgraph_default():
    """An unbounded budget must not produce a limit that is worse than stock."""
    assert _recursion_limit(RunBudget(max_model_calls=0, max_files=0)) == 25


def test_recursion_limit_is_capped(monkeypatch):
    """Erring high is safe — the budget still stops the run — but not unbounded."""
    from app.services.agent.application import runner as runner_mod

    monkeypatch.setattr(
        runner_mod.settings, "GRAPH_AUDITS_RECURSION_LIMIT_CAP", 100, raising=False
    )
    assert _recursion_limit(RunBudget(max_model_calls=10_000)) == 100


def test_recursion_limit_tolerates_a_budgetless_request():
    assert _recursion_limit(None) == 25


# ---------------------------------------------------------------------------
# total_files lost on the polling path
# ---------------------------------------------------------------------------


def test_snapshot_carries_the_file_count():
    """start is async by default, so clients read the persisted row — the file
    count has to survive into it."""
    from app.services.agent.domain import FileArtifact

    snap = _serialize_snapshot(
        {"audit_id": "a1", "manifest": RepositoryManifest(snapshot_id="s1", files=[])}
    )
    assert snap["total_files"] == 0

    manifest = RepositoryManifest(
        snapshot_id="s1",
        files=[FileArtifact(path=f"f{i}.py", language="python") for i in range(37)],
    )
    snap = _serialize_snapshot({"audit_id": "a1", "manifest": manifest})
    assert snap["total_files"] == 37


def test_snapshot_without_a_manifest_is_still_serialisable():
    assert "total_files" not in _serialize_snapshot({"audit_id": "a1"})


@pytest.mark.asyncio
async def test_polled_task_reports_the_same_file_count_as_the_run():
    """get_task used to hardcode total_files=0, so every polling client saw 0."""
    fixture = {f"pkg/mod{i}.py": "import os\nos.system('x')\n" for i in range(6)}
    req = AuditRequest(
        repository=RepositoryRef(source_type="local", local_path="fixture://t"),
        budget=RunBudget(max_tokens=50_000, max_model_calls=20),
    )
    runtime = GraphRuntime(llm=FakeLLM(), offline=True, extra={"fixture_files": fixture})
    facade = GraphAuditFacade(runner=AuditRunner(store=InMemoryBusinessStore()))

    started = await facade.start(req, runtime=runtime)
    polled = await facade.get_task(req.id)

    assert started["total_files"] == len(fixture)
    assert polled["total_files"] == started["total_files"]
    assert polled["indexed_files"] == started["total_files"]
