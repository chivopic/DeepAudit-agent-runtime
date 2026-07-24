"""M5 Context Manager tests."""

from __future__ import annotations

import pytest

from app.services.agent.context import ContextManager, estimate_tokens
from app.services.agent.domain import (
    Finding,
    RunBudget,
    Severity,
    SourceLocation,
    VerificationStatus,
)
from app.services.agent.persistence import InMemoryArtifactStore


@pytest.mark.asyncio
async def test_build_context_includes_pinned_findings():
    cm = ContextManager(artifacts=InMemoryArtifactStore())
    findings = [
        Finding(
            title="SQLi",
            description="d",
            severity=Severity.HIGH,
            location=SourceLocation(file_path="a.py", start_line=1),
            verification_status=VerificationStatus.NOT_RUN,
        )
    ]
    pack = await cm.build_context(
        audit_id="aud1",
        pinned={"audit_id": "aud1", "repo": "demo"},
        active="print('hello')\n" * 10,
        running_summary="scanning auth",
        findings=findings,
        pending_task_ids=["t1", "t2"],
        security_policy="no network; no raw shell",
        max_context_tokens=50_000,
    )
    names = pack.layer_names()
    assert "pinned" in names
    assert "findings_index" in names
    assert "pending_tasks" in names
    assert "security_policy" in names
    assert "active" in names
    assert pack.total_tokens_est > 0
    assert "SQLi" in pack.as_prompt_block()


@pytest.mark.asyncio
async def test_compact_never_drops_pinned():
    cm = ContextManager(default_budget_tokens=100)
    huge = "x" * 20_000
    pack = await cm.build_context(
        audit_id="a",
        pinned={"k": "must keep"},
        active=huge,
        retrieved=[huge, huge],
        findings=[
            Finding(
                title="KeepMe",
                description="d",
                severity=Severity.LOW,
                verification_status=VerificationStatus.NOT_RUN,
            )
        ],
        max_context_tokens=200,
    )
    assert pack.compacted
    text = pack.as_prompt_block()
    assert "must keep" in text
    assert "KeepMe" in text
    # unpinned should be reduced
    assert pack.total_tokens_est <= 250


@pytest.mark.asyncio
async def test_offload_large_active_to_artifact():
    store = InMemoryArtifactStore()
    cm = ContextManager(artifacts=store, offload_threshold_chars=100)
    big = "LINE\n" * 100
    pack = await cm.build_context(
        audit_id="a",
        active=big,
        max_context_tokens=100_000,
    )
    active = next(l for l in pack.layers if l.name == "active")
    assert active.refs
    assert "offloaded" in active.text
    raw = await store.get_bytes(active.refs[0])
    assert raw.decode("utf-8") == big


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1


@pytest.mark.asyncio
async def test_budget_caps_context():
    cm = ContextManager(default_budget_tokens=10_000)
    pack = await cm.build_context(
        audit_id="a",
        active="y" * 50_000,
        budget=RunBudget(max_tokens=1000, tokens_used=900),
        max_context_tokens=None,
    )
    # remaining tokens = 100 → compact aggressively
    assert pack.total_tokens_est <= 150
