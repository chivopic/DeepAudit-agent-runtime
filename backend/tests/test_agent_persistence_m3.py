"""M3: durable execution — checkpointer + business store + AuditRunner."""

from __future__ import annotations

import pytest

from app.services.agent.domain import (
    ArtifactKind,
    AuditRequest,
    AuditStatus,
    Finding,
    RepositoryRef,
    RunBudget,
    Severity,
    SourceLocation,
    VerificationStatus,
)
from app.services.agent.application import AuditRunner
from app.services.agent.graph.llm import FakeLLM
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.persistence import (
    FilesystemArtifactStore,
    InMemoryArtifactStore,
    InMemoryBusinessStore,
    create_checkpointer,
)


FIXTURE = {
    "svc/db.py": "q = 'SELECT * FROM t WHERE id=' + x\nos.system(cmd)\n",
}


def _req(**kw) -> AuditRequest:
    base = dict(
        repository=RepositoryRef(source_type="local", local_path="/tmp/x"),
        budget=RunBudget(max_tokens=20_000, max_model_calls=20),
    )
    base.update(kw)
    return AuditRequest(**base)


def _runtime() -> GraphRuntime:
    return GraphRuntime(
        llm=FakeLLM(),
        offline=True,
        extra={"fixture_files": FIXTURE},
    )


@pytest.mark.asyncio
async def test_create_checkpointer_memory():
    cp = create_checkpointer(backend="memory")
    assert cp is not None


@pytest.mark.asyncio
async def test_business_store_idempotent_findings():
    store = InMemoryBusinessStore()
    f = Finding(
        title="SQLi",
        description="concat",
        severity=Severity.HIGH,
        location=SourceLocation(file_path="a.py", start_line=1),
        fingerprint="fp-abc",
        verification_status=VerificationStatus.NOT_RUN,
    )
    n1 = await store.upsert_findings("aud1", [f])
    n2 = await store.upsert_findings("aud1", [f])
    assert n1 == 1
    assert n2 == 0
    row = await store.get("aud1")
    assert row and len(row.findings) == 1


@pytest.mark.asyncio
async def test_artifact_store_roundtrip(temp_dir):
    mem = InMemoryArtifactStore()
    ref = await mem.put("hello", kind=ArtifactKind.LOG, media_type="text/plain")
    assert (await mem.get_bytes(ref)) == b"hello"

    fs = FilesystemArtifactStore(temp_dir)
    ref2 = await fs.put("world", kind=ArtifactKind.REPORT, audit_id="a1", suffix=".md")
    assert (await fs.get_bytes(ref2)) == b"world"


@pytest.mark.asyncio
async def test_audit_runner_end_to_end():
    runner = AuditRunner(store=InMemoryBusinessStore(), checkpointer_backend="memory")
    req = _req()
    result = await runner.run(req, runtime=_runtime())
    assert result.status is AuditStatus.COMPLETED
    assert result.audit_id == req.id
    assert all(f.verification_status is VerificationStatus.NOT_RUN for f in result.findings)
    # Persisted
    row = await runner.get(req.id)
    assert row is not None
    assert row.status is AuditStatus.COMPLETED
    assert len(row.events) >= 1
    # Idempotent re-save
    n = await runner.store.upsert_findings(req.id, result.findings)
    assert n == 0


@pytest.mark.asyncio
async def test_audit_runner_cancel_before_start():
    runner = AuditRunner()
    req = _req()
    runner.request_cancel(req.id)
    result = await runner.run(req, runtime=_runtime())
    assert result.status is AuditStatus.CANCELLED


class _CancelOnFirstLLM(FakeLLM):
    """Flip runner cancel flag after first model call so the loop observes mid-run cancel."""

    def __init__(self, runner: AuditRunner, audit_id: str) -> None:
        super().__init__()
        self._runner = runner
        self._audit_id = audit_id
        self._n = 0

    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self._n += 1
        if self._n >= 1:
            self._runner.request_cancel(self._audit_id)
        return await super().complete(messages, **kwargs)


@pytest.mark.asyncio
async def test_audit_runner_cooperative_cancel_mid_analyze():
    multi = {
        "a.py": "os.system(cmd)\n",
        "b.py": "eval(user_input)\n",
        "c.py": "SELECT * FROM t WHERE id=' + x\n",
    }
    runner = AuditRunner(store=InMemoryBusinessStore(), checkpointer_backend="memory")
    req = _req()
    rt = GraphRuntime(
        llm=_CancelOnFirstLLM(runner, req.id),
        offline=True,
        extra={"fixture_files": multi},
    )
    result = await runner.run(req, runtime=rt)
    assert result.status is AuditStatus.CANCELLED
    row = await runner.get(req.id)
    assert row is not None
    assert row.status is AuditStatus.CANCELLED


@pytest.mark.asyncio
async def test_filesystem_artifact_rejects_escape(temp_dir):
    fs = FilesystemArtifactStore(temp_dir)
    ref = await fs.put("safe", kind=ArtifactKind.LOG, audit_id="good-id", suffix=".txt")
    assert (await fs.get_bytes(ref)) == b"safe"
    # Traversal via malicious audit_id segment is sanitized (no write outside root)
    ref2 = await fs.put(
        "x", kind=ArtifactKind.LOG, audit_id="../../escape", suffix=".txt"
    )
    from pathlib import Path

    assert Path(ref2.uri).resolve().is_relative_to(Path(temp_dir).resolve())
    # Forged URI outside root must fail
    bad = ref.model_copy(update={"uri": str(Path(temp_dir).resolve().parent / "out.bin")})
    with pytest.raises(PermissionError):
        await fs.get_bytes(bad)


@pytest.mark.asyncio
async def test_audit_runner_resume_completed_is_noop():
    runner = AuditRunner()
    req = _req()
    first = await runner.run(req, runtime=_runtime())
    assert first.status is AuditStatus.COMPLETED
    second = await runner.resume(req.id, runtime=_runtime())
    assert second.status is AuditStatus.COMPLETED
    assert len(second.findings) == len(first.findings)


@pytest.mark.asyncio
async def test_shared_checkpointer_state_visible():
    cp = create_checkpointer(backend="memory")
    runner = AuditRunner(checkpointer=cp)
    req = _req()
    await runner.run(req, runtime=_runtime())
    # Same checkpointer should expose thread state
    app = runner._graph()
    snap = await app.aget_state({"configurable": {"thread_id": req.id}})
    assert snap.values.get("audit_id") == req.id
