"""Run one product audit on the LangGraph harness.

The web task layer supplies an authorized project root, the user's LLM
config, and callbacks for events and cancellation. This module does not
open a database session and does not accept a client host path.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.services.agent.application.activity_log import (
    finding_log_lines,
    progress_counts,
    user_node_message,
    user_outcome_message,
)
from app.services.agent.application.assembly import assemble_audit_runtime
from app.services.agent.application.project_source import (
    AuthorizedSnapshot,
    ProjectSourceError,
    load_authorized_snapshot,
)
from app.services.agent.application.runner import AuditRunner
from app.services.agent.domain import (
    ArtifactKind,
    ArtifactRef,
    AuditRequest,
    AuditStatus,
    RepositoryRef,
    RunBudget,
)
from app.services.agent.domain.mappers import finding_to_legacy_dict
from app.services.agent.graph.gateway import LLMServiceGateway
from app.services.agent.persistence.artifact_store import FilesystemArtifactStore
from app.services.agent.persistence.control import FileControlPlane
from app.services.agent.persistence.file_checkpointer import FileCheckpointSaver
from app.services.agent.persistence.sqlite_store import SqliteBusinessStore

logger = logging.getLogger(__name__)

EventSink = Any
CancelProbe = Callable[[], bool]


async def run_product_graph_audit(
    *,
    task_id: str,
    project_root: str | Path,
    user_config: dict[str, Any] | None,
    target_files: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    graph_verification: bool = False,
    is_cancelled: CancelProbe | None = None,
    event_sink: EventSink | None = None,
    state_dir: str | Path = "./data/agent_runtime",
    max_files: int = 100,
    max_bytes: int = 2_000_000,
    token_budget: int = 100_000,
    resume: bool = False,
    owner: str | None = None,
    control: FileControlPlane | None = None,
    checkpoint_backend: str = "file",
    control_backend: str = "file",
) -> dict[str, Any]:
    """Execute or continue a graph audit. Returns a JSON-friendly summary."""
    root = Path(project_root)
    state_root = Path(state_dir)
    validate_product_backends(checkpoint_backend, control_backend)
    control = control or FileControlPlane(state_root / "control")
    holder = owner or f"worker-{uuid.uuid4().hex[:8]}"
    control.acquire(task_id, holder)
    started = time.monotonic()
    try:
        artifacts = FilesystemArtifactStore(state_root / "artifacts")
        store = SqliteBusinessStore(state_root / "audits.sqlite3")
        if resume:
            row = await store.get(task_id)
            if row is None or row.request is None:
                raise ProjectSourceError("找不到原审计记录，请新建任务。")
            request = row.request
            saved_ref = request.config.get("source_snapshot")
            if not saved_ref:
                raise ProjectSourceError("旧任务没有保存源码快照，请新建任务。")
            ref = ArtifactRef.model_validate(saved_ref)
            raw = await artifacts.get_bytes(ref)
            if not ref.content_hash or hashlib.sha256(raw).hexdigest() != ref.content_hash:
                raise ProjectSourceError("已保存的源码快照校验失败，请新建任务。")
            snapshot = AuthorizedSnapshot.model_validate_json(raw)
            graph_verification = request.enable_verification
        else:
            snapshot = load_authorized_snapshot(
                root,
                max_files=max_files,
                max_bytes=max_bytes,
                target_files=target_files,
                exclude_patterns=exclude_patterns,
            )
            ref = await artifacts.put(
                snapshot.model_dump_json(),
                kind=ArtifactKind.FULL_FILE,
                media_type="application/json",
                audit_id=task_id,
                suffix=".json",
            )
            request = AuditRequest(
                id=task_id,
                repository=RepositoryRef(
                    source_type="local",
                    local_path="project://authorized",
                    metadata={"project_root_name": root.name},
                ),
                budget=RunBudget(
                    max_tokens=token_budget,
                    max_model_calls=max(8, max_files * 2),
                    max_files=max_files,
                ),
                enable_verification=graph_verification,
                config={
                    "engine": "graph",
                    "source_snapshot": ref.model_dump(mode="json"),
                    "source_coverage": snapshot.coverage(),
                },
            )
        files = snapshot.files
        files_total = snapshot.discovered_files
        gateway, gateway_note = _gateway_from_user_config(user_config)
        if gateway_note:
            await _emit(event_sink, gateway_note)
        runner = AuditRunner(
            store=store,
            artifacts=artifacts,
            checkpointer=FileCheckpointSaver(state_root / "checkpoints" / f"{_safe(task_id)}.pkl"),
            checkpointer_backend="memory",
        )
        runtime_holder = assemble_audit_runtime(
            files=files,
            gateway=gateway,
            offline=gateway is None,
            parallel=1,
            cross_file=len(files) > 1,
            pattern_scan=True,
            verify=graph_verification,
            runner=runner,
        )
        if not resume:
            request.config["model_unavailable"] = gateway is None

        seen_steps: set[str] = set()

        async def on_update(chunk: dict[str, Any], values: dict[str, Any]) -> None:
            control.renew(task_id, holder)
            if is_cancelled is not None and is_cancelled():
                runner.request_cancel(task_id)
            if not isinstance(chunk, dict):
                return
            for node_name in chunk:
                if not isinstance(node_name, str) or node_name.startswith("__"):
                    continue
                text = user_node_message(node_name, values, file_total=len(files))
                if text is None:
                    continue
                if node_name == "analyze_file":
                    analyzed, total = progress_counts(values, fallback_total=len(files))
                    await _emit_progress(event_sink, analyzed, total, text)
                    continue
                if node_name in seen_steps:
                    continue
                seen_steps.add(node_name)
                await _emit(event_sink, text)

        graph_runtime = runtime_holder._build_runtime()
        graph_runtime.cancel_check = lambda: control.is_cancelled(task_id) or bool(
            is_cancelled and is_cancelled()
        )
        if resume:
            result = await runner.resume(task_id, runtime=graph_runtime, on_update=on_update)
        else:
            result = await runner.run(
                request,
                runtime=graph_runtime,
                audit_id=task_id,
                on_update=on_update,
            )
        findings = [finding_to_legacy_dict(item) for item in result.findings]
        for line in finding_log_lines(findings):
            severity = line.get("severity")
            await _emit(
                event_sink,
                str(line.get("message") or ""),
                {"severity": severity} if severity else None,
            )
        status = result.status
        task_status = _task_status(status)
        files_analyzed = _files_analyzed(result.raw_state)
        error = result.record.error_message if result.record else None
        verified = sum(1 for item in findings if item.get("is_verified"))
        duration_ms = int((time.monotonic() - started) * 1000)
        user_message = user_outcome_message(
            status=task_status,
            files_analyzed=files_analyzed,
            files_total=files_total,
            findings=findings,
            duration_ms=duration_ms,
            coverage=_coverage_from_result(result),
            verification_enabled=graph_verification,
            verified_count=verified,
            error=error,
        )
        return {
            "status": status.value,
            "task_status": task_status,
            "findings": findings,
            "files_total": files_total,
            "files_analyzed": files_analyzed,
            "tokens": int(result.usage.total_tokens) if result.usage else 0,
            "error": error,
            "summary": result.report.summary if result.report else "",
            "duration_ms": duration_ms,
            "user_message": user_message,
            "coverage": _coverage_from_result(result),
            "source_snapshot_hash": ref.content_hash,
        }
    finally:
        control.release(task_id, holder)


def validate_product_backends(checkpoint_backend: str, control_backend: str) -> None:
    """The product currently supports durable execution on one host only."""
    if checkpoint_backend != "file":
        raise RuntimeError(
            f"AGENT_CHECKPOINT_BACKEND={checkpoint_backend} is not supported by the product; "
            "set AGENT_CHECKPOINT_BACKEND=file"
        )
    if control_backend != "file":
        raise RuntimeError(
            f"AGENT_CONTROL_BACKEND={control_backend} is not supported by the product; "
            "set AGENT_CONTROL_BACKEND=file"
        )


def _gateway_from_user_config(
    user_config: dict[str, Any] | None,
) -> tuple[LLMServiceGateway | None, str | None]:
    try:
        from app.services.llm.service import LLMService

        service = LLMService(user_config=user_config)
        config = service.config
    except Exception as exc:  # noqa: BLE001
        logger.info("LLM config unavailable: %s", type(exc).__name__)
        return None, "没有可用的模型配置，这次只做代码模式扫描。"
    api_key = getattr(config, "api_key", "") or ""
    if not api_key:
        return None, "没有配置模型密钥，这次只做代码模式扫描。"
    provider = getattr(getattr(config, "provider", None), "value", None) or "gateway"
    model = getattr(config, "model", None) or "gateway"
    timeout = float(getattr(config, "timeout", 120) or 120)
    return (
        LLMServiceGateway(
            service,
            provider=str(provider),
            model=str(model),
            timeout_seconds=min(timeout, 180.0),
        ),
        None,
    )


def _files_analyzed(raw_state: dict[str, Any] | None) -> int:
    if not isinstance(raw_state, dict):
        return 0
    budget = raw_state.get("budget")
    if isinstance(budget, dict):
        return int(budget.get("files_analyzed") or 0)
    return int(getattr(budget, "files_analyzed", 0) or 0)


def _task_status(status: AuditStatus) -> str:
    if status is AuditStatus.PARTIAL:
        return "partial"
    if status is AuditStatus.PAUSED:
        return "paused"
    if status is AuditStatus.CANCELLED:
        return "cancelled"
    if status is AuditStatus.FAILED:
        return "failed"
    if status is AuditStatus.COMPLETED:
        return "completed"
    return "running"


def _safe(audit_id: str) -> str:
    cleaned = "".join(ch for ch in audit_id if ch.isalnum() or ch in {"-", "_"})
    return cleaned or "audit"


def _coverage_from_result(result: Any) -> dict[str, Any]:
    report = getattr(result, "report", None)
    meta = getattr(report, "metadata", None)
    if not isinstance(meta, dict):
        return {}
    coverage = meta.get("coverage")
    merged = dict(coverage) if isinstance(coverage, dict) else {}
    if meta.get("budget_exhausted"):
        merged["budget_exhausted"] = True
    return merged


async def _emit(
    sink: EventSink | None,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if sink is None or not message:
        return
    fn: Callable[..., Awaitable[Any]] | None = getattr(sink, "emit_info", None)
    if fn is None:
        return
    if metadata:
        await fn(message, metadata)
        return
    await fn(message)


async def _emit_progress(sink: EventSink | None, current: int, total: int, message: str) -> None:
    if sink is None or not message:
        return
    fn: Callable[..., Awaitable[Any]] | None = getattr(sink, "emit_progress", None)
    if fn is not None:
        await fn(current, total, message)
        return
    await _emit(sink, message)
