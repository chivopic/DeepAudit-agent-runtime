"""Business DB boundary for audit runs (findings / events / status).

Does not store LangGraph checkpoints — only product-queryable rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from app.services.agent.domain import (
    AuditReport,
    AuditRequest,
    AuditStatus,
    Finding,
    ModelUsage,
    finding_to_legacy_dict,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PersistedAuditRecord:
    """Snapshot of an audit run for listing / resume metadata."""

    audit_id: str
    status: AuditStatus
    request: Optional[AuditRequest] = None
    findings: list[Finding] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    report: Optional[AuditReport] = None
    usage: Optional[ModelUsage] = None
    # Serialized graph resume payload (JSON-friendly dict)
    graph_snapshot: dict[str, Any] = field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    completed_at: Optional[datetime] = None
    # Idempotency: fingerprints already written
    finding_fingerprints: set[str] = field(default_factory=set)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "status": self.status.value if isinstance(self.status, AuditStatus) else self.status,
            "findings_count": len(self.findings),
            "findings": [finding_to_legacy_dict(f) for f in self.findings],
            "events_count": len(self.events),
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "usage": self.usage.model_dump() if self.usage else None,
        }


@runtime_checkable
class BusinessAuditStore(Protocol):
    async def get(self, audit_id: str) -> Optional[PersistedAuditRecord]: ...

    async def upsert_run(
        self,
        audit_id: str,
        *,
        request: Optional[AuditRequest] = None,
        status: Optional[AuditStatus] = None,
    ) -> PersistedAuditRecord: ...

    async def append_events(
        self, audit_id: str, events: list[dict[str, Any]]
    ) -> int: ...

    async def upsert_findings(
        self, audit_id: str, findings: list[Finding]
    ) -> int:
        """Insert findings idempotently by fingerprint; return newly inserted count."""
        ...

    async def save_result(
        self,
        audit_id: str,
        *,
        status: AuditStatus,
        findings: list[Finding],
        events: list[dict[str, Any]],
        report: Optional[AuditReport] = None,
        usage: Optional[ModelUsage] = None,
        graph_snapshot: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> PersistedAuditRecord: ...

    async def list_audits(self, *, limit: int = 50) -> list[PersistedAuditRecord]: ...


class InMemoryBusinessStore:
    """Process-local store for tests and M3 without DB coupling."""

    def __init__(self) -> None:
        self._rows: dict[str, PersistedAuditRecord] = {}

    async def get(self, audit_id: str) -> Optional[PersistedAuditRecord]:
        return self._rows.get(audit_id)

    async def upsert_run(
        self,
        audit_id: str,
        *,
        request: Optional[AuditRequest] = None,
        status: Optional[AuditStatus] = None,
    ) -> PersistedAuditRecord:
        row = self._rows.get(audit_id)
        if row is None:
            row = PersistedAuditRecord(
                audit_id=audit_id,
                status=status or AuditStatus.PENDING,
                request=request,
            )
            self._rows[audit_id] = row
        else:
            if request is not None:
                row.request = request
            if status is not None:
                row.status = status
            row.updated_at = _utc_now()
        return row

    async def append_events(
        self, audit_id: str, events: list[dict[str, Any]]
    ) -> int:
        row = await self.upsert_run(audit_id)
        row.events.extend(events)
        row.updated_at = _utc_now()
        return len(events)

    async def upsert_findings(
        self, audit_id: str, findings: list[Finding]
    ) -> int:
        row = await self.upsert_run(audit_id)
        inserted = 0
        for f in findings:
            fp = f.fingerprint or f.id
            if fp in row.finding_fingerprints:
                # update in place by fingerprint
                for i, existing in enumerate(row.findings):
                    if (existing.fingerprint or existing.id) == fp:
                        row.findings[i] = f
                        break
                continue
            row.findings.append(f)
            row.finding_fingerprints.add(fp)
            inserted += 1
        row.updated_at = _utc_now()
        return inserted

    async def save_result(
        self,
        audit_id: str,
        *,
        status: AuditStatus,
        findings: list[Finding],
        events: list[dict[str, Any]],
        report: Optional[AuditReport] = None,
        usage: Optional[ModelUsage] = None,
        graph_snapshot: Optional[dict[str, Any]] = None,
        error_message: Optional[str] = None,
    ) -> PersistedAuditRecord:
        row = await self.upsert_run(audit_id, status=status)
        await self.upsert_findings(audit_id, findings)
        if events:
            # replace with full list for final save (idempotent re-run)
            row.events = list(events)
        row.report = report
        row.usage = usage
        if graph_snapshot is not None:
            row.graph_snapshot = graph_snapshot
        row.error_message = error_message
        row.status = status
        row.updated_at = _utc_now()
        if status in {AuditStatus.COMPLETED, AuditStatus.FAILED, AuditStatus.CANCELLED}:
            row.completed_at = _utc_now()
        return row

    async def list_audits(self, *, limit: int = 50) -> list[PersistedAuditRecord]:
        rows = sorted(self._rows.values(), key=lambda r: r.updated_at, reverse=True)
        return rows[:limit]
