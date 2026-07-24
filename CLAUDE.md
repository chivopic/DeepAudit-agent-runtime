# DeepAudit Development Instructions

## Project Purpose

DeepAudit is a multi-agent source-code auditing platform (backend FastAPI + frontend React/Vite).

Engineering goal for the agent runtime refactor:

* LangGraph (workflow) + Pydantic (domain/API/tool I/O)
* PostgreSQL business data + LangGraph checkpoints + Artifact Store
* Context Manager, isolated Sandbox, MCP, OpenTelemetry, evals
* Agent Harness as a **wrapper** around LangGraph (not a second loop)

Authoritative M0 docs:

* [docs/implementation/current-architecture.md](docs/implementation/current-architecture.md)
* [docs/implementation/gap-analysis.md](docs/implementation/gap-analysis.md)
* [docs/implementation/target-architecture.md](docs/implementation/target-architecture.md)
* [docs/architecture/ADR-001-agent-runtime.md](docs/architecture/ADR-001-agent-runtime.md)
* [docs/architecture/ADR-002-state-and-persistence.md](docs/architecture/ADR-002-state-and-persistence.md)
* [docs/architecture/ADR-003-sandbox-boundary.md](docs/architecture/ADR-003-sandbox-boundary.md)
* [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)

Existing deep dive (as-built agent design): [docs/AGENT_AUDIT_ARCHITECTURE.md](docs/AGENT_AUDIT_ARCHITECTURE.md)

## Active milestone

Read `IMPLEMENTATION_STATUS.md` first. **Only implement the active milestone.**  
Order: M0 → M1 → … → M11. Do not implement later phases “while here.”

## Required workflow

Before modifying code:

1. Read the relevant implementation and tests.
2. Read architecture docs + `IMPLEMENTATION_STATUS.md`.
3. Identify compatibility constraints (`/api/v1/agent-tasks/*`, dual finding stores).
4. State the files that will change.

After modifying code:

1. Format (Black) / lint (Ruff) / typecheck (MyPy) as applicable.
2. Run unit tests and relevant integration tests.
3. Update docs + `IMPLEMENTATION_STATUS.md`.
4. Do not claim success if checks did not pass.

## Stack anchors (do not fork)

| Concern | Use |
|---------|-----|
| Package manager | **uv** + `backend/pyproject.toml` |
| API | FastAPI under `backend/app` |
| Config | `app.core.config.Settings` only |
| ORM / migrations | SQLAlchemy async + Alembic |
| DB | PostgreSQL |
| LLM | `app.services.llm` gateway evolution |
| Agent code home | `backend/app/services/agent/` |
| Frontend API prefix | `/api/v1` |

Do **not**: second ORM, second settings system, `backend/src/deepaudit_agent/` package (until explicit extraction ADR), unrelated dependency upgrades, large renames.

## Architecture rules

### Graph

* Keep graph construction separate from node bodies.
* Nodes independently testable, ideally idempotent; return **state deltas**.
* Define reducers for parallel lists.
* No DB/HTTP/LLM clients stored in global graph state.
* No large source files in checkpoints — use ArtifactRef.

### Domain

Pydantic for API schemas, domain models, model outputs, tool I/O, sandbox I/O, harness config.  
LangGraph state: TypedDict; objects inside: Pydantic.

### Boundaries

Business logic depends on **protocols**. Infrastructure implements them.  
Replaceable in tests: LLM, checkpointer, business repo, artifact store, context store, sandbox, tools, MCP, telemetry.

### Security

Never:

* Execute arbitrary model-generated shell strings
* Privileged containers / host network / host PID
* Mount Docker socket into **execution** containers
* Writable mount of the real repository into sandbox
* Network by default
* Log secrets / put credentials in source
* Disable TLS verification
* Bypass permission failures or auto-retry security denials
* Trust model paths without validation

Phase 1 graph path: `verification_status = NOT_RUN` (no untrusted exec required).

### Data

Distinguish checkpoints, application DB, artifacts, observability, evals.  
Do not use one as an implicit substitute for another.

### Testing

Default tests: deterministic, no paid model, no external net, no writable real repos.  
Use fake models/tools for graph tests. Mark integration tests for Postgres/Docker/MCP/real models.

## Commands (typical)

```bash
# Backend
cd backend
uv sync --extra dev   # or project-equivalent install
pytest
ruff check .
black --check .
mypy app

# Frontend
cd frontend
pnpm install
pnpm test
pnpm lint

# Stack
docker compose up -d
```

## Compatibility freeze

Preserve frontend-facing `/api/v1/agent-tasks` behavior and SSE field shapes unless an explicit versioned migration is approved. Classic `/tasks` + `/scan` paths remain valid product surfaces.

## Completion definition

A milestone is complete only when required behavior, tests, checks, docs, and `IMPLEMENTATION_STATUS.md` are updated, with no critical TODO and compatibility impact documented.
