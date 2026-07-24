"""Graph runtime config passed via LangGraph configurable / node deps."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

from .llm import FakeLLM, LLMGateway

_runtime_ctx: ContextVar[Optional["GraphRuntime"]] = ContextVar(
    "deepaudit_graph_runtime", default=None
)


@dataclass
class GraphRuntime:
    """Injectable dependencies for graph nodes (M2 skeleton)."""

    llm: LLMGateway = field(default_factory=FakeLLM)
    workspace_root: Optional[Path] = None
    # When True, analyze/plan use FakeLLM scripted payloads only.
    offline: bool = True
    extra: dict[str, Any] = field(default_factory=dict)
    # Cooperative cancel: runner sets this; nodes poll via is_cancelled().
    cancel_check: Optional[Any] = None  # Callable[[], bool]

    def is_cancelled(self) -> bool:
        fn = self.cancel_check
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False


def set_runtime(runtime: GraphRuntime):
    """Bind runtime for the current context (tests / application layer)."""
    return _runtime_ctx.set(runtime)


def reset_runtime(token) -> None:
    _runtime_ctx.reset(token)


def get_runtime(config: Optional[Union[dict, Any]] = None) -> GraphRuntime:
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


def _from_config(cfg: Any) -> Optional[GraphRuntime]:
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
