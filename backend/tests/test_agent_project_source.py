"""Server-resolved project source for graph-audits.

The point of this surface is that a real repository can be audited *without*
the client ever naming a path. These tests pin that property down.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.v1.endpoints import graph_audits as ga
from app.services.agent.graph.nodes import _resolve_workspace


class FakeUser:
    id = "u1"
    is_superuser = False


class FakeProject:
    id = "p1"
    owner_id = "u1"
    source_type = "repository"
    repository_url = "https://example.invalid/repo.git"
    default_branch = "main"


class FakeDB:
    """Just enough AsyncSession surface for the ACL path."""

    def __init__(self, project=None):
        self._project = project

    async def get(self, model, pk):
        return self._project

    async def execute(self, *_a, **_kw):
        class _R:
            def scalars(self_inner):
                class _S:
                    def first(self_s):
                        return None

                return _S()

        return _R()


# ---------------------------------------------------------------------------
# The ACL cannot be switched off for this source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_acl_is_mandatory_even_when_flag_disabled(monkeypatch):
    """GRAPH_AUDITS_ENFORCE_PROJECT_ACL must not weaken the project source."""
    monkeypatch.setattr(
        ga.settings, "GRAPH_AUDITS_ENFORCE_PROJECT_ACL", False, raising=False
    )

    stranger = FakeUser()
    stranger.id = "someone-else"

    with pytest.raises(HTTPException) as exc:
        await ga._assert_project_access(
            FakeDB(FakeProject()), stranger, "p1", require=True
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_project_acl_allows_owner():
    project = await ga._assert_project_access(
        FakeDB(FakeProject()), FakeUser(), "p1", require=True
    )
    assert project is not None


@pytest.mark.asyncio
async def test_missing_project_is_404():
    with pytest.raises(HTTPException) as exc:
        await ga._assert_project_access(FakeDB(None), FakeUser(), "p1", require=True)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_legacy_callers_keep_opt_in_behaviour(monkeypatch):
    """require=False preserves the old flag-driven no-op."""
    monkeypatch.setattr(
        ga.settings, "GRAPH_AUDITS_ENFORCE_PROJECT_ACL", False, raising=False
    )
    assert (
        await ga._assert_project_access(FakeDB(None), FakeUser(), "p1", require=False)
        is None
    )


# ---------------------------------------------------------------------------
# Client-supplied paths stay rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "~/secrets", "C:/Windows", "//host/share", "/proc/self/environ"],
)
def test_host_paths_are_still_rejected(path):
    assert ga._is_absolute_or_host_path(path) is True


def test_relative_paths_are_not_flagged_as_host_paths():
    assert ga._is_absolute_or_host_path("src/app.py") is False


# ---------------------------------------------------------------------------
# Synthetic locators are never opened as filesystem paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("locator", ["project://p1", "fixture://graph-audit"])
def test_synthetic_locator_is_not_a_workspace_path(locator):
    """A locator names a source; resolving it as a directory would be a bug."""
    from app.services.agent.domain import AuditRequest, RepositoryRef
    from app.services.agent.graph.runtime import GraphRuntime

    req = AuditRequest(
        repository=RepositoryRef(source_type="local", local_path=locator)
    )
    assert _resolve_workspace({"request": req}, GraphRuntime()) is None


def test_real_path_still_resolves(tmp_path):
    from app.services.agent.domain import AuditRequest, RepositoryRef
    from app.services.agent.graph.runtime import GraphRuntime

    req = AuditRequest(
        repository=RepositoryRef(source_type="local", local_path=str(tmp_path))
    )
    assert _resolve_workspace({"request": req}, GraphRuntime()) == tmp_path


# ---------------------------------------------------------------------------
# Deferred materialisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_awaits_resolver_and_caches_root(tmp_path):
    """Clone happens in ingest, not during the HTTP request, and only once."""
    from app.services.agent.domain import AuditRequest, RepositoryRef
    from app.services.agent.graph.nodes import ingest_repository
    from app.services.agent.graph.runtime import GraphRuntime

    (tmp_path / "app.py").write_text("import os\nos.system('x')\n")

    calls = []

    async def resolver():
        calls.append(1)
        return str(tmp_path)

    runtime = GraphRuntime(extra={"workspace_resolver": resolver})
    req = AuditRequest(
        repository=RepositoryRef(source_type="local", local_path="project://p1")
    )

    from app.services.agent.graph.runtime import reset_runtime, set_runtime

    token = set_runtime(runtime)
    try:
        out = await ingest_repository({"request": req, "audit_id": "aud1"})
    finally:
        reset_runtime(token)

    snap = out["repository"]
    assert snap.file_count == 1
    assert calls == [1]
    # later nodes read the resolved root back off the runtime
    assert runtime.workspace_root == tmp_path


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_stream_frames_match_agent_tasks_shape():
    """Frontend stream handling is shared, so frames must look the same."""
    import json

    from app.services.agent.application import get_graph_event_bus

    bus = get_graph_event_bus()
    task_id = "aud_sse_1"
    await bus.publish_raw(task_id, {"kind": "task_start", "message": "started"})
    await bus.publish_raw(task_id, {"kind": "node.completed", "message": "ingest"})
    await bus.publish_raw(task_id, {"kind": "task_complete", "message": "done"})

    frames = []
    async for event in bus.subscribe(task_id):
        frames.append(event)
        if event.get("type") in {"task_complete", "task_error", "task_cancel"}:
            break

    # graph "kind" is mapped onto the AgentEvent vocabulary the frontend knows
    assert [f["type"] for f in frames] == ["task_start", "info", "task_complete"]
    assert all("task_id" in f and "sequence" in f for f in frames)
    # each frame must be JSON-serialisable for `data: {json}`
    for f in frames:
        json.loads(json.dumps(f, ensure_ascii=False))


@pytest.mark.asyncio
async def test_event_stream_requires_ownership():
    """An unknown / unowned audit must not open a stream."""
    from app.api.v1.endpoints.graph_audits import _assert_audit_owner

    with pytest.raises(HTTPException) as exc:
        await _assert_audit_owner("does-not-exist", FakeUser())
    assert exc.value.status_code in (403, 404)
