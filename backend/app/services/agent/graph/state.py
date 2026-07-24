"""LangGraph AuditState: TypedDict + reducers (no giant Pydantic state)."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from app.services.agent.domain import (
    AuditPlan,
    AuditReport,
    AuditRequest,
    AuditStatus,
    CandidateFinding,
    Finding,
    ModelUsage,
    NodeError,
    RepositoryManifest,
    RepositorySnapshot,
    RunBudget,
    VerifiedFinding,
)


def merge_dicts(left: Optional[dict], right: Optional[dict]) -> dict:
    """Shallow-merge dicts; right wins on key conflict."""
    out: dict = {}
    if left:
        out.update(left)
    if right:
        out.update(right)
    return out


def last_value(left: Any, right: Any) -> Any:
    """Reducer that prefers the non-None right-hand update."""
    if right is None:
        return left
    return right


class AuditState(TypedDict, total=False):
    """Graph state for a single audit run.

    Nodes return **deltas**. List fields with ``Annotated[..., operator.add]``
    append under parallel fan-in. Large payloads must be ``ArtifactRef`` only.
    """

    audit_id: str
    thread_id: str
    request: AuditRequest
    repository: RepositorySnapshot
    manifest: RepositoryManifest
    plan: AuditPlan
    # Work queue for analyze_file fan-out (serial in M2 skeleton)
    pending_task_ids: list[str]
    current_task_id: Optional[str]
    # Parallel-safe append reducers
    candidate_findings: Annotated[list[CandidateFinding], operator.add]
    normalized_findings: list[Finding]
    verified_findings: Annotated[list[VerifiedFinding], operator.add]
    errors: Annotated[list[NodeError], operator.add]
    events: Annotated[list[dict[str, Any]], operator.add]
    budget: RunBudget
    usage: ModelUsage
    status: AuditStatus
    report: AuditReport
    # Free-form node scratch (shallow-merged)
    meta: Annotated[dict[str, Any], merge_dicts]
    cancelled: bool


def empty_audit_state(
    *,
    audit_id: str,
    request: AuditRequest,
    thread_id: Optional[str] = None,
) -> AuditState:
    """Initial state for a new graph run."""
    return AuditState(
        audit_id=audit_id,
        thread_id=thread_id or audit_id,
        request=request,
        candidate_findings=[],
        normalized_findings=[],
        verified_findings=[],
        errors=[],
        events=[],
        pending_task_ids=[],
        current_task_id=None,
        budget=request.budget.model_copy(deep=True),
        usage=ModelUsage(),
        status=AuditStatus.PENDING,
        meta={},
        cancelled=False,
    )
