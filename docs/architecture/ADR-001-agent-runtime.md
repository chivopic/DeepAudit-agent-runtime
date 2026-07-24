# ADR-001: Agent Runtime Substrate

- **Status:** Accepted (M0)
- **Date:** 2026-07-24
- **Deciders:** DeepAudit maintainers / architecture M0

## Context

DeepAudit v3.0.4 implements multi-agent auditing as a **custom ReAct agent tree** (`OrchestratorAgent` → recon/analysis/verification) under `backend/app/services/agent/`. Dependencies declare `langgraph` and `langchain`, and some modules/docs claim LangGraph, but **no runtime `StateGraph` / checkpointer integration exists**.

The refactor goal requires durable workflows: node scheduling, parallel merge with reducers, checkpoint/resume, and clear separation from business persistence.

## Decision

1. **Adopt LangGraph as the workflow runtime** for the audit pipeline (state transitions, routing, parallel analysis, checkpoint, interrupt/resume).
2. **Do not rewrite the product as “LangGraph-only agents” in one step.** Preserve role semantics (orchestrator/recon/analysis/verification) as **node implementations / subgraphs** behind protocols.
3. **Evolve in place under `backend/app/services/agent/`** (plus `app/services/llm`, models, API). Do **not** create `backend/src/deepaudit_agent/` during M1–M4.
4. **Agent Harness (M11) wraps LangGraph** — budget, model/tool routing, permissions, retry classification, events, approval — and does **not** reimplement the agent loop.
5. Treat unused LangGraph imports/docs as technical debt: either wire for real (preferred) or remove deps later; **do not leave “phantom LangGraph”**.

## Consequences

### Positive

- Checkpoint/resume and parallel reducers align with LangGraph strengths.
- Clear testability: Fake LLM + graph unit tests without Docker/LLM.
- Matches target architecture and industry durable-agent patterns.

### Negative / costs

- Migration effort from text ReAct parsing to structured graph + tool calling.
- Need dual-run or façade so `/api/v1/agent-tasks` keeps working.
- Team must learn LangGraph checkpoint semantics and avoid stuffing business queries into checkpoints.

### Compatibility

- Frontend and route contracts unchanged during early milestones.
- Existing tool implementations and LLMService reused via adapters.

## Alternatives considered

| Option | Why rejected |
|--------|--------------|
| Keep pure custom ReAct forever | Checkpoint/resume and parallel merge remain ad-hoc; multi-worker fragile |
| Full rewrite under new package root | Massive import/test/Docker churn without product value |
| Celery-only orchestration without graph | Weak model for LLM node state and interrupt/resume |
| Drop multi-agent for single prompt | Regresses product differentiation |

## Follow-ups

- M2: graph skeleton + Fake LLM
- M3: checkpointer + business dual-write
- M11: Harness encapsulation
