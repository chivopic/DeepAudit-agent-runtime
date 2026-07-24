"""LangGraph package for the DeepAudit audit workflow (M2+).

Phase 1 graph is additive and does not replace the production ReAct path.
"""

from .state import AuditState, empty_audit_state
from .builder import build_audit_graph, compile_audit_graph
from .llm import FakeLLM, LLMGateway, LLMMessage, LLMResponse

__all__ = [
    "AuditState",
    "empty_audit_state",
    "build_audit_graph",
    "compile_audit_graph",
    "FakeLLM",
    "LLMGateway",
    "LLMMessage",
    "LLMResponse",
]
