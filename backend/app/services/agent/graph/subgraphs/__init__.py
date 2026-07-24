"""Verification subgraph (M7): strategy selection + confidence update.

Phase 1 default: static_recheck / skip → NOT_RUN or SKIPPED without sandbox exec.
When allow_execution and sandbox allowlisted actions: optional confirmation signal.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langchain_core.runnables import RunnableConfig

from app.services.agent.domain import (
    ExecutionStatus,
    Finding,
    FindingStatus,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerifiedFinding,
)
from app.services.agent.sandbox import (
    LocalAllowlistExecutor,
    NullSandboxExecutor,
    SandboxExecutor,
    SandboxRequest,
)

logger = logging.getLogger(__name__)


class VerificationState(TypedDict, total=False):
    finding: Finding
    request: VerificationRequest
    result: VerificationResult
    verified_finding: VerifiedFinding
    sandbox: Any  # not checkpointed ideally; injected via config/runtime
    events: list[dict[str, Any]]


def select_verification_strategy(state: VerificationState) -> dict:
    """Pick strategy based on finding + request flags."""
    finding = state["finding"]
    req = state.get("request")
    if req is None:
        strategy = "static_recheck"
        allow_exec = False
        req = VerificationRequest(
            finding_id=finding.id,
            strategy=strategy,
            allow_execution=False,
        )
    else:
        strategy = req.strategy
        allow_exec = req.allow_execution
        # Phase-1 safety: force no exec unless explicitly allowed
        if not allow_exec and strategy == "sandbox":
            strategy = "static_recheck"
            req = req.model_copy(update={"strategy": strategy})

    return {
        "request": req,
        "events": [
            {
                "kind": "verify.strategy",
                "message": f"strategy={req.strategy} allow_execution={req.allow_execution}",
                "finding_id": finding.id,
            }
        ],
    }


async def verify_finding(
    state: VerificationState, config: Optional[RunnableConfig] = None
) -> dict:
    """Run verification for a single finding."""
    finding = state["finding"]
    req = state["request"]
    sandbox: SandboxExecutor = _get_sandbox(config)

    if req.strategy == "human":
        result = VerificationResult(
            request_id=req.id,
            finding_id=finding.id,
            status=VerificationStatus.PENDING,
            execution_status=ExecutionStatus.SUCCEEDED,
            confidence=finding.confidence,
            summary="awaiting human review",
        )
        return {"result": result, "events": [{"kind": "verify.pending", "message": "human"}]}

    if req.strategy == "heuristic" or (
        req.strategy == "static_recheck" and not req.allow_execution
    ):
        # Static recheck: confidence from evidence quality only
        conf = finding.confidence
        if finding.evidence:
            conf = min(1.0, conf + 0.05 * len(finding.evidence))
        # Phase 1: do not mark CONFIRMED without sandbox — use INCONCLUSIVE or keep NOT_RUN path
        status = VerificationStatus.SKIPPED
        summary = "static_recheck only; sandbox not run (phase1)"
        if req.strategy == "heuristic":
            status = VerificationStatus.INCONCLUSIVE
            summary = "heuristic recheck without execution"
        result = VerificationResult(
            request_id=req.id,
            finding_id=finding.id,
            status=status,
            execution_status=ExecutionStatus.SUCCEEDED,
            confidence=conf,
            summary=summary,
            details={"strategy": req.strategy},
        )
        return {
            "result": result,
            "events": [
                {
                    "kind": "verify.completed",
                    "message": summary,
                    "finding_id": finding.id,
                    "status": status.value,
                }
            ],
        }

    if req.strategy == "sandbox" and req.allow_execution:
        path = finding.location.file_path if finding.location else ""
        sreq = SandboxRequest(
            action="run_static_analyzer",
            args={"path": path},
            timeout_seconds=req.timeout_seconds,
        )
        sres = await sandbox.execute(sreq)
        if sres.policy_denied:
            result = VerificationResult(
                request_id=req.id,
                finding_id=finding.id,
                status=VerificationStatus.ERROR,
                execution_status=ExecutionStatus.POLICY_DENIED,
                confidence=finding.confidence,
                summary=sres.stderr,
            )
        elif sres.status is ExecutionStatus.SUCCEEDED:
            hits = sres.stdout or ""
            confirmed = "hits=" in hits and hits != "hits="
            result = VerificationResult(
                request_id=req.id,
                finding_id=finding.id,
                status=(
                    VerificationStatus.CONFIRMED
                    if confirmed
                    else VerificationStatus.INCONCLUSIVE
                ),
                execution_status=ExecutionStatus.SUCCEEDED,
                confidence=min(1.0, finding.confidence + (0.2 if confirmed else 0.0)),
                summary=hits or "sandbox ok",
                details={"stdout": sres.stdout, "duration_ms": sres.duration_ms},
            )
        else:
            result = VerificationResult(
                request_id=req.id,
                finding_id=finding.id,
                status=VerificationStatus.ERROR,
                execution_status=sres.status,
                confidence=finding.confidence,
                summary=sres.stderr or "sandbox failed",
            )
        return {
            "result": result,
            "events": [
                {
                    "kind": "verify.completed",
                    "message": result.summary,
                    "finding_id": finding.id,
                    "status": result.status.value,
                }
            ],
        }

    # Default: not run
    result = VerificationResult(
        request_id=req.id,
        finding_id=finding.id,
        status=VerificationStatus.NOT_RUN,
        execution_status=ExecutionStatus.SUCCEEDED,
        confidence=finding.confidence,
        summary="verification not run",
    )
    return {"result": result, "events": [{"kind": "verify.skipped", "message": "not_run"}]}


def update_confidence(state: VerificationState) -> dict:
    """Merge verification result into VerifiedFinding."""
    finding = state["finding"]
    result = state["result"]
    status_map_finding = {
        VerificationStatus.CONFIRMED: FindingStatus.VERIFIED,
        VerificationStatus.REJECTED: FindingStatus.FALSE_POSITIVE,
        VerificationStatus.PENDING: FindingStatus.NEEDS_REVIEW,
    }
    f_status = status_map_finding.get(result.status, finding.status)

    data = finding.model_dump()
    data.update(
        {
            "verification_status": result.status,
            "status": f_status,
            "confidence": result.confidence,
            "verification_notes": result.summary,
            "verification_artifact": result.artifact,
        }
    )
    vf = VerifiedFinding(**data)
    return {
        "verified_finding": vf,
        "events": [
            {
                "kind": "verify.confidence",
                "message": f"confidence={result.confidence:.2f}",
                "finding_id": finding.id,
                "verification_status": result.status.value,
            }
        ],
    }


# Optional injection when LangGraph does not pass config into sync nodes
from contextvars import ContextVar

_sandbox_ctx: ContextVar[Optional[SandboxExecutor]] = ContextVar(
    "deepaudit_sandbox", default=None
)


def set_sandbox(sandbox: SandboxExecutor):
    return _sandbox_ctx.set(sandbox)


def reset_sandbox(token) -> None:
    _sandbox_ctx.reset(token)


def _get_sandbox(config: Optional[dict] = None) -> SandboxExecutor:
    if config:
        configurable = {}
        if isinstance(config, dict):
            configurable = config.get("configurable") or {}
        sb = configurable.get("sandbox") if isinstance(configurable, dict) else None
        if sb is not None:
            return sb
    try:
        from langgraph.config import get_config

        cfg = get_config()
        if cfg:
            configurable = cfg.get("configurable") or {}
            sb = configurable.get("sandbox")
            if sb is not None:
                return sb
    except Exception:  # noqa: BLE001
        pass
    ctx = _sandbox_ctx.get()
    if ctx is not None:
        return ctx
    return NullSandboxExecutor()


def build_verification_graph() -> StateGraph:
    g: StateGraph = StateGraph(VerificationState)
    g.add_node("select_verification_strategy", select_verification_strategy)
    g.add_node("verify_finding", verify_finding)
    g.add_node("update_confidence", update_confidence)
    g.add_edge(START, "select_verification_strategy")
    g.add_edge("select_verification_strategy", "verify_finding")
    g.add_edge("verify_finding", "update_confidence")
    g.add_edge("update_confidence", END)
    return g


def compile_verification_graph():
    return build_verification_graph().compile()


async def verify_one(
    finding: Finding,
    *,
    request: Optional[VerificationRequest] = None,
    sandbox: Optional[SandboxExecutor] = None,
) -> VerifiedFinding:
    """Convenience: run verification subgraph for a single finding."""
    app = compile_verification_graph()
    sb = sandbox or NullSandboxExecutor()
    init: VerificationState = {
        "finding": finding,
        "events": [],
    }
    if request is not None:
        init["request"] = request
    token = set_sandbox(sb)
    try:
        result = await app.ainvoke(
            init,
            {"configurable": {"thread_id": f"verify-{finding.id}", "sandbox": sb}},
        )
    finally:
        reset_sandbox(token)
    return result["verified_finding"]


async def verify_findings(
    findings: list[Finding],
    *,
    allow_execution: bool = False,
    sandbox: Optional[SandboxExecutor] = None,
) -> list[VerifiedFinding]:
    """Batch verify. Default Phase 1: static skip path."""
    out: list[VerifiedFinding] = []
    sb = sandbox or (
        LocalAllowlistExecutor() if allow_execution else NullSandboxExecutor()
    )
    for f in findings:
        req = VerificationRequest(
            finding_id=f.id,
            strategy="sandbox" if allow_execution else "static_recheck",
            allow_execution=allow_execution,
        )
        out.append(await verify_one(f, request=req, sandbox=sb))
    return out
