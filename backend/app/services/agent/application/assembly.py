"""Assemble a governed AgentRuntime for a real audit.

Offline runs keep FakeLLM. A production run passes an ``LLMServiceGateway``
and refuses to start if the spec asks for a live model and none was given.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.agent.graph.llm import FakeLLM, LLMGateway
from app.services.agent.harness import (
    AgentRuntime,
    AgentSpec,
    ModelRouter,
    PermissionPolicy,
    ToolRouter,
)
from app.services.agent.observability import Tracer


def assemble_audit_runtime(
    *,
    files: dict[str, str],
    gateway: LLMGateway | None = None,
    offline: bool = False,
    workspace_root: Path | None = None,
    parallel: int = 1,
    cross_file: bool = False,
    pattern_scan: bool = True,
    verify: bool = False,
    tracer: Tracer | None = None,
    runner: Any | None = None,
) -> AgentRuntime:
    """Build the harness runtime used by the product graph path."""
    use_offline = offline or gateway is None
    width = max(1, min(int(parallel), 8))
    spec = AgentSpec(
        offline=use_offline,
        provider="fake" if use_offline else str(getattr(gateway, "provider", "gateway")),
        model="fake-model" if use_offline else str(getattr(gateway, "model", "gateway")),
        enable_model_calls=not use_offline,
        enable_heuristic_analysis=True,
        allow_execution=False,
        max_parallel_analyzers=width,
    )
    return AgentRuntime(
        spec=spec,
        runner=runner,
        model_router=ModelRouter(default=FakeLLM(), production=None if use_offline else gateway),
        tool_router=ToolRouter(files=dict(files)),
        permission_policy=PermissionPolicy(allow_execution=False),
        workspace_root=workspace_root,
        tracer=tracer,
        runtime_extra={
            "context_windows": True,
            "validate_findings": True,
            "pattern_scan": pattern_scan,
            "enable_parallel_analysis": width > 1,
            "max_parallel_analyzers": width,
            "cross_file": cross_file,
            "fixture_files": dict(files),
            "graph_verification": verify,
        },
    )
