"""Graph runtime config passed via LangGraph configurable / node deps."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .llm import FakeLLM, LLMGateway

_runtime_ctx: ContextVar[GraphRuntime | None] = ContextVar("deepaudit_graph_runtime", default=None)


@dataclass
class GraphRuntime:
    """Injectable dependencies for graph nodes (M2 skeleton).

    Optional governance hooks (tools / tracer / budget_manager) are set by the
    Agent Harness or façade so nodes can enforce policies mid-run.
    """

    llm: LLMGateway = field(default_factory=FakeLLM)
    workspace_root: Path | None = None
    # ScannerProtocol | None — injected static-analysis boundary.
    scanner: Any | None = None
    # When True, analyze/plan use FakeLLM scripted payloads only.
    offline: bool = True
    # Keep legacy graph behavior by default; CLI-1 disables both explicitly.
    enable_model_calls: bool = True
    enable_heuristic_analysis: bool = True
    extra: dict[str, Any] = field(default_factory=dict)
    # Cooperative cancel: runner sets this; nodes poll via is_cancelled().
    cancel_check: Any | None = None  # Callable[[], bool]
    # ToolRegistry | None — allowlisted tools for analyze nodes
    tools: Any | None = None
    # Tracer | None — observability; falls back to get_tracer() if unset
    tracer: Any | None = None
    # BudgetManager | None — harness-level budget mirror (optional)
    budget_manager: Any | None = None

    def is_cancelled(self) -> bool:
        fn = self.cancel_check
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False

    def get_tools(self) -> Any:
        if self.tools is not None:
            return self.tools
        return self.extra.get("tools")

    def get_tracer(self) -> Any:
        if self.tracer is not None:
            return self.tracer
        extra_t = self.extra.get("tracer")
        if extra_t is not None:
            return extra_t
        try:
            from app.services.agent.observability import get_tracer

            return get_tracer()
        except Exception:  # noqa: BLE001
            return None


def set_runtime(runtime: GraphRuntime):
    """Bind runtime for the current context (tests / application layer)."""
    return _runtime_ctx.set(runtime)


def reset_runtime(token) -> None:
    _runtime_ctx.reset(token)


def get_runtime(config: dict | Any | None = None) -> GraphRuntime:
    """Extract GraphRuntime from LangGraph RunnableConfig or contextvar."""
    # 1) Explicit argument
    rt = _from_config(config)
    if rt is not None:
        return rt
    # 2) LangGraph active config
    try:
        from langgraph.config import get_config

        rt = _from_config(get_config())
        if rt is not None:
            return rt
    except Exception:  # noqa: BLE001
        pass
    # 3) Contextvar (application / tests)
    ctx = _runtime_ctx.get()
    if ctx is not None:
        return ctx
    return GraphRuntime()


def _from_config(cfg: Any) -> GraphRuntime | None:
    if not cfg:
        return None
    configurable: Any = None
    if isinstance(cfg, dict):
        configurable = cfg.get("configurable")
    elif hasattr(cfg, "get"):
        try:
            configurable = cfg.get("configurable")
        except Exception:  # noqa: BLE001
            configurable = None
    if not isinstance(configurable, dict):
        return None
    rt = configurable.get("runtime")
    return rt if isinstance(rt, GraphRuntime) else None
