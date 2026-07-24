"""Agent Harness (M11): governs LangGraph execution.

Harness wraps AuditRunner / compiled graphs — it does **not** reimplement
the agent loop. Policies (model, tools, context, permission, budget) are
enforced here before/around graph invocation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from pydantic import BaseModel, Field

from app.services.agent.application.event_bus import GraphEventBus, get_graph_event_bus
from app.services.agent.application.runner import AuditRunner, AuditRunResult
from app.services.agent.domain import AuditRequest, AuditStatus, RunBudget
from app.services.agent.graph.llm import FakeLLM, LLMGateway
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.observability import (
    SPAN_AUDIT_RUN,
    Tracer,
    get_tracer,
    set_tracer,
)
from app.services.agent.tooling import ToolProtocol, ToolRegistry, default_tool_registry

logger = logging.getLogger(__name__)


class AgentSpec(BaseModel):
    """Declarative configuration for a governed audit agent."""

    name: str = "deepaudit-graph"
    version: str = "1.0"
    model: str = "fake-model"
    provider: str = "fake"
    offline: bool = True
    allow_execution: bool = False
    max_parallel_analyzers: int = Field(default=5, ge=1, le=32)
    tool_allowlist: Optional[list[str]] = None
    budget: RunBudget = Field(default_factory=RunBudget)
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass
class ModelRouter:
    """Selects an LLMGateway implementation for the run."""

    default: LLMGateway = field(default_factory=FakeLLM)

    def resolve(self, spec: AgentSpec) -> LLMGateway:
        # Phase 1: FakeLLM / injected gateway only (no paid models in default path)
        return self.default


@dataclass
class ToolRouter:
    """Builds a ToolRegistry from AgentSpec allowlist."""

    files: dict[str, str] = field(default_factory=dict)
    extra: Optional[ToolProtocol] = None

    def resolve(self, spec: AgentSpec) -> ToolRegistry:
        allow = set(spec.tool_allowlist) if spec.tool_allowlist else None
        reg = default_tool_registry(files=self.files)
        if allow is not None:
            reg.allowlist = allow
        if self.extra is not None:
            reg.register(self.extra)
        return reg


@dataclass
class ContextPolicy:
    """Token budget for context packs (wired to ContextManager later)."""

    max_tokens: int = 32_000
    never_drop_pinned: bool = True


@dataclass
class PermissionPolicy:
    """Hard security gates applied by the harness."""

    allow_execution: bool = False
    allow_network: bool = False
    allow_raw_shell: bool = False  # always False for Phase 1

    def assert_start_allowed(self, spec: AgentSpec) -> None:
        if self.allow_raw_shell:
            raise PermissionError("raw shell must never be enabled")
        if spec.allow_execution and not self.allow_execution:
            raise PermissionError("execution not permitted by PermissionPolicy")


@dataclass
class BudgetManager:
    """Tracks RunBudget consumption across a harness session."""

    budget: RunBudget = field(default_factory=RunBudget)

    @staticmethod
    def from_spec(spec: AgentSpec) -> BudgetManager:
        return BudgetManager(budget=spec.budget.model_copy(deep=True))

    def exhausted(self) -> bool:
        return self.budget.is_exhausted()

    def note_model_call(self, tokens: int = 0) -> None:
        self.budget = self.budget.consume_model_call(tokens)

    def note_tool_call(self) -> None:
        self.budget = self.budget.consume_tool_call()


class AgentRuntime:
    """Harness entrypoint: start / resume / cancel / stream_events.

    Delegates workflow execution to ``AuditRunner`` (LangGraph).
    """

    def __init__(
        self,
        *,
        spec: Optional[AgentSpec] = None,
        runner: Optional[AuditRunner] = None,
        model_router: Optional[ModelRouter] = None,
        tool_router: Optional[ToolRouter] = None,
        context_policy: Optional[ContextPolicy] = None,
        permission_policy: Optional[PermissionPolicy] = None,
        tracer: Optional[Tracer] = None,
        bus: Optional[GraphEventBus] = None,
    ) -> None:
        self.spec = spec or AgentSpec()
        self.runner = runner or AuditRunner()
        self.model_router = model_router or ModelRouter()
        self.tool_router = tool_router or ToolRouter()
        self.context_policy = context_policy or ContextPolicy()
        # Hard gate defaults closed; do NOT mirror AgentSpec.allow_execution here.
        # Callers must pass an explicit PermissionPolicy to opt into execution.
        self.permission_policy = permission_policy or PermissionPolicy(
            allow_execution=False
        )
        self.tracer = tracer or get_tracer()
        self.bus = bus or get_graph_event_bus()
        self.budget_manager = BudgetManager.from_spec(self.spec)
        self._last_result: Optional[AuditRunResult] = None
        self._tools: Optional[ToolRegistry] = None

    @property
    def tools(self) -> ToolRegistry:
        if self._tools is None:
            self._tools = self.tool_router.resolve(self.spec)
        return self._tools

    def _build_runtime(self) -> GraphRuntime:
        llm = self.model_router.resolve(self.spec)
        return GraphRuntime(
            llm=llm,
            offline=self.spec.offline,
            extra={
                "max_parallel_analyzers": self.spec.max_parallel_analyzers,
                **({"fixture_files": self.tool_router.files} if self.tool_router.files else {}),
            },
        )

    async def start(self, request: AuditRequest) -> AuditRunResult:
        """Validate policies, open root span, run graph via AuditRunner."""
        self.permission_policy.assert_start_allowed(self.spec)
        if self.budget_manager.exhausted():
            return AuditRunResult(
                audit_id=request.id,
                status=AuditStatus.FAILED,
                events=[{"kind": "budget.exhausted", "message": "budget exhausted before start"}],
            )

        set_tracer(self.tracer)
        # Ensure tools registry is materialised (side effect for callers/tests)
        _ = self.tools

        with self.tracer.span(
            SPAN_AUDIT_RUN,
            **{
                "audit.id": request.id,
                "agent.name": self.spec.name,
                "agent.version": self.spec.version,
                "model.name": self.spec.model,
                "allow_execution": self.spec.allow_execution,
            },
        ):
            self.tracer.counter("audit.starts", 1, **{"agent.name": self.spec.name})
            runtime = self._build_runtime()
            result = await self.runner.run(request, runtime=runtime, audit_id=request.id)
            self._last_result = result
            self.tracer.counter(
                "audit.completes",
                1,
                **{"status": result.status.value, "agent.name": self.spec.name},
            )
            await self.bus.publish_many(request.id, result.events)
            return result

    async def resume(self, audit_id: str) -> AuditRunResult:
        self.permission_policy.assert_start_allowed(self.spec)
        with self.tracer.span(SPAN_AUDIT_RUN, **{"audit.id": audit_id, "resume": True}):
            runtime = self._build_runtime()
            result = await self.runner.resume(audit_id, runtime=runtime)
            self._last_result = result
            return result

    async def cancel(self, audit_id: str) -> dict[str, Any]:
        self.runner.request_cancel(audit_id)
        await self.bus.publish_raw(
            audit_id, {"kind": "task.cancelled", "message": "cancelled by harness"}
        )
        return {"id": audit_id, "status": "cancelling"}

    async def stream_events(self, audit_id: str) -> AsyncIterator[dict[str, Any]]:
        async for ev in self.bus.subscribe(audit_id):
            yield ev

    def last_result(self) -> Optional[AuditRunResult]:
        return self._last_result


def default_runtime(
    *,
    offline: bool = True,
    files: Optional[dict[str, str]] = None,
    allow_execution: bool = False,
) -> AgentRuntime:
    """Factory for Phase-1 governed runtime (FakeLLM + NullSandbox path)."""
    spec = AgentSpec(offline=offline, allow_execution=allow_execution)
    return AgentRuntime(
        spec=spec,
        tool_router=ToolRouter(files=dict(files or {})),
        permission_policy=PermissionPolicy(allow_execution=allow_execution),
    )
