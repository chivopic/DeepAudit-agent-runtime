"""Conditional verification step for the main audit graph.

The node is only reached when the request sets ``enable_verification``.
The disabled route adds no graph step. The enabled path uses the in-process
allowlist static analyzer. It does not start Docker or a shell.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig

from app.services.agent.domain import AuditStatus, VerificationStatus
from app.services.agent.graph.runtime import get_runtime
from app.services.agent.graph.state import AuditState
from app.services.agent.graph.subgraphs import verify_findings
from app.services.agent.sandbox import LocalAllowlistExecutor, NullSandboxExecutor


async def verify_audit_findings(
    state: AuditState, config: RunnableConfig | None = None
) -> dict[str, Any]:
    """Confirm pattern hits in memory. Docker remains a separate placeholder."""
    runtime = get_runtime(config)
    findings = list(state.get("normalized_findings") or [])
    files = dict(runtime.extra.get("fixture_files") or {})
    sandbox = LocalAllowlistExecutor(files=files) if files else NullSandboxExecutor()
    verified = await verify_findings(findings, allow_execution=True, sandbox=sandbox)
    confirmed = sum(
        1 for item in verified if item.verification_status is VerificationStatus.CONFIRMED
    )
    return {
        "status": AuditStatus.VERIFYING,
        "normalized_findings": verified,
        "events": [
            {
                "kind": "node.completed",
                "message": "verify_audit_findings",
                "confirmed": confirmed,
                "count": len(verified),
                "worker": "local_allowlist",
            }
        ],
    }
