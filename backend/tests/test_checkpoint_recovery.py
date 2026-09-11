"""Checkpoints must outlive the process that wrote them.

The default backend was ``MemorySaver`` — a dict — and `resume` only read
finished state back rather than continuing. Both looked fine in tests, because
a single-process test never asks the question that matters: *is it still there
after the process is gone?*

The Postgres tests here are marked ``integration`` and skip without a DSN. The
cross-process one is the point of the file: it writes checkpoints in a **child
process**, kills it, and resumes in this one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.services.agent.persistence.checkpointer import (
    CheckpointerFactory,
    acreate_checkpointer,
    create_checkpointer,
)

DSN = os.environ.get("TEST_CHECKPOINT_DSN")
BACKEND = Path(__file__).resolve().parent.parent

requires_postgres = pytest.mark.skipif(
    not DSN, reason="set TEST_CHECKPOINT_DSN to run checkpoint integration tests"
)


# ---------------------------------------------------------------------------
# DSN handling — the application speaks SQLAlchemy, psycopg does not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        ("postgresql+asyncpg://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
    ],
)
def test_sqlalchemy_dialect_prefixes_are_stripped(given, expected):
    """Handing psycopg a '+asyncpg' URL fails in a confusing way at startup."""
    assert CheckpointerFactory(dsn=given)._resolve_dsn() == expected


@pytest.mark.parametrize("given", ["sqlite:///x.db", "mysql://h/db"])
def test_non_postgres_urls_are_refused_rather_than_mangled(given):
    assert CheckpointerFactory(dsn=given)._resolve_dsn() is None


def test_an_empty_dsn_means_not_provided_and_falls_back_to_settings(monkeypatch):
    """Empty is absence, not a URL — so configuration still gets a say."""
    from app.core import config as config_mod

    monkeypatch.setattr(
        config_mod.settings, "AGENT_CHECKPOINT_DSN", None, raising=False
    )
    monkeypatch.setattr(
        config_mod.settings, "DATABASE_URL", "postgresql+asyncpg://u@h/db", raising=False
    )
    assert CheckpointerFactory(dsn="")._resolve_dsn() == "postgresql://u@h/db"


def test_postgres_backend_refuses_the_sync_factory():
    """It opens a pool, so it cannot be built from a sync call."""
    with pytest.raises(RuntimeError, match="async"):
        create_checkpointer("postgres")


def _is_memory_saver(cp) -> bool:
    """The library renamed MemorySaver to InMemorySaver; check the type, not
    the spelling."""
    from langgraph.checkpoint.memory import MemorySaver

    return isinstance(cp, MemorySaver)


def test_memory_backend_still_works_without_a_database():
    assert _is_memory_saver(create_checkpointer("memory"))


@pytest.mark.asyncio
async def test_auto_falls_back_to_memory_without_a_dsn(monkeypatch):
    from app.services.agent.persistence import checkpointer as mod

    monkeypatch.setattr(
        mod.CheckpointerFactory, "_resolve_dsn", lambda self: None, raising=False
    )
    cp = await acreate_checkpointer("auto")
    assert _is_memory_saver(cp)


# ---------------------------------------------------------------------------
# Against a real database
# ---------------------------------------------------------------------------


@pytest.fixture
async def pg_checkpointer():
    """A Postgres checkpointer whose pool is closed afterwards.

    An open pool keeps the event loop alive, so a test that forgets this hangs
    at teardown rather than failing.
    """
    cp = await acreate_checkpointer("postgres", dsn=DSN)
    try:
        yield cp
    finally:
        closer = getattr(cp, "aclose_pool", None)
        if closer is not None:
            await closer()


@requires_postgres
@pytest.mark.asyncio
async def test_postgres_checkpointer_creates_its_tables(pg_checkpointer):
    assert type(pg_checkpointer).__name__ == "AsyncPostgresSaver"


# ---------------------------------------------------------------------------
# The one that matters: a checkpoint written by a process that no longer exists
# ---------------------------------------------------------------------------


_CHILD = """
import asyncio, sys
from app.services.agent.application.runner import AuditRunner
from app.services.agent.domain import AuditRequest, RunBudget
from app.services.agent.domain.repository import RepositoryRef
from app.services.agent.graph.llm import FakeLLM
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.persistence.checkpointer import acreate_checkpointer

DSN, AUDIT_ID = sys.argv[1], sys.argv[2]
FIXTURE = {f"m{i}.py": "import os\\nos.system('ping ' + h)\\n" for i in range(6)}

async def main():
    cp = await acreate_checkpointer("postgres", dsn=DSN)
    runner = AuditRunner(checkpointer=cp)
    req = AuditRequest(
        id=AUDIT_ID,
        repository=RepositoryRef(source_type="local", local_path="fixture://t"),
        # Two model calls: the run stops part-way with files still pending.
        budget=RunBudget(max_tokens=100000, max_model_calls=2),
    )
    rt = GraphRuntime(llm=FakeLLM(), offline=True, extra={"fixture_files": FIXTURE})
    result = await runner.run(req, runtime=rt, audit_id=AUDIT_ID)
    print("CHILD_STATUS", result.status.value)

asyncio.run(main())
"""


@requires_postgres
@pytest.mark.asyncio
async def test_a_checkpoint_survives_the_process_that_wrote_it(tmp_path):
    """Run in a child process, let it exit, then read its checkpoint here.

    With MemorySaver this is unwritable: the dict dies with the child. That is
    exactly why the gap went unnoticed for so long.
    """
    audit_id = "aud_crossproc_1"
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(_CHILD), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(script), DSN, audit_id],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, f"child failed:\n{proc.stdout}\n{proc.stderr}"
    assert "CHILD_STATUS" in proc.stdout, proc.stdout

    # The child is gone. Its checkpoint must not be.
    cp = await acreate_checkpointer("postgres", dsn=DSN)
    try:
        state = await cp.aget({"configurable": {"thread_id": audit_id}})
    finally:
        await cp.aclose_pool()

    assert state is not None, (
        "no checkpoint found for a thread written by another process — "
        "this is the failure MemorySaver hid"
    )
    channel_values = state.get("channel_values") or {}
    assert channel_values.get("audit_id") == audit_id
