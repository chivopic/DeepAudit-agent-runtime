"""AuditRunner: run / resume / cancel audit graphs (M3)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    Finding,
    ModelUsage,
)
from app.services.agent.graph.builder import compile_audit_graph
from app.services.agent.graph.runtime import GraphRuntime, reset_runtime, set_runtime
from app.services.agent.graph.state import empty_audit_state
from app.services.agent.persistence.artifact_store import (
    ArtifactStore,
    InMemoryArtifactStore,
)
from app.services.agent.persistence.business_store import (
    BusinessAuditStore,
    InMemoryBusinessStore,
    PersistedAuditRecord,
)
from app.services.agent.persistence.checkpointer import create_checkpointer

logger = logging.getLogger(__name__)


def _serialize_snapshot(result: dict[str, Any]) -> dict[str, Any]:
    """JSON-friendly summary for resume metadata (not full LangGraph checkpoint)."""
    snap: dict[str, Any] = {
        "audit_id": result.get("audit_id"),
        "status": (
            result["status"].value
            if hasattr(result.get("status"), "value")
            else result.get("status")
        ),
        "pending_task_ids": list(result.get("pending_task_ids") or []),
        "meta": dict(result.get("meta") or {}),
    }
    budget = result.get("budget")
    if budget is not None and hasattr(budget, "model_dump"):
        snap["budget"] = budget.model_dump()
    usage = result.get("usage")
    if usage is not None and hasattr(usage, "model_dump"):
        snap["usage"] = usage.model_dump()
    return snap


@dataclass
class AuditRunResult:
    audit_id: str
    status: AuditStatus
    findings: list[Finding] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    report: Any = None
    usage: Optional[ModelUsage] = None
    record: Optional[PersistedAuditRecord] = None
    raw_state: dict[str, Any] = field(default_factory=dict)


class AuditRunner:
    """Coordinates graph compile → invoke → business persistence.

    Cancel is cooperative: sets a flag on an in-process map (M4 hardens).
    """

    def __init__(
        self,
        *,
        store: Optional[BusinessAuditStore] = None,
        artifacts: Optional[ArtifactStore] = None,
        checkpointer: Any = None,
        checkpointer_backend: str = "memory",
    ) -> None:
        self.store: BusinessAuditStore = store or InMemoryBusinessStore()
        self.artifacts: ArtifactStore = artifacts or InMemoryArtifactStore()
        self._checkpointer = checkpointer
        self._checkpointer_backend = checkpointer_backend
        self._cancel_flags: dict[str, bool] = {}
        self._compiled = None

    def _graph(self):
        if self._compiled is None:
            cp = self._checkpointer or create_checkpointer(
                backend=self._checkpointer_backend  # type: ignore[arg-type]
            )
            self._compiled = compile_audit_graph(checkpointer=cp)
        return self._compiled

    def request_cancel(self, audit_id: str) -> None:
        self._cancel_flags[audit_id] = True

    def is_cancelled(self, audit_id: str) -> bool:
        return bool(self._cancel_flags.get(audit_id))

    async def run(
        self,
        request: AuditRequest,
        *,
        runtime: Optional[GraphRuntime] = None,
        audit_id: Optional[str] = None,
    ) -> AuditRunResult:
        aid = audit_id or request.id
        runtime = runtime or GraphRuntime(offline=True)
        # Wire cooperative cancel into runtime so nodes/routing can observe it.
        runtime.cancel_check = lambda: self.is_cancelled(aid)
        await self.store.upsert_run(aid, request=request, status=AuditStatus.PENDING)

        state = empty_audit_state(audit_id=aid, request=request, thread_id=aid)
        if self.is_cancelled(aid):
            await self.store.save_result(
                aid,
                status=AuditStatus.CANCELLED,
                findings=[],
                events=[{"kind": "task.cancelled", "message": "cancelled before start"}],
            )
            return AuditRunResult(audit_id=aid, status=AuditStatus.CANCELLED)

        token = set_runtime(runtime)
        try:
            app = self._graph()
            result = await app.ainvoke(
                state,
                {
                    "configurable": {
                        "thread_id": aid,
                        "runtime": runtime,
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("audit run failed: %s", aid)
            rec = await self.store.save_result(
                aid,
                status=AuditStatus.FAILED,
                findings=[],
                events=[{"kind": "task.error", "message": str(exc)}],
                error_message=str(exc),
            )
            return AuditRunResult(
                audit_id=aid,
                status=AuditStatus.FAILED,
                events=rec.events,
                record=rec,
            )
        finally:
            reset_runtime(token)

        status = result.get("status") or AuditStatus.COMPLETED
        if not isinstance(status, AuditStatus):
            try:
                status = AuditStatus(str(status))
            except ValueError:
                status = AuditStatus.COMPLETED

        # Prefer explicit cancel flag / runtime cancel over a completed/partial report.
        if result.get("cancelled") or self.is_cancelled(aid):
            status = AuditStatus.CANCELLED

        findings = list(result.get("normalized_findings") or [])
        events = list(result.get("events") or [])
        report = result.get("report")
        usage = result.get("usage")

        # Optional: park report markdown as artifact
        if (
            report is not None
            and getattr(report, "summary", None)
            and status not in {AuditStatus.CANCELLED}
        ):
            try:
                ref = await self.artifacts.put(
                    report.summary,
                    kind=__import__(
                        "app.services.agent.domain", fromlist=["ArtifactKind"]
                    ).ArtifactKind.REPORT,
                    media_type="text/markdown",
                    audit_id=aid,
                    suffix=".md",
                )
                if report.metadata is None:
                    report.metadata = {}
                report.metadata["summary_artifact"] = ref.model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001
                logger.debug("artifact put skipped: %s", exc)

        rec = await self.store.save_result(
            aid,
            status=status,
            findings=findings,
            events=events,
            report=report,
            usage=usage if isinstance(usage, ModelUsage) else None,
            graph_snapshot=_serialize_snapshot(result),
        )
        # Idempotent second write should insert 0 new findings
        await self.store.upsert_findings(aid, findings)

        return AuditRunResult(
            audit_id=aid,
            status=status,
            findings=findings,
            events=events,
            report=report,
            usage=usage if isinstance(usage, ModelUsage) else None,
            record=rec,
            raw_state=result,
        )

    async def resume(
        self,
        audit_id: str,
        *,
        runtime: Optional[GraphRuntime] = None,
    ) -> AuditRunResult:
        """Resume or re-drive an audit.

        If the in-process checkpointer still has the thread, read state.
        If status is incomplete and request is stored, re-run from request
        (M3: full re-invoke; M4+ may continue mid-graph).
        """
        row = await self.store.get(audit_id)
        if row is None:
            raise KeyError(f"unknown audit_id: {audit_id}")

        if row.status in {
            AuditStatus.COMPLETED,
            AuditStatus.PARTIAL,
            AuditStatus.CANCELLED,
            AuditStatus.FAILED,
        }:
            return AuditRunResult(
                audit_id=audit_id,
                status=row.status,
                findings=list(row.findings),
                events=list(row.events),
                report=row.report,
                usage=row.usage,
                record=row,
            )

        # Try live checkpointer state
        app = self._graph()
        cfg = {"configurable": {"thread_id": audit_id}}
        try:
            snap = await app.aget_state(cfg)
            if snap and snap.values and snap.values.get("status") is AuditStatus.COMPLETED:
                result = snap.values
                findings = list(result.get("normalized_findings") or [])
                rec = await self.store.save_result(
                    audit_id,
                    status=AuditStatus.COMPLETED,
                    findings=findings,
                    events=list(result.get("events") or []),
                    report=result.get("report"),
                    usage=result.get("usage"),
                    graph_snapshot=_serialize_snapshot(result),
                )
                return AuditRunResult(
                    audit_id=audit_id,
                    status=AuditStatus.COMPLETED,
                    findings=findings,
                    events=rec.events,
                    report=result.get("report"),
                    record=rec,
                    raw_state=result,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("aget_state resume miss: %s", exc)

        if row.request is None:
            raise RuntimeError(f"cannot resume {audit_id}: missing stored request")

        return await self.run(
            row.request,
            runtime=runtime,
            audit_id=audit_id,
        )

    async def get(self, audit_id: str) -> Optional[PersistedAuditRecord]:
        return await self.store.get(audit_id)
