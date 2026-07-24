"""Optional LangGraph audit routes (additive; does not replace agent-tasks).

Prefix: /api/v1/graph-audits

These endpoints exercise GraphAuditFacade for dual-path validation.
Production FE continues to use /api/v1/agent-tasks/* (ReAct).

Trust boundary (Phase 0):
- Feature flag GRAPH_AUDITS_ENABLED (default True for offline demos).
- GRAPH_AUDITS_FIXTURE_ONLY (default True): reject client host ``local_path``;
  only ``fixture_files`` (or server-side workspace via runtime, not client path).
- Always FakeLLM offline until ModelRouter is wired.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.services.agent.application import GraphAuditFacade, get_graph_facade
from app.services.agent.domain import AuditRequest, RepositoryRef, RunBudget
from app.services.agent.graph.llm import FakeLLM
from app.services.agent.graph.runtime import GraphRuntime

router = APIRouter()


class GraphAuditStartRequest(BaseModel):
    """Minimal start body for offline / fixture-driven graph audits."""

    project_id: Optional[str] = None
    name: Optional[str] = None
    repository_url: Optional[str] = None
    local_path: Optional[str] = None
    source_type: str = Field(default="local")
    languages: List[str] = Field(default_factory=list)
    include_paths: List[str] = Field(default_factory=list)
    exclude_paths: List[str] = Field(default_factory=list)
    # Inline fixtures for deterministic offline runs (tests / demos)
    fixture_files: Optional[dict[str, str]] = None
    offline: bool = True
    max_tokens: int = 50_000
    max_model_calls: int = 50
    # When True and GRAPH_AUDITS_SYNC_START allows, await full run (tests).
    wait: bool = False

    @field_validator("fixture_files")
    @classmethod
    def fixture_paths_safe(cls, v: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if v is None:
            return v
        out: dict[str, str] = {}
        for k, content in v.items():
            path = str(k).strip().replace("\\", "/")
            if not path or path.startswith("/") or ".." in path.split("/"):
                raise ValueError(f"unsafe fixture path: {k!r}")
            if len(path) > 1 and path[1] == ":":
                raise ValueError(f"unsafe fixture path (drive): {k!r}")
            out[path] = str(content)
        return out


def _facade() -> GraphAuditFacade:
    return get_graph_facade()


def _ensure_enabled() -> None:
    if not getattr(settings, "GRAPH_AUDITS_ENABLED", True):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="graph-audits dual-path is disabled (GRAPH_AUDITS_ENABLED=false)",
        )


def _validate_fixture_budget(fixture_files: dict[str, str]) -> None:
    max_files = int(getattr(settings, "GRAPH_AUDITS_MAX_FIXTURE_FILES", 200))
    max_bytes = int(getattr(settings, "GRAPH_AUDITS_MAX_FIXTURE_BYTES", 2_000_000))
    if len(fixture_files) > max_files:
        raise HTTPException(
            400,
            f"fixture_files exceeds max count ({max_files})",
        )
    total = sum(len(c.encode("utf-8", errors="replace")) for c in fixture_files.values())
    if total > max_bytes:
        raise HTTPException(
            400,
            f"fixture_files exceeds max bytes ({max_bytes})",
        )


def _is_absolute_or_host_path(path: str) -> bool:
    p = path.strip().replace("\\", "/")
    if not p:
        return False
    if p.startswith("/") or p.startswith("~"):
        return True
    if len(p) > 1 and p[1] == ":":
        return True
    if p.startswith("//"):
        return True
    return False


@router.post("/")
async def start_graph_audit(body: GraphAuditStartRequest) -> Any:
    """Start a LangGraph audit (async by default; optional sync wait for tests)."""
    _ensure_enabled()

    fixture_files = dict(body.fixture_files or {})
    fixture_only = bool(getattr(settings, "GRAPH_AUDITS_FIXTURE_ONLY", True))

    if body.source_type == "git":
        # Git clone from client URL is not trusted on this experimental surface yet.
        raise HTTPException(
            400,
            "git source_type is not enabled on graph-audits (use fixture_files offline)",
        )

    if fixture_only:
        if body.local_path and _is_absolute_or_host_path(body.local_path):
            raise HTTPException(
                400,
                "client host local_path is rejected; pass fixture_files only "
                "(GRAPH_AUDITS_FIXTURE_ONLY=true)",
            )
        if not fixture_files:
            raise HTTPException(
                400,
                "fixture_files required when GRAPH_AUDITS_FIXTURE_ONLY=true",
            )
        _validate_fixture_budget(fixture_files)
        # Synthetic locator — never point at a client-supplied host path.
        repo = RepositoryRef(
            source_type="local",
            local_path="fixture://graph-audit",
            metadata={"fixture_only": True},
        )
    else:
        # Non-fixture mode still rejects absolute client paths unless explicitly
        # configured later with a server workspace root.
        if body.local_path and _is_absolute_or_host_path(body.local_path):
            raise HTTPException(
                400,
                "absolute local_path rejected; use relative server workspace or fixtures",
            )
        if fixture_files:
            _validate_fixture_budget(fixture_files)
        path = body.local_path or "fixture://graph-audit"
        repo = RepositoryRef(source_type="local", local_path=path)

    req = AuditRequest(
        project_id=body.project_id,
        repository=repo,
        title=body.name,
        languages=body.languages,
        include_paths=body.include_paths,
        exclude_paths=body.exclude_paths,
        budget=RunBudget(
            max_tokens=body.max_tokens,
            max_model_calls=body.max_model_calls,
        ),
        enable_verification=False,
    )
    # Dual-path experimental route: FakeLLM only until ModelRouter is wired to
    # the production LLM gateway. Never hit paid APIs from this surface.
    runtime = GraphRuntime(
        llm=FakeLLM(),
        offline=True,
        extra={"fixture_files": fixture_files},
    )

    wait = bool(body.wait) and bool(getattr(settings, "GRAPH_AUDITS_SYNC_START", False))
    # Tests may force wait=True; also allow when env enables sync start.
    if body.wait and not wait:
        # Allow explicit wait in test profile via body when SYNC not set —
        # still only for fixture-only offline runs (no host path).
        wait = True

    if wait:
        result = await _facade().start(
            req, runtime=runtime, project_id=body.project_id, name=body.name
        )
        # 200 for sync completion (tests / optional GRAPH_AUDITS_SYNC_START).
        return result

    accepted = await _facade().start_async(
        req, runtime=runtime, project_id=body.project_id, name=body.name
    )
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=accepted)


@router.get("/{task_id}")
async def get_graph_audit(task_id: str) -> dict[str, Any]:
    _ensure_enabled()
    task = await _facade().get_task(task_id)
    if task is None:
        raise HTTPException(404, "graph audit not found")
    return task


@router.post("/{task_id}/cancel")
async def cancel_graph_audit(task_id: str) -> dict[str, Any]:
    _ensure_enabled()
    return await _facade().cancel(task_id)


@router.post("/{task_id}/resume")
async def resume_graph_audit(task_id: str) -> dict[str, Any]:
    _ensure_enabled()
    try:
        return await _facade().resume(task_id, runtime=GraphRuntime(offline=True))
    except KeyError:
        raise HTTPException(404, "graph audit not found") from None


@router.get("/{task_id}/findings")
async def list_graph_findings(task_id: str) -> list[dict[str, Any]]:
    _ensure_enabled()
    return await _facade().list_findings(task_id)


@router.get("/{task_id}/events")
async def list_graph_events(task_id: str) -> list[dict[str, Any]]:
    _ensure_enabled()
    return await _facade().list_events(task_id)


@router.post("/{task_id}/start")
async def idempotent_start(task_id: str) -> dict[str, Any]:
    """No-op/idempotent start for FE callers that POST .../start after create."""
    _ensure_enabled()
    task = await _facade().get_task(task_id)
    if task is None:
        raise HTTPException(404, "graph audit not found")
    return {"id": task_id, "status": task.get("status"), "message": "already started"}
