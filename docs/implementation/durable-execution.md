# Durable execution (M3)

> Packages: `persistence/`, `application/`  
> Status: implemented 2026-07-24

## Persistence triad (ADR-002)

| Store | Interface | Default M3 impl |
|-------|-----------|-----------------|
| Checkpointer | `create_checkpointer` | `MemorySaver` |
| Business | `BusinessAuditStore` | `InMemoryBusinessStore` |
| Artifacts | `ArtifactStore` | `InMemoryArtifactStore` / `FilesystemArtifactStore` |

LangGraph checkpoints are **not** used for product listings. Business store holds findings/events/status.

## Application layer

`AuditRunner`:

- `run(request, runtime=…)` — compile graph, invoke, persist results
- `resume(audit_id)` — return completed snapshot or re-drive from stored request
- `request_cancel(audit_id)` — cooperative pre-start cancel (M4 hardens mid-run)

Idempotent finding writes key on `fingerprint`.

## Tests

```bash
cd backend && uv run pytest tests/test_agent_persistence_m3.py -q
```

## Non-goals

- SQLAlchemy wiring to `AgentTask` / `AgentFinding` (M4 façade can map)
- Postgres checkpointer package (optional when `langgraph-checkpoint-sqlite` installed)
- SSE / HTTP routes (M4)
