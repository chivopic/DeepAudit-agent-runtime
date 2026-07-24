# Graph skeleton (M2)

> Package: `backend/app/services/agent/graph/`  
> Status: implemented 2026-07-24

## Purpose

Additive **LangGraph** audit workflow that runs **side-by-side** with the production ReAct multi-agent path. Nodes use M1 domain models; LLM access is via `LLMGateway` (FakeLLM for tests).

## Layout

```text
graph/
├── __init__.py
├── state.py          # AuditState TypedDict + reducers
├── llm.py            # LLMGateway protocol + FakeLLM
├── runtime.py        # GraphRuntime + contextvar injection
├── routing.py        # conditional edges
├── builder.py        # build_audit_graph / compile_audit_graph
└── nodes/__init__.py # Phase 1 node implementations
```

## Phase 1 flow

```text
START
  → validate_request
  → ingest_repository
  → build_manifest
  → plan_audit
  → analyze_file*        # loop while pending_task_ids
  → aggregate_findings
  → deduplicate_findings
  → prioritize_findings
  → generate_report
  → END
```

## Invariants

1. Findings leave the graph with `verification_status=NOT_RUN` (Phase 1).
2. No untrusted code execution; heuristics + optional LLM JSON only.
3. Large payloads must be `ArtifactRef` (not yet wired — skeleton uses snippets in Evidence only for tests).
4. Production ReAct path under `agents/` is **unchanged**.
5. Runtime deps (`FakeLLM`, fixture files) via `configurable.runtime` and/or `set_runtime()` contextvar.

## Tests

```bash
cd backend && uv run pytest tests/test_agent_graph_m2.py tests/test_agent_domain.py -q
```

## Non-goals (M2)

- Postgres checkpointer (M3)
- API façade wiring (M4)
- Context Manager (M5)
- Sandbox (M6)
- Verification subgraph (M7)
