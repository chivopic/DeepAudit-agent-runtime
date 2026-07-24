"""M11 Agent Harness tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    RepositoryRef,
    RunBudget,
    VerificationStatus,
)
from app.services.agent.harness import (
    AgentRuntime,
    AgentSpec,
    BudgetManager,
    ModelRouter,
    PermissionPolicy,
    ToolRouter,
    default_runtime,
)
from app.services.agent.graph.llm import FakeLLM
from app.services.agent.observability import Tracer
from app.services.agent.persistence.business_store import InMemoryBusinessStore
from app.services.agent.application.runner import AuditRunner
from app.services.agent.tooling import ToolInput


FIXTURE = {
    "app/vuln.py": (
        "import os\n"
        "def run(cmd):\n"
        "    os.system(cmd)\n"
        "    q = 'SELECT * FROM t WHERE id=' + x\n"
    ),
}


def _request() -> AuditRequest:
    return AuditRequest(
        repository=RepositoryRef(source_type="local", local_path="/tmp/harness-fixture"),
        languages=["python"],
        budget=RunBudget(max_tokens=50_000, max_model_calls=30, max_files=20),
        enable_verification=False,
    )


@pytest.mark.asyncio
async def test_harness_start_end_to_end():
    tracer = Tracer(service_name="test-harness")
    runner = AuditRunner(store=InMemoryBusinessStore())
    # Patch runner path: inject fixture via model router + custom start
    rt = AgentRuntime(
        spec=AgentSpec(name="test-agent", offline=True, allow_execution=False),
        runner=runner,
        model_router=ModelRouter(default=FakeLLM()),
        tool_router=ToolRouter(files=FIXTURE),
        permission_policy=PermissionPolicy(allow_execution=False),
        tracer=tracer,
    )

    # Wire fixture into graph by wrapping runner.run
    original_run = runner.run

    async def run_with_fixture(request, *, runtime=None, audit_id=None):
        if runtime is not None:
            runtime.extra = dict(runtime.extra or {})
            runtime.extra["fixture_files"] = FIXTURE
        return await original_run(request, runtime=runtime, audit_id=audit_id)

    runner.run = run_with_fixture  # type: ignore[method-assign]

    result = await rt.start(_request())
    assert result.status is AuditStatus.COMPLETED
    assert result.findings
    assert all(f.verification_status is VerificationStatus.NOT_RUN for f in result.findings)
    flat = tracer.get_spans_flat()
    assert any(s.name == "audit.run" for s in flat)
    assert rt.tools.list_tools()  # tools materialised
    # tool allowlist default includes builtins
    names = {s.name for s in rt.tools.list_tools()}
    assert "list_files" in names


@pytest.mark.asyncio
async def test_permission_policy_blocks_execution_when_disallowed():
    policy = PermissionPolicy(allow_execution=False)
    with pytest.raises(PermissionError):
        policy.assert_start_allowed(AgentSpec(allow_execution=True))


def test_permission_policy_never_allows_raw_shell():
    policy = PermissionPolicy(allow_raw_shell=True)
    with pytest.raises(PermissionError, match="raw shell"):
        policy.assert_start_allowed(AgentSpec())


@pytest.mark.asyncio
async def test_tool_router_allowlist():
    router = ToolRouter(files={"a.py": "x"})
    reg = router.resolve(AgentSpec(tool_allowlist=["echo"]))
    names = {s.name for s in reg.list_tools()}
    assert names == {"echo"}
    denied = await reg.invoke(ToolInput(name="list_files", arguments={}))
    assert not denied.success
    assert denied.metadata.get("policy_denied")


def test_budget_manager_exhaustion():
    bm = BudgetManager.from_spec(
        AgentSpec(budget=RunBudget(max_model_calls=1, max_tokens=10, max_tool_calls=1))
    )
    assert not bm.exhausted()
    bm.note_model_call(tokens=5)
    # one model call used — if max is 1, exhausted
    assert bm.budget.model_calls_used == 1
    assert bm.budget.model_calls_exhausted()


@pytest.mark.asyncio
async def test_cancel_via_harness():
    rt = default_runtime(files=FIXTURE)
    out = await rt.cancel("aud_does_not_exist")
    assert out["id"] == "aud_does_not_exist"
    assert "cancel" in out["status"]


def test_agent_spec_validation():
    s = AgentSpec(name="x", max_parallel_analyzers=3)
    assert s.version == "1.0"
    with pytest.raises(ValidationError):
        AgentSpec(max_parallel_analyzers=0)
