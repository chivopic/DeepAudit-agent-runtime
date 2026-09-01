"""Optional LangGraph audit routes (additive; does not replace agent-tasks).

Prefix: /api/v1/graph-audits

These endpoints exercise GraphAuditFacade for dual-path validation.
Production FE continues to use /api/v1/agent-tasks/* (ReAct).

Trust boundary:
- JWT required (same ``get_current_user`` as agent-tasks).
- Per-audit ownership via ``AuditRequest.user_id`` on subsequent routes.
- Feature flag GRAPH_AUDITS_ENABLED.
- Client-controlled server ``local_path`` is rejected until a server-side
  workspace registry exists; graph-audits remains fixture-only in practice.
- Optional GRAPH_AUDITS_ENFORCE_PROJECT_ACL for Project owner/member checks.
- Always FakeLLM offline until ModelRouter is wired.
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core.config import settings
from app.db.session import get_db
from app.models.project import Project, ProjectMember
from app.models.user import User
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


async def _assert_project_access(
    db: AsyncSession,
    user: User,
    project_id: Optional[str],
) -> None:
    """Optional DB project ACL (owner or member). No-op when flag off or no project_id."""
    if not project_id:
        return
    if not bool(getattr(settings, "GRAPH_AUDITS_ENFORCE_PROJECT_ACL", False)):
        return
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if getattr(user, "is_superuser", False):
        return
    if project.owner_id == user.id:
        return
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    member = result.scalars().first()
    if member is None:
        raise HTTPException(status_code=403, detail="无权访问此项目")


async def _assert_audit_owner(task_id: str, user: User) -> dict[str, Any]:
    """Load task and enforce creator ownership (user_id on AuditRequest)."""
    task = await _facade().get_task(task_id)
    if task is None:
        raise HTTPException(404, "graph audit not found")
    row = await _facade().runner.get(task_id)
    owner_id: Optional[str] = None
    if row is not None and row.request is not None:
        owner_id = row.request.user_id
    if owner_id is None:
        # Legacy rows without user_id: deny when auth is required (fail closed).
        raise HTTPException(status_code=403, detail="audit has no owner; access denied")
    if getattr(user, "is_superuser", False):
        return task
    if owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问此审计任务")
    return task


@router.post("/")
async def start_graph_audit(
    body: GraphAuditStartRequest,
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Any:
    """Start a LangGraph audit (async by default; optional sync wait for tests)."""
    _ensure_enabled()
    await _assert_project_access(db, current_user, body.project_id)

    fixture_files = dict(body.fixture_files or {})

    if body.source_type == "git":
        # Git clone from client URL is not trusted on this experimental surface yet.
        raise HTTPException(
            400,
            "git source_type is not enabled on graph-audits (use fixture_files offline)",
        )

    # Client paths are never server capabilities.  Until graph-audits has a
    # server-managed workspace registry (workspace_id -> trusted path), do not
    # accept any client-supplied local_path, even a syntactically relative one.
    if body.local_path:
        raise HTTPException(
            400,
            "client local_path is disabled; use fixture_files until a server "
            "workspace registry is available",
        )

    if not fixture_files:
        raise HTTPException(
            400,
            "fixture_files required; server local_path access is disabled",
        )
    _validate_fixture_budget(fixture_files)

    # Synthetic locator only; never point at a client-supplied host path.
    repo = RepositoryRef(
        source_type="local",
        local_path="fixture://graph-audit",
        metadata={"fixture_only": True},
    )

    req = AuditRequest(
        project_id=body.project_id,
        user_id=str(current_user.id),
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

    sync_enabled = bool(getattr(settings, "GRAPH_AUDITS_SYNC_START", False))
    if body.wait and not sync_enabled:
        raise HTTPException(
            400,
            "synchronous graph-audit start is disabled "
            "(GRAPH_AUDITS_SYNC_START=false)",
        )
    wait = bool(body.wait)

    if wait:
        result = await _facade().start(
            req, runtime=runtime, project_id=body.project_id, name=body.name
        )
        # 200 for sync completion when explicitly enabled by the operator.
        return result

    accepted = await _facade().start_async(
        req, runtime=runtime, project_id=body.project_id, name=body.name
    )
    return JSONResponse(status_code=status.HTTP_202_ACCEPTED, content=accepted)


@router.get("/{task_id}")
async def get_graph_audit(
    task_id: str,
    current_user: User = Depends(deps.get_current_user),
) -> dict[str, Any]:
    _ensure_enabled()
    return await _assert_audit_owner(task_id, current_user)


@router.post("/{task_id}/cancel")
async def cancel_graph_audit(
    task_id: str,
    current_user: User = Depends(deps.get_current_user),
) -> dict[str, Any]:
    _ensure_enabled()
    await _assert_audit_owner(task_id, current_user)
    return await _facade().cancel(task_id)


@router.post("/{task_id}/resume")
async def resume_graph_audit(
    task_id: str,
    current_user: User = Depends(deps.get_current_user),
) -> dict[str, Any]:
    _ensure_enabled()
    await _assert_audit_owner(task_id, current_user)
    try:
        return await _facade().resume(task_id, runtime=GraphRuntime(offline=True))
    except KeyError:
        raise HTTPException(404, "graph audit not found") from None


@router.get("/{task_id}/findings")
async def list_graph_findings(
    task_id: str,
    current_user: User = Depends(deps.get_current_user),
) -> list[dict[str, Any]]:
    _ensure_enabled()
    await _assert_audit_owner(task_id, current_user)
    return await _facade().list_findings(task_id)


@router.get("/{task_id}/events")
async def list_graph_events(
    task_id: str,
    current_user: User = Depends(deps.get_current_user),
) -> list[dict[str, Any]]:
    _ensure_enabled()
    await _assert_audit_owner(task_id, current_user)
    return await _facade().list_events(task_id)


@router.post("/{task_id}/start")
async def idempotent_start(
    task_id: str,
    current_user: User = Depends(deps.get_current_user),
) -> dict[str, Any]:
    """No-op/idempotent start for FE callers that POST .../start after create."""
    _ensure_enabled()
    task = await _assert_audit_owner(task_id, current_user)
    return {"id": task_id, "status": task.get("status"), "message": "already started"}
