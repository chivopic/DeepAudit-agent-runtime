"""M7 verification subgraph tests."""

from __future__ import annotations

import pytest

from app.services.agent.domain import (
    ExecutionStatus,
    Finding,
    FindingStatus,
    Severity,
    SourceLocation,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
)
from app.services.agent.graph.subgraphs import (
    compile_verification_graph,
    verify_findings,
    verify_one,
)
from app.services.agent.sandbox import LocalAllowlistExecutor, NullSandboxExecutor


def _finding(**kw) -> Finding:
    base = dict(
        title="Command Injection",
        description="os.system",
        severity=Severity.HIGH,
        location=SourceLocation(file_path="app.py", start_line=3),
        confidence=0.55,
        verification_status=VerificationStatus.NOT_RUN,
    )
    base.update(kw)
    return Finding(**base)


@pytest.mark.asyncio
async def test_phase1_static_recheck_skips_sandbox():
    f = _finding()
    vf = await verify_one(f, sandbox=NullSandboxExecutor())
    assert vf.verification_status in {
        VerificationStatus.SKIPPED,
        VerificationStatus.NOT_RUN,
        VerificationStatus.INCONCLUSIVE,
    }
    assert vf.verification_notes


def test_verification_result_default_execution_not_succeeded():
    r = VerificationResult(request_id="r1", finding_id="f1")
    assert r.execution_status is ExecutionStatus.SKIPPED
    assert r.execution_status is not ExecutionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_sandbox_confirm_with_allowlist():
    files = {"app.py": "import os\nos.system(cmd)\n"}
    sb = LocalAllowlistExecutor(files=files)
    f = _finding()
    req = VerificationRequest(
        finding_id=f.id,
        strategy="sandbox",
        allow_execution=True,
    )
    vf = await verify_one(f, request=req, sandbox=sb)
    assert vf.verification_status is VerificationStatus.CONFIRMED
    assert vf.status is FindingStatus.VERIFIED
    assert vf.confidence >= f.confidence


@pytest.mark.asyncio
async def test_sandbox_clean_is_inconclusive():
    files = {"app.py": "print('ok')\n"}
    sb = LocalAllowlistExecutor(files=files)
    f = _finding()
    req = VerificationRequest(
        finding_id=f.id,
        strategy="sandbox",
        allow_execution=True,
    )
    vf = await verify_one(f, request=req, sandbox=sb)
    assert vf.verification_status is VerificationStatus.INCONCLUSIVE


@pytest.mark.asyncio
async def test_policy_denied_without_allowlisted_action():
    # Force a denied action by using strategy sandbox but executor that denies analyzer
    from app.services.agent.sandbox import SandboxPolicy, LocalAllowlistExecutor

    policy = SandboxPolicy(allowed_actions=frozenset({"noop"}))
    sb = LocalAllowlistExecutor(policy=policy, files={"app.py": "os.system(x)"})
    f = _finding()
    req = VerificationRequest(
        finding_id=f.id, strategy="sandbox", allow_execution=True
    )
    vf = await verify_one(f, request=req, sandbox=sb)
    assert vf.verification_status is VerificationStatus.ERROR


@pytest.mark.asyncio
async def test_batch_verify_findings_phase1():
    findings = [_finding(title="A"), _finding(title="B")]
    out = await verify_findings(findings, allow_execution=False)
    assert len(out) == 2
    for vf in out:
        assert vf.verification_status != VerificationStatus.CONFIRMED


@pytest.mark.asyncio
async def test_graph_compile_and_invoke():
    app = compile_verification_graph()
    f = _finding()
    state = await app.ainvoke(
        {"finding": f, "events": []},
        {
            "configurable": {
                "thread_id": "v1",
                "sandbox": NullSandboxExecutor(),
            }
        },
    )
    assert state["verified_finding"].id == f.id
    assert state["result"] is not None
