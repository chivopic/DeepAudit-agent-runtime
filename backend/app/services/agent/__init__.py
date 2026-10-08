# ruff: noqa: F401
"""DeepAudit agent service package.

The package-level API is kept for backwards compatibility, but its exports are
loaded lazily. This matters for local consumers such as the CLI: importing the
governed harness must not initialise legacy agents, RAG knowledge, or optional
infrastructure that the caller did not request.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agents import (
        AgentConfig,
        AgentResult,
        AnalysisAgent,
        BaseAgent,
        OrchestratorAgent,
        ReconAgent,
        VerificationAgent,
    )
    from .core import (
        AgentMessage,
        AgentRegistry,
        AgentState,
        AgentStatus,
        MessageBus,
        MessagePriority,
        MessageType,
        agent_registry,
    )
    from .event_manager import AgentEventEmitter, EventManager
    from .knowledge import (
        GetVulnerabilityKnowledgeTool,
        KnowledgeLoader,
        SecurityKnowledgeQueryTool,
        SecurityKnowledgeRAG,
        get_available_modules,
        get_module_content,
        knowledge_loader,
        security_knowledge_rag,
    )
    from .telemetry import Tracer, get_global_tracer, set_global_tracer
    from .tools import (
        AgentFinishTool,
        CreateSubAgentTool,
        CreateVulnerabilityReportTool,
        FinishScanTool,
        ReflectTool,
        SendMessageTool,
        ThinkTool,
        ViewAgentGraphTool,
        WaitForMessageTool,
    )


_EXPORT_MODULES = {
    # Event management
    "EventManager": ".event_manager",
    "AgentEventEmitter": ".event_manager",
    # Legacy agent classes
    "BaseAgent": ".agents",
    "AgentConfig": ".agents",
    "AgentResult": ".agents",
    "OrchestratorAgent": ".agents",
    "ReconAgent": ".agents",
    "AnalysisAgent": ".agents",
    "VerificationAgent": ".agents",
    # Core state, registry, and messaging
    "AgentState": ".core",
    "AgentStatus": ".core",
    "AgentRegistry": ".core",
    "agent_registry": ".core",
    "AgentMessage": ".core",
    "MessageType": ".core",
    "MessagePriority": ".core",
    "MessageBus": ".core",
    # Knowledge/RAG
    "KnowledgeLoader": ".knowledge",
    "knowledge_loader": ".knowledge",
    "get_available_modules": ".knowledge",
    "get_module_content": ".knowledge",
    "SecurityKnowledgeRAG": ".knowledge",
    "security_knowledge_rag": ".knowledge",
    "SecurityKnowledgeQueryTool": ".knowledge",
    "GetVulnerabilityKnowledgeTool": ".knowledge",
    # Collaboration tools
    "ThinkTool": ".tools",
    "ReflectTool": ".tools",
    "CreateVulnerabilityReportTool": ".tools",
    "FinishScanTool": ".tools",
    "CreateSubAgentTool": ".tools",
    "SendMessageTool": ".tools",
    "ViewAgentGraphTool": ".tools",
    "WaitForMessageTool": ".tools",
    "AgentFinishTool": ".tools",
    # Telemetry
    "Tracer": ".telemetry",
    "get_global_tracer": ".telemetry",
    "set_global_tracer": ".telemetry",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    """Resolve a legacy package-level export only when it is requested."""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
