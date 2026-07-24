"""Conditional routing helpers for the audit graph."""

from __future__ import annotations

from typing import Literal

from .state import AuditState
from app.services.agent.domain import AuditStatus


def route_after_validate(state: AuditState) -> Literal["ingest_repository", "__end__"]:
    if state.get("status") is AuditStatus.FAILED:
        return "__end__"
    if state.get("cancelled"):
        return "__end__"
    return "ingest_repository"


def route_after_analyze(
    state: AuditState,
) -> Literal["analyze_file", "aggregate_findings", "finalize_cancelled"]:
    """Loop analyze_file while pending tasks remain and budget allows."""
    if state.get("cancelled") or state.get("status") is AuditStatus.CANCELLED:
        return "finalize_cancelled"
    # Also observe runtime cancel_check (in-process runner flag)
    try:
        from .runtime import get_runtime

        if get_runtime().is_cancelled():
            return "finalize_cancelled"
    except Exception:  # noqa: BLE001
        pass
    pending = state.get("pending_task_ids") or []
    budget = state.get("budget")
    if budget is not None and budget.is_exhausted():
        return "aggregate_findings"
    if pending:
        return "analyze_file"
    return "aggregate_findings"


def route_after_ingest(state: AuditState) -> Literal["build_manifest", "__end__"]:
    if state.get("status") is AuditStatus.FAILED:
        return "__end__"
    return "build_manifest"
