"""Map graph domain events / status → legacy agent-task API shapes (M4)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from app.services.agent.domain import (
    AuditStatus,
    Finding,
    finding_to_legacy_dict,
)


# Graph AuditStatus → AgentTaskStatus strings used by FE / ORM
_STATUS_MAP: dict[str, str] = {
    AuditStatus.PENDING.value: "pending",
    AuditStatus.VALIDATING.value: "initializing",
    AuditStatus.INGESTING.value: "indexing",
    AuditStatus.PLANNING.value: "planning",
    AuditStatus.ANALYZING.value: "analyzing",
    AuditStatus.AGGREGATING.value: "analyzing",
    AuditStatus.VERIFYING.value: "verifying",
    AuditStatus.REPORTING.value: "reporting",
    AuditStatus.COMPLETED.value: "completed",
    AuditStatus.PARTIAL.value: "partial",
    AuditStatus.FAILED.value: "failed",
    AuditStatus.CANCELLED.value: "cancelled",
    AuditStatus.PAUSED.value: "paused",
}

_PHASE_MAP: dict[str, str] = {
    AuditStatus.PENDING.value: "planning",
    AuditStatus.VALIDATING.value: "planning",
    AuditStatus.INGESTING.value: "indexing",
    AuditStatus.PLANNING.value: "planning",
    AuditStatus.ANALYZING.value: "analysis",
    AuditStatus.AGGREGATING.value: "analysis",
    AuditStatus.VERIFYING.value: "verification",
    AuditStatus.REPORTING.value: "reporting",
    AuditStatus.COMPLETED.value: "reporting",
    AuditStatus.PARTIAL.value: "reporting",
}


def audit_status_to_task_status(status: AuditStatus | str) -> str:
    key = status.value if isinstance(status, AuditStatus) else str(status)
    return _STATUS_MAP.get(key, key)


def audit_status_to_phase(status: AuditStatus | str) -> str:
    key = status.value if isinstance(status, AuditStatus) else str(status)
    return _PHASE_MAP.get(key, "analysis")


def graph_event_to_sse(
    event: dict[str, Any],
    *,
    task_id: str,
    sequence: int = 0,
) -> dict[str, Any]:
    """Convert a graph node event dict into AgentEvent.to_sse_dict-compatible shape."""
    kind = str(event.get("kind") or "info")
    message = str(event.get("message") or kind)

    # Map graph kinds → AgentEventType-ish strings
    type_map = {
        "node.completed": "info",
        "node.failed": "error",
        "task.cancelled": "task_cancel",
        "task.error": "task_error",
        "task_start": "task_start",
        "task_complete": "task_complete",
        "finding_new": "finding_new",
        "progress": "progress",
    }
    event_type = type_map.get(kind, "info")
    if kind.startswith("phase."):
        event_type = "phase_start" if "start" in kind else "phase_complete"

    meta = {k: v for k, v in event.items() if k not in {"kind", "message"}}
    return {
        "id": str(uuid4()),
        "task_id": task_id,
        "type": event_type,
        "phase": event.get("phase"),
        "message": message,
        "tool_name": event.get("tool_name"),
        "tool_input": event.get("tool_input"),
        "tool_output": event.get("tool_output"),
        "tool_duration_ms": event.get("tool_duration_ms"),
        "finding_id": event.get("finding_id"),
        "tokens_used": int(event.get("tokens_used") or 0),
        "metadata": meta or None,
        "sequence": sequence,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def findings_to_api_list(findings: list[Finding]) -> list[dict[str, Any]]:
    out = []
    for f in findings:
        d = finding_to_legacy_dict(f)
        # Align with AgentFinding.to_dict / FE fields
        d.setdefault("vulnerability_type", f.category or f.rule_id or "other")
        d.setdefault("ai_confidence", f.confidence)
        d.setdefault("suggestion", f.recommendation)
        d.setdefault("is_verified", f.verification_status.value == "confirmed")
        d.setdefault(
            "status",
            "new" if f.status.value == "new" else f.status.value,
        )
        out.append(d)
    return out


def task_summary_from_run(
    *,
    audit_id: str,
    project_id: Optional[str],
    status: AuditStatus | str,
    findings: list[Finding],
    tokens_used: int = 0,
    analyzed_files: int = 0,
    total_files: int = 0,
    name: Optional[str] = None,
    error_message: Optional[str] = None,
    created_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build AgentTaskResponse-compatible dict (without ORM)."""
    sev = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    verified = 0
    for f in findings:
        key = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
        if key in sev:
            sev[key] += 1
        if f.verification_status.value == "confirmed":
            verified += 1

    st = audit_status_to_task_status(status)
    now = datetime.now(timezone.utc)
    return {
        "id": audit_id,
        "project_id": project_id or "",
        "name": name,
        "description": None,
        "task_type": "agent_audit",
        "status": st,
        "current_phase": audit_status_to_phase(status),
        "current_step": None,
        "total_files": total_files,
        "indexed_files": total_files,
        "analyzed_files": analyzed_files,
        "total_chunks": 0,
        "total_iterations": 0,
        "tool_calls_count": 0,
        "tokens_used": tokens_used,
        "findings_count": len(findings),
        "total_findings": len(findings),
        "verified_count": verified,
        "verified_findings": verified,
        "false_positive_count": 0,
        "critical_count": sev["critical"],
        "high_count": sev["high"],
        "medium_count": sev["medium"],
        "low_count": sev["low"],
        "quality_score": 0.0,
        "security_score": None,
        "progress_percentage": (
            100.0 if st in {"completed", "partial", "cancelled", "failed"} else 0.0
        ),
        "created_at": created_at or now,
        "started_at": created_at,
        "completed_at": completed_at,
        "audit_scope": None,
        "target_vulnerabilities": None,
        "verification_level": "analysis_only",
        "exclude_patterns": None,
        "target_files": None,
        "error_message": error_message,
        "runtime": "langgraph",
    }
