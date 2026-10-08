"""AuditRunner: run / resume / cancel audit graphs (M3)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, cast

from app.core.config import settings
from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    Finding,
    ModelUsage,
)
from app.services.agent.graph.builder import compile_audit_graph
from app.services.agent.graph.limits import recursion_limit_for_budget
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
from app.services.agent.persistence.checkpointer import (
    CheckpointerBackend,
    acreate_checkpointer,
    create_checkpointer,
)

logger = logging.getLogger(__name__)


# Nodes outside the per-file loop (validate, ingest, manifest, plan, aggregate,
# dedupe, prioritize, report) plus routing headroom.
_GRAPH_FIXED_STEPS = 12


def _recursion_limit(budget: Any) -> int:
    """Derive LangGraph's recursion limit from the run budget.

    Every ``analyze_file`` iteration is a super-step, so LangGraph's default of
    25 caps an audit at roughly twenty files — a real repository dies with
    GraphRecursionError long before the budget is spent. Size the limit from
    whichever budget cap actually bounds the loop.

    Erring high is safe: the budget still stops the run. Erring low kills it.
    """
    max_files = int(getattr(budget, "max_files", 0) or 0)
    max_model_calls = int(getattr(budget, "max_model_calls", 0) or 0)
    per_file = max(max_files, max_model_calls)
    limit = _GRAPH_FIXED_STEPS + 2 * per_file
    cap = int(getattr(settings, "GRAPH_AUDITS_RECURSION_LIMIT_CAP", 2000))
    return max(25, min(limit, cap))


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
    # File count must survive into the snapshot: start is async by default, so
    # clients read the persisted row, not the in-memory result.
    manifest = result.get("manifest")
    if manifest is not None and hasattr(manifest, "files"):
        snap["total_files"] = len(manifest.files)
    return snap


async def _checkpoint_values(app: Any, audit_id: str) -> dict[str, Any]:
    """Last successful graph checkpoint, if the checkpointer still has it."""
    try:
        snap = await app.aget_state({"configurable": {"thread_id": audit_id}})
    except Exception as exc:  # noqa: BLE001
        logger.debug("checkpoint read failed: %s", exc)
        return {}
    values = getattr(snap, "values", None) if snap is not None else None
    if not values:
        return {}
    return dict(values)


async def _findings_from_checkpoint(values: dict[str, Any]) -> list[Finding]:
    """Prefer normalized findings; otherwise promote candidates already produced."""
    findings = list(values.get("normalized_findings") or [])
    if findings:
        return findings
    if not values.get("candidate_findings"):
        return []
    try:
        from app.services.agent.graph.nodes import aggregate_findings

        aggregated = await aggregate_findings(values)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001
        logger.debug("candidate salvage failed: %s", exc)
        return []
    return list(aggregated.get("normalized_findings") or [])


@dataclass
class AuditRunResult:
    audit_id: str
    status: AuditStatus
    findings: list[Finding] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    report: Any = None
    usage: ModelUsage | None = None
    record: PersistedAuditRecord | None = None
    raw_state: dict[str, Any] = field(default_factory=dict)


class AuditRunner:
    """Coordinates graph compile → invoke → business persistence.

    Cancel is cooperative: sets a flag on an in-process map (M4 hardens).
    """

    def __init__(
        self,
        *,
        store: BusinessAuditStore | None = None,
        artifacts: ArtifactStore | None = None,
        checkpointer: Any = None,
        checkpointer_backend: str | None = None,
    ) -> None:
        self.store: BusinessAuditStore = store or InMemoryBusinessStore()
        self.artifacts: ArtifactStore = artifacts or InMemoryArtifactStore()
        self._checkpointer = checkpointer
        # Default comes from configuration rather than being pinned to
        # "memory" in the constructor — that pin is why production ran with an
        # in-process dict for every milestone since M3.
        self._checkpointer_backend = cast(
            CheckpointerBackend,
            checkpointer_backend or getattr(settings, "AGENT_CHECKPOINT_BACKEND", "auto"),
        )
        self._cancel_flags: dict[str, bool] = {}
        self._compiled = None

    async def _aget_graph(self) -> Any:
        """Compile once, opening the checkpointer's pool if it needs one.

        Postgres is async to construct, so compilation moved behind an await.
        """
        if self._compiled is None:
            cp = self._checkpointer
            if cp is None:
                cp = await acreate_checkpointer(
                    backend=self._checkpointer_backend,
                )
            self._checkpointer = cp
            self._compiled = compile_audit_graph(checkpointer=cp)
        return self._compiled

    def _graph(self) -> Any:
        """Synchronous compile. Memory/sqlite only — kept for existing callers."""
        if self._compiled is None:
            cp = self._checkpointer or create_checkpointer(backend=self._checkpointer_backend)
            self._checkpointer = cp
            self._compiled = compile_audit_graph(checkpointer=cp)
        return self._compiled

    def request_cancel(self, audit_id: str) -> None:
        self._cancel_flags[audit_id] = True

    def is_cancelled(self, audit_id: str) -> bool:
        return bool(self._cancel_flags.get(audit_id))

    def _invoke_config(
        self, audit_id: str, runtime: GraphRuntime, request: AuditRequest | None
    ) -> dict[str, Any]:
        budget = request.budget if request is not None else None
        limit = (
            min(recursion_limit_for_budget(budget), _recursion_limit(budget))
            if budget is not None
            else _recursion_limit(None)
        )
        return {
            "recursion_limit": limit,
            "durability": "sync",
            "configurable": {
                "thread_id": audit_id,
                "runtime": runtime,
            },
        }

    async def run(
        self,
        request: AuditRequest,
        *,
        runtime: GraphRuntime | None = None,
        audit_id: str | None = None,
        on_update: Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]] | None = None,
        stop_when: Callable[[dict[str, Any]], bool] | None = None,
    ) -> AuditRunResult:
        aid = audit_id or request.id
        runtime = runtime or GraphRuntime(offline=True)
        # Wire cooperative cancel into runtime so nodes/routing can observe it.
        external_cancel = runtime.cancel_check
        runtime = replace(
            runtime,
            cancel_check=lambda: self.is_cancelled(aid)
            or bool(external_cancel and external_cancel()),
        )
        await self.store.upsert_run(aid, request=request, status=AuditStatus.PENDING)

        state = empty_audit_state(audit_id=aid, request=request, thread_id=aid)
        if runtime.is_cancelled():
            await self.store.save_result(
                aid,
                status=AuditStatus.CANCELLED,
                findings=[],
                events=[{"kind": "task.cancelled", "message": "cancelled before start"}],
            )
            return AuditRunResult(audit_id=aid, status=AuditStatus.CANCELLED)

        token = set_runtime(runtime)
        app = None
        try:
            app = await self._aget_graph()
            config = self._invoke_config(aid, runtime, request)
            if on_update is None and stop_when is None:
                result = await app.ainvoke(state, config)
            else:
                stopped = False
                async for chunk in app.astream(state, config, stream_mode="updates"):
                    values = await _checkpoint_values(app, aid)
                    update = chunk if isinstance(chunk, dict) else {"update": chunk}
                    if on_update is not None:
                        await on_update(update, values)
                    if stop_when is not None and stop_when(values):
                        stopped = True
                        break
                if stopped:
                    salvaged = await _checkpoint_values(app, aid)
                    findings = await _findings_from_checkpoint(salvaged)
                    usage = salvaged.get("usage")
                    rec = await self.store.save_result(
                        aid,
                        status=AuditStatus.PAUSED,
                        findings=findings,
                        events=list(salvaged.get("events") or []),
                        report=salvaged.get("report"),
                        usage=usage if isinstance(usage, ModelUsage) else None,
                        graph_snapshot=_serialize_snapshot(salvaged) if salvaged else None,
                    )
                    return AuditRunResult(
                        audit_id=aid,
                        status=AuditStatus.PAUSED,
                        findings=findings,
                        events=list(salvaged.get("events") or []),
                        usage=usage if isinstance(usage, ModelUsage) else None,
                        record=rec,
                        raw_state=salvaged,
                    )
                result = await _checkpoint_values(app, aid)
        except Exception as exc:  # noqa: BLE001
            logger.exception("audit run failed: %s", aid)
            salvaged = await _checkpoint_values(app, aid) if app is not None else {}
            findings = await _findings_from_checkpoint(salvaged)
            events = list(salvaged.get("events") or [])
            events.append({"kind": "task.error", "message": str(exc)})
            usage = salvaged.get("usage")
            rec = await self.store.save_result(
                aid,
                status=AuditStatus.FAILED,
                findings=findings,
                events=events,
                report=salvaged.get("report"),
                usage=usage if isinstance(usage, ModelUsage) else None,
                graph_snapshot=_serialize_snapshot(salvaged) if salvaged else None,
                error_message=str(exc),
            )
            await self.store.upsert_findings(aid, findings)
            return AuditRunResult(
                audit_id=aid,
                status=AuditStatus.FAILED,
                findings=findings,
                events=rec.events,
                report=salvaged.get("report"),
                usage=usage if isinstance(usage, ModelUsage) else None,
                record=rec,
                raw_state=salvaged,
            )
        finally:
            reset_runtime(token)

        return await self._persist(aid, result, runtime=runtime)

    async def _persist(
        self,
        aid: str,
        result: dict[str, Any],
        *,
        runtime: GraphRuntime | None = None,
        resumed: bool = False,
    ) -> AuditRunResult:
        """Turn a finished graph state into a stored record.

        Shared by ``run`` and ``resume`` so a resumed run is persisted exactly
        like a fresh one — two copies of this would drift, and the drift would
        only show up on the recovery path nobody exercises.
        """
        status = result.get("status") or AuditStatus.COMPLETED
        if not isinstance(status, AuditStatus):
            try:
                status = AuditStatus(str(status))
            except ValueError:
                status = AuditStatus.COMPLETED

        # Prefer explicit cancel flag / runtime cancel over a completed/partial report.
        if (
            result.get("cancelled")
            or self.is_cancelled(aid)
            or (runtime is not None and runtime.is_cancelled())
        ):
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

        if resumed:
            events = events + [{"kind": "task.resumed", "message": "continued from checkpoint"}]

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
        runtime: GraphRuntime | None = None,
        on_update: Callable[[dict[str, Any], dict[str, Any]], Awaitable[None]] | None = None,
    ) -> AuditRunResult:
        """Continue the saved graph with the same cancellation and event hooks as run."""
        row = await self.store.get(audit_id)
        if row is None:
            raise KeyError(f"unknown audit_id: {audit_id}")

        if row.status in {
            AuditStatus.COMPLETED,
            AuditStatus.PARTIAL,
            AuditStatus.CANCELLED,
        }:
            return AuditRunResult(
                audit_id=audit_id,
                status=row.status,
                findings=list(row.findings),
                events=list(row.events),
                report=row.report,
                usage=row.usage,
                record=row,
                raw_state=row.graph_snapshot,
            )

        # Non-terminal work continues from the last checkpoint. A second runner
        # with the same checkpointer must not start the graph over.
        runtime = runtime or GraphRuntime(offline=True)
        external_cancel = runtime.cancel_check
        runtime = replace(
            runtime,
            cancel_check=lambda: self.is_cancelled(audit_id)
            or bool(external_cancel and external_cancel()),
        )
        token = set_runtime(runtime)
        app = None
        try:
            app = await self._aget_graph()
            snap = await app.aget_state({"configurable": {"thread_id": audit_id}})
            if snap and snap.values:
                if runtime.is_cancelled():
                    result = dict(snap.values)
                    result["cancelled"] = True
                    result["normalized_findings"] = await _findings_from_checkpoint(result)
                elif not tuple(getattr(snap, "next", ()) or ()):
                    result = snap.values
                elif on_update is None:
                    result = await app.ainvoke(
                        None,
                        self._invoke_config(audit_id, runtime, row.request),
                    )
                else:
                    async for chunk in app.astream(
                        None,
                        self._invoke_config(audit_id, runtime, row.request),
                        stream_mode="updates",
                    ):
                        values = await _checkpoint_values(app, audit_id)
                        update = chunk if isinstance(chunk, dict) else {"update": chunk}
                        await on_update(update, values)
                    result = await _checkpoint_values(app, audit_id)
                if runtime.is_cancelled():
                    self.request_cancel(audit_id)
                return await self._persist(audit_id, result, runtime=runtime, resumed=True)
            raise RuntimeError("saved checkpoint is missing; start a new audit")
        except Exception as exc:  # noqa: BLE001
            logger.exception("checkpoint resume failed: %s", audit_id)
            salvaged = await _checkpoint_values(app, audit_id) if app is not None else {}
            findings = await _findings_from_checkpoint(salvaged)
            usage = salvaged.get("usage") or row.usage
            rec = await self.store.save_result(
                audit_id,
                status=AuditStatus.FAILED,
                findings=findings or list(row.findings),
                events=list(salvaged.get("events") or row.events),
                report=salvaged.get("report") or row.report,
                usage=usage if isinstance(usage, ModelUsage) else None,
                graph_snapshot=_serialize_snapshot(salvaged) if salvaged else row.graph_snapshot,
                error_message=str(exc),
            )
            return AuditRunResult(
                audit_id=audit_id,
                status=AuditStatus.FAILED,
                findings=list(rec.findings),
                events=rec.events,
                report=rec.report,
                usage=rec.usage,
                record=rec,
                raw_state=salvaged or row.graph_snapshot,
            )
        finally:
            reset_runtime(token)

    async def get(self, audit_id: str) -> PersistedAuditRecord | None:
        return await self.store.get(audit_id)
