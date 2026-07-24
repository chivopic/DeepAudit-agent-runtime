"""Build and compile the Phase-1 audit LangGraph."""

from __future__ import annotations

from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .routing import route_after_analyze, route_after_ingest, route_after_validate
from .state import AuditState


def build_audit_graph() -> StateGraph:
    """Construct the uncompiled StateGraph (Phase 1 linear + analyze loop).

    Flow::

        START → validate_request → ingest_repository → build_manifest
              → plan_audit → analyze_file* → aggregate_findings
              → deduplicate_findings → prioritize_findings
              → generate_report → END
    """
    g: StateGraph = StateGraph(AuditState)

    g.add_node("validate_request", nodes.validate_request)
    g.add_node("ingest_repository", nodes.ingest_repository)
    g.add_node("build_manifest", nodes.build_manifest)
    g.add_node("plan_audit", nodes.plan_audit)
    g.add_node("analyze_file", nodes.analyze_file)
    g.add_node("aggregate_findings", nodes.aggregate_findings)
    g.add_node("deduplicate_findings", nodes.deduplicate_findings)
    g.add_node("prioritize_findings", nodes.prioritize_findings)
    g.add_node("generate_report", nodes.generate_report)
    g.add_node("finalize_cancelled", nodes.finalize_cancelled)

    g.add_edge(START, "validate_request")
    g.add_conditional_edges(
        "validate_request",
        route_after_validate,
        {
            "ingest_repository": "ingest_repository",
            "__end__": END,
        },
    )
    g.add_conditional_edges(
        "ingest_repository",
        route_after_ingest,
        {
            "build_manifest": "build_manifest",
            "__end__": END,
        },
    )
    g.add_edge("build_manifest", "plan_audit")
    g.add_edge("plan_audit", "analyze_file")
    g.add_conditional_edges(
        "analyze_file",
        route_after_analyze,
        {
            "analyze_file": "analyze_file",
            "aggregate_findings": "aggregate_findings",
            "finalize_cancelled": "finalize_cancelled",
        },
    )
    g.add_edge("aggregate_findings", "deduplicate_findings")
    g.add_edge("deduplicate_findings", "prioritize_findings")
    g.add_edge("prioritize_findings", "generate_report")
    g.add_edge("generate_report", END)
    g.add_edge("finalize_cancelled", END)

    return g


def compile_audit_graph(
    *,
    checkpointer: Any = None,
    use_memory_checkpointer: bool = True,
):
    """Compile the audit graph.

    Parameters
    ----------
    checkpointer:
        Optional external checkpointer (M3 will use Postgres).
    use_memory_checkpointer:
        If True and checkpointer is None, attach ``MemorySaver`` for tests/dev.
    """
    graph = build_audit_graph()
    cp = checkpointer
    if cp is None and use_memory_checkpointer:
        cp = MemorySaver()
    if cp is not None:
        return graph.compile(checkpointer=cp)
    return graph.compile()
