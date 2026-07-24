# ADR-002: State and Persistence Boundaries

- **Status:** Accepted (M0)
- **Date:** 2026-07-24

## Context

Today three mechanisms mix concerns:

1. **Business tables** — `agent_tasks`, `agent_events`, `agent_findings`, `agent_tree_nodes` (queryable product data).
2. **Checkpoint tables / FS JSON** — `agent_checkpoints.state_data`, `./agent_checkpoints/*.json` (serialized `AgentState` including message history).
3. **In-process maps** — running/cancelled task registries for cancel and SSE.

There is no first-class Artifact Store; code snippets and large text often live in DB columns or state blobs. Checkpoints are **not** consistently written on the orchestrator happy path despite schema existing.

## Decision

Strict three-way split:

| Store | Owns | Does not own |
|-------|------|--------------|
| **LangGraph Checkpointer** | Graph execution state, resume cursor, interrupts | Product listing queries, report APIs |
| **Business PostgreSQL** | Tasks, events, findings, reports, ACL, tree projection | Multi-MB raw sources, full prompts |
| **Artifact Store** | Snippets, logs, patches, verification outputs, optional raw model dumps | Primary keys for user-facing finding lists |

Additional rules:

1. LangGraph state uses **TypedDict** + reducers; domain objects inside state are **Pydantic**.
2. Nodes return **state deltas** only.
3. State holds **`ArtifactRef`**, not bulk source.
4. `thread_id = audit_id` (existing `AgentTask.id` / future audit id).
5. Dev checkpointer: SQLite acceptable; production: PostgreSQL checkpointer.
6. Same `audit_id` retry must not create duplicate business findings (idempotent keys / fingerprints).
7. In-process maps may remain as a **cache** but must not be the sole durability mechanism after M3.

## Consequences

### Positive

- Recoverable runs without treating checkpoint JSON as a data warehouse.
- Smaller checkpoints → better performance and privacy posture.
- Clear migration path from existing tables (extend, don't replace overnight).

### Negative

- Need artifact APIs and cleanup/GC policies.
- Dual-write complexity during transition (graph state + business rows).
- Existing `AgentState` message-heavy design must shrink carefully.

## Alternatives considered

| Option | Why rejected |
|--------|--------------|
| Checkpoint as only store | Cannot efficiently query findings/events for UI |
| Business DB only for resume | Poor fit for partial graph state / interrupt semantics |
| Keep full messages in checkpoint forever | Size, cost, secret leakage risk |

## Follow-ups

- M1: domain models + ArtifactRef
- M3: wire checkpointer; dual-write findings/events
- M5: artifact offload from Context Manager
