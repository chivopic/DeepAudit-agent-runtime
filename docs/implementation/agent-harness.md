# Agent Harness (M11)

> Package: `backend/app/services/agent/harness/`  
> Status: implemented 2026-07-24

## Boundary

```text
AgentSpec → AgentRuntime.start / resume / cancel / stream_events
  ModelRouter · ToolRouter · ContextPolicy · PermissionPolicy
  BudgetManager · Tracer · EventBus
        ↓
   AuditRunner (LangGraph) — harness does NOT reimplement the loop
```

## Components

| Piece | Role |
|-------|------|
| `AgentSpec` | Declarative config (model, budget, tool allowlist, allow_execution) |
| `ModelRouter` | Resolves `LLMGateway` (default FakeLLM offline) |
| `ToolRouter` | Builds `ToolRegistry` from allowlist + fixture files |
| `PermissionPolicy` | Hard gates: no raw shell; execution only if both policy + spec allow |
| `BudgetManager` | Tracks RunBudget consumption |
| `AgentRuntime` | start/resume/cancel/stream; opens `audit.run` span |

## Phase 1 defaults

- `offline=True`, `allow_execution=False`
- Findings remain `verification_status=NOT_RUN`
- Production ReAct path (`/api/v1/agent-tasks/*`) **unchanged**

## Mid-run governance (post-M11 wiring)

`AgentRuntime._build_runtime` injects into `GraphRuntime`:

| Field | Source | Node consumption |
|-------|--------|------------------|
| `tools` | `ToolRouter` → `ToolRegistry` | `analyze_file` invokes allowlisted `heuristic_scan` |
| `tracer` | harness `Tracer` | `plan_audit` / `analyze_file` open `graph.node.*`, `tool.call`, `llm.call` spans |
| `budget_manager` | `BudgetManager.from_spec` | graph `RunBudget` is source of truth; `_sync_budget_manager` mirrors after each analyze/plan step (no double-count via `note_*`) |

Nodes without a registry still run heuristics + FakeLLM (backward compatible). Tool denials and missing adapters are non-fatal; events carry `tool.call` / `tool.error`.

## Tests

- `tests/test_agent_harness_m11.py` — end-to-end spans + tool events + budget mirror
- `tests/test_agent_graph_m2.py::test_analyze_uses_tool_registry_and_tracer`
