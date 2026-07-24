# API façade (M4)

> Packages: `application/facade.py`, `api_mapping.py`, `event_bus.py`  
> Routes: `GET/POST /api/v1/graph-audits/*` (additive)  
> Status: implemented 2026-07-24

## Design

- Production FE continues to use `/api/v1/agent-tasks/*` (ReAct).
- LangGraph dual-path is exposed under **`/api/v1/graph-audits`**.
- `GraphAuditFacade` maps domain → `AgentTaskResponse`-compatible dicts and SSE-shaped events.
- Idempotent `POST /{id}/start` for callers that expect a start endpoint.

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/graph-audits/` | Start offline/fixture graph audit |
| GET | `/graph-audits/{id}` | Task summary |
| POST | `/graph-audits/{id}/cancel` | Cancel request |
| POST | `/graph-audits/{id}/resume` | Resume / re-drive |
| GET | `/graph-audits/{id}/findings` | Findings (legacy flat keys) |
| GET | `/graph-audits/{id}/events` | Event list |
| POST | `/graph-audits/{id}/start` | Idempotent start |

## Tests

`tests/test_agent_facade_m4.py`
