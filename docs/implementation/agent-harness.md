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

## Tests

`tests/test_agent_harness_m11.py`
