"""API façade over AuditRunner — dual-path ready, no route rewrites required.

Production ReAct path remains default. Callers can opt into LangGraph via
``GraphAuditFacade`` (M4). Optional thin routes can wrap these methods later.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.services.agent.domain import AuditRequest, AuditStatus
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.persistence.business_store import InMemoryBusinessStore

from .api_mapping import (
    findings_to_api_list,
    task_summary_from_run,
)
from .event_bus import GraphEventBus, get_graph_event_bus
from .runner import AuditRunner, AuditRunResult

logger = logging.getLogger(__name__)


class GraphAuditFacade:
    """Façade: run/cancel/resume/status/findings/events in API-friendly shapes."""

    def __init__(
        self,
        runner: Optional[AuditRunner] = None,
        bus: Optional[GraphEventBus] = None,
    ) -> None:
        self.runner = runner or AuditRunner(store=InMemoryBusinessStore())
        self.bus = bus or get_graph_event_bus()
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    async def start(
        self,
        request: AuditRequest,
        *,
        runtime: Optional[GraphRuntime] = None,
        project_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> dict[str, Any]:
        """Run graph audit to completion and publish events to the bus."""
        await self.bus.publish_raw(
            request.id,
            {"kind": "task_start", "message": "langgraph audit started"},
        )
        result = await self.runner.run(request, runtime=runtime, audit_id=request.id)
        await self.bus.publish_many(request.id, result.events)
        terminal_ok = result.status in {
            AuditStatus.COMPLETED,
            AuditStatus.PARTIAL,
            AuditStatus.CANCELLED,
        }
        await self.bus.publish_raw(
            request.id,
            {
                "kind": "task_complete" if terminal_ok else "task.error",
                "message": f"status={result.status.value}",
            },
        )
        await self.bus.close(request.id)
        return self._result_to_task_dict(
            result, project_id=project_id or request.project_id, name=name
        )

    async def start_async(
        self,
        request: AuditRequest,
        *,
        runtime: Optional[GraphRuntime] = None,
        project_id: Optional[str] = None,
        name: Optional[str] = None,
    ) -> dict[str, Any]:
        """Register PENDING run and schedule graph execution in the background.

        Returns immediately so cancel can race against mid-run nodes.
        """
        await self.runner.store.upsert_run(
            request.id, request=request, status=AuditStatus.PENDING
        )
        await self.bus.publish_raw(
            request.id,
            {"kind": "task_start", "message": "langgraph audit queued"},
        )

        async def _run() -> None:
            try:
                result = await self.runner.run(
                    request, runtime=runtime, audit_id=request.id
                )
                await self.bus.publish_many(request.id, result.events)
                terminal_ok = result.status in {
                    AuditStatus.COMPLETED,
                    AuditStatus.PARTIAL,
                    AuditStatus.CANCELLED,
                }
                await self.bus.publish_raw(
                    request.id,
                    {
                        "kind": "task_complete" if terminal_ok else "task.error",
                        "message": f"status={result.status.value}",
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("background graph audit failed: %s", request.id)
                await self.bus.publish_raw(
                    request.id,
                    {"kind": "task.error", "message": str(exc)},
                )
            finally:
                await self.bus.close(request.id)

        task = asyncio.create_task(_run(), name=f"graph-audit-{request.id}")
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

        return {
            "id": request.id,
            "project_id": project_id or request.project_id or "",
            "name": name or request.title,
            "status": "pending",
            "current_phase": "planning",
            "runtime": "langgraph",
            "findings_count": 0,
            "message": "accepted; running in background",
        }

    async def cancel(self, task_id: str) -> dict[str, Any]:
        self.runner.request_cancel(task_id)
        row = await self.runner.get(task_id)
        if row and row.status in {
            AuditStatus.COMPLETED,
            AuditStatus.PARTIAL,
        }:
            return {
                "id": task_id,
                "status": row.status.value,
                "message": "already completed",
            }
        # If not started or mid-flight: flag set; if never run, mark cancelled in store
        if row is None:
            from app.services.agent.domain import AuditStatus as AS

            await self.runner.store.upsert_run(task_id, status=AS.CANCELLED)
        await self.bus.publish_raw(
            task_id, {"kind": "task.cancelled", "message": "cancel requested"}
        )
        return {"id": task_id, "status": "cancelled", "message": "cancel requested"}

    async def resume(
        self,
        task_id: str,
        *,
        runtime: Optional[GraphRuntime] = None,
        project_id: Optional[str] = None,
    ) -> dict[str, Any]:
        result = await self.runner.resume(task_id, runtime=runtime)
        await self.bus.publish_many(task_id, result.events)
        return self._result_to_task_dict(
            result, project_id=project_id, name=None
        )

    async def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        row = await self.runner.get(task_id)
        if row is None:
            return None
        tokens = row.usage.total_tokens if row.usage else 0
        analyzed = 0
        total = 0
        if row.graph_snapshot:
            b = row.graph_snapshot.get("budget") or {}
            analyzed = int(b.get("files_analyzed") or 0)
        return task_summary_from_run(
            audit_id=row.audit_id,
            project_id=row.request.project_id if row.request else None,
            status=row.status,
            findings=list(row.findings),
            tokens_used=tokens,
            analyzed_files=analyzed,
            total_files=total,
            error_message=row.error_message,
            created_at=row.created_at,
            completed_at=row.completed_at,
        )

    async def list_findings(self, task_id: str) -> list[dict[str, Any]]:
        row = await self.runner.get(task_id)
        if row is None:
            return []
        return findings_to_api_list(list(row.findings))

    async def list_events(self, task_id: str) -> list[dict[str, Any]]:
        hist = self.bus.history(task_id)
        if hist:
            return hist
        row = await self.runner.get(task_id)
        if row is None:
            return []
        from .api_mapping import graph_event_to_sse

        return [
            graph_event_to_sse(e, task_id=task_id, sequence=i + 1)
            for i, e in enumerate(row.events)
        ]

    def _result_to_task_dict(
        self,
        result: AuditRunResult,
        *,
        project_id: Optional[str],
        name: Optional[str],
    ) -> dict[str, Any]:
        tokens = result.usage.total_tokens if result.usage else 0
        analyzed = 0
        total = 0
        raw = result.raw_state or {}
        budget = raw.get("budget")
        if budget is not None and hasattr(budget, "files_analyzed"):
            analyzed = budget.files_analyzed
        manifest = raw.get("manifest")
        if manifest is not None and hasattr(manifest, "files"):
            total = len(manifest.files)
        summary = task_summary_from_run(
            audit_id=result.audit_id,
            project_id=project_id,
            status=result.status,
            findings=result.findings,
            tokens_used=tokens,
            analyzed_files=analyzed,
            total_files=total,
            name=name,
            error_message=result.record.error_message if result.record else None,
            completed_at=result.record.completed_at if result.record else None,
            created_at=result.record.created_at if result.record else None,
        )
        summary["runtime"] = "langgraph"
        return summary


# Singleton for optional dual-path experiments
_facade: Optional[GraphAuditFacade] = None


def get_graph_facade() -> GraphAuditFacade:
    global _facade
    if _facade is None:
        _facade = GraphAuditFacade()
    return _facade
