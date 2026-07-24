"""Optional LangGraph audit routes (additive; does not replace agent-tasks).

Prefix: /api/v1/graph-audits

These endpoints exercise GraphAuditFacade for dual-path validation.
Production FE continues to use /api/v1/agent-tasks/* (ReAct).
"""

from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

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


def _facade() -> GraphAuditFacade:
    return get_graph_facade()


@router.post("/")
async def start_graph_audit(body: GraphAuditStartRequest) -> dict[str, Any]:
    """Start a LangGraph audit (synchronous for M4 simplicity)."""
    if body.source_type == "git":
        if not body.repository_url:
            raise HTTPException(400, "repository_url required for git")
        repo = RepositoryRef(source_type="git", url=body.repository_url)
    else:
        path = body.local_path or "/tmp/graph-audit"
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
        extra={"fixture_files": body.fixture_files or {}},
    )
    return await _facade().start(
        req, runtime=runtime, project_id=body.project_id, name=body.name
    )


@router.get("/{task_id}")
async def get_graph_audit(task_id: str) -> dict[str, Any]:
    task = await _facade().get_task(task_id)
    if task is None:
        raise HTTPException(404, "graph audit not found")
    return task


@router.post("/{task_id}/cancel")
async def cancel_graph_audit(task_id: str) -> dict[str, Any]:
    return await _facade().cancel(task_id)


@router.post("/{task_id}/resume")
async def resume_graph_audit(task_id: str) -> dict[str, Any]:
    try:
        return await _facade().resume(task_id, runtime=GraphRuntime(offline=True))
    except KeyError:
        raise HTTPException(404, "graph audit not found") from None


@router.get("/{task_id}/findings")
async def list_graph_findings(task_id: str) -> list[dict[str, Any]]:
    return await _facade().list_findings(task_id)


@router.get("/{task_id}/events")
async def list_graph_events(task_id: str) -> list[dict[str, Any]]:
    return await _facade().list_events(task_id)


@router.post("/{task_id}/start")
async def idempotent_start(task_id: str) -> dict[str, Any]:
    """No-op/idempotent start for FE callers that POST .../start after create."""
    task = await _facade().get_task(task_id)
    if task is None:
        raise HTTPException(404, "graph audit not found")
    return {"id": task_id, "status": task.get("status"), "message": "already started"}
