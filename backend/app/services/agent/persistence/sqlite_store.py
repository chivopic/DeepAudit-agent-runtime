"""SQLite business store shared by separate processes.

Checkpoints stay in the checkpointer. This table stores the product record:
status, findings, events, and the serialized request needed to resume.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.services.agent.domain import (
    AuditReport,
    AuditRequest,
    AuditStatus,
    Finding,
    ModelUsage,
)
from app.services.agent.persistence.business_store import (
    PersistedAuditRecord,
    _utc_now,
)


class SqliteBusinessStore:
    """Async wrapper around a WAL sqlite file. Safe for one writer at a time."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS graph_audit_records (
                    audit_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    async def get(self, audit_id: str) -> PersistedAuditRecord | None:
        return await asyncio.to_thread(self._get, audit_id)

    async def upsert_run(
        self,
        audit_id: str,
        *,
        request: AuditRequest | None = None,
        status: AuditStatus | None = None,
    ) -> PersistedAuditRecord:
        return await asyncio.to_thread(self._upsert_run, audit_id, request, status)

    async def append_events(self, audit_id: str, events: list[dict[str, Any]]) -> int:
        return await asyncio.to_thread(self._append_events, audit_id, events)

    async def upsert_findings(self, audit_id: str, findings: list[Finding]) -> int:
        return await asyncio.to_thread(self._upsert_findings, audit_id, findings)

    async def save_result(
        self,
        audit_id: str,
        *,
        status: AuditStatus,
        findings: list[Finding],
        events: list[dict[str, Any]],
        report: AuditReport | None = None,
        usage: ModelUsage | None = None,
        graph_snapshot: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> PersistedAuditRecord:
        return await asyncio.to_thread(
            self._save_result,
            audit_id,
            status,
            findings,
            events,
            report,
            usage,
            graph_snapshot,
            error_message,
        )

    async def list_audits(self, *, limit: int = 50) -> list[PersistedAuditRecord]:
        return await asyncio.to_thread(self._list, limit)

    def _get(self, audit_id: str) -> PersistedAuditRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM graph_audit_records WHERE audit_id = ?",
                (audit_id,),
            ).fetchone()
        if row is None:
            return None
        return _decode(json.loads(row["payload"]))

    def _upsert_run(
        self,
        audit_id: str,
        request: AuditRequest | None,
        status: AuditStatus | None,
    ) -> PersistedAuditRecord:
        current = self._get(audit_id)
        if current is None:
            current = PersistedAuditRecord(
                audit_id=audit_id,
                status=status or AuditStatus.PENDING,
                request=request,
            )
        else:
            if request is not None:
                current.request = request
            if status is not None:
                current.status = status
            current.updated_at = _utc_now()
        self._write(current)
        return current

    def _append_events(self, audit_id: str, events: list[dict[str, Any]]) -> int:
        row = self._upsert_run(audit_id, None, None)
        row.events.extend(events)
        row.updated_at = _utc_now()
        self._write(row)
        return len(events)

    def _upsert_findings(self, audit_id: str, findings: list[Finding]) -> int:
        row = self._upsert_run(audit_id, None, None)
        inserted = 0
        for finding in findings:
            fp = finding.fingerprint or finding.id
            if fp in row.finding_fingerprints:
                for index, existing in enumerate(row.findings):
                    if (existing.fingerprint or existing.id) == fp:
                        row.findings[index] = finding
                        break
                continue
            row.findings.append(finding)
            row.finding_fingerprints.add(fp)
            inserted += 1
        row.updated_at = _utc_now()
        self._write(row)
        return inserted

    def _save_result(
        self,
        audit_id: str,
        status: AuditStatus,
        findings: list[Finding],
        events: list[dict[str, Any]],
        report: AuditReport | None,
        usage: ModelUsage | None,
        graph_snapshot: dict[str, Any] | None,
        error_message: str | None,
    ) -> PersistedAuditRecord:
        row = self._upsert_run(audit_id, None, status)
        self._upsert_findings(audit_id, findings)
        row = self._get(audit_id) or row
        if events:
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
        self._write(row)
        return row

    def _list(self, limit: int) -> list[PersistedAuditRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM graph_audit_records ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_decode(json.loads(row["payload"])) for row in rows]

    def _write(self, record: PersistedAuditRecord) -> None:
        payload = _encode(record)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO graph_audit_records (audit_id, status, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(audit_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (record.audit_id, record.status.value, payload, record.updated_at.isoformat()),
            )


def _encode(record: PersistedAuditRecord) -> str:
    body = {
        "audit_id": record.audit_id,
        "status": record.status.value,
        "request": record.request.model_dump(mode="json") if record.request else None,
        "findings": [f.model_dump(mode="json") for f in record.findings],
        "events": record.events,
        "report": record.report.model_dump(mode="json") if record.report else None,
        "usage": record.usage.model_dump(mode="json") if record.usage else None,
        "graph_snapshot": record.graph_snapshot,
        "error_message": record.error_message,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "finding_fingerprints": sorted(record.finding_fingerprints),
    }
    return json.dumps(body)


def _decode(body: dict[str, Any]) -> PersistedAuditRecord:
    return PersistedAuditRecord(
        audit_id=body["audit_id"],
        status=AuditStatus(body["status"]),
        request=AuditRequest.model_validate(body["request"]) if body.get("request") else None,
        findings=[Finding.model_validate(item) for item in body.get("findings") or []],
        events=list(body.get("events") or []),
        report=AuditReport.model_validate(body["report"]) if body.get("report") else None,
        usage=ModelUsage.model_validate(body["usage"]) if body.get("usage") else None,
        graph_snapshot=dict(body.get("graph_snapshot") or {}),
        error_message=body.get("error_message"),
        finding_fingerprints=set(body.get("finding_fingerprints") or []),
    )
