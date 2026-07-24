# Gap Analysis: Current DeepAudit vs Target Agent Runtime

> M0 deliverable · 2026-07-24 · No business code changes in this milestone.

Related:

- [current-architecture.md](./current-architecture.md)
- [target-architecture.md](./target-architecture.md)
- ADRs under [`docs/architecture/`](../architecture/)

---

## 1. Purpose

Map every major target capability to **REUSE / MIGRATE / REPLACE / NEW**, list **breaking-change risks**, and record **path accuracy** so later milestones (M1–M11) do not invent a second stack.

---

## 2. Inventory by verdict

### 2.1 REUSE (keep as production surface)

| Component | Location | Why |
|-----------|----------|-----|
| FastAPI app shell | `backend/app/main.py` | Stable entry, lifespan, health |
| Settings | `backend/app/core/config.py` | Single config system (extend, don't fork) |
| Auth / deps | `core/security.py`, `api/deps.py` | JWT contract used by frontend |
| SQLAlchemy async + Alembic | `db/*`, `alembic/*` | Only ORM/migration path |
| Dual business models | `models/audit.py`, `models/agent_task.py` | Classic + agent product paths |
| Agent HTTP contracts | `api/v1/endpoints/agent_tasks.py` routes | Frontend hard-depends on them |
| EventManager + SSE shapes | `event_manager.py`, `AgentEvent.to_sse_dict` | Live UI stream |
| Tool implementations | `services/agent/tools/*` | File, SAST, RAG, sandbox value |
| Sandbox image | `docker/sandbox/` | Existing verification image |
| Knowledge modules | `services/agent/knowledge/*` | Vuln/framework knowledge |
| Code RAG | `services/rag/*` | Indexing/retrieval |
| LLMService / Factory / adapters | `services/llm/*` | Multi-provider gateway base |
| Resilience modules (code) | `core/retry`, `circuit_breaker`, `rate_limiter`, `fallback` | Already unit-tested |
| Frontend agent UI/API | `frontend/src/pages/AgentAudit/**`, `shared/api/agentTasks.ts` | Contract freeze |
| Classic scanner + PDF | `scanner.py`, `report_generator.py` | Parallel product path |

### 2.2 MIGRATE (keep design, change substrate)

| Component | Today | Target direction |
|-----------|-------|------------------|
| Orchestrator / Recon / Analysis / Verification roles | Custom ReAct agents | LangGraph nodes/subgraphs preserving roles & `TaskHandoff` semantics |
| `AgentState` | Large Pydantic with full message history | Graph `TypedDict` + Pydantic domain objects; large blobs → artifacts |
| Checkpoint tables | `agent_checkpoints` schema ready, weak happy-path use | LangGraph checkpointer + business tables; activate resume |
| `AgentGraphController` / registry / message bus | Process-local tree control | Graph runtime + optional process registry for UI tree only |
| `MemoryCompressor` | NL history trim | Context Manager with budgets, pinned context, structured summary |
| `SandboxManager` | Docker-from-API, string commands | `SandboxExecutor` protocol, argv-only, worker isolation |
| Inline agent schemas in endpoints | `agent_tasks.py` Pydantic models | Shared domain + API schemas package |
| Custom `Tracer` + correlation context | File/in-memory | OpenTelemetry-facing observability facade |
| Resilience modules | Under-wired on LLM/tool path | Policy layer in Harness (retry ≠ security bypass) |

### 2.3 REPLACE (stop treating as the long-term core)

| Component | Why |
|-----------|-----|
| Text ReAct parse (`Thought` / `Action` regex) | Brittle; prefer structured tool-calling + Pydantic outputs |
| Declared-but-unused LangGraph as “already done” | Dependency present, **no** `StateGraph` runtime — either adopt for real or stop claiming it |
| Process-global `_running_*` / `_cancelled_*` maps as sole control plane | Multi-worker unsafe |
| Full source / prompts in checkpoints or default traces | Violates artifact / redaction boundaries |
| Arbitrary `run_code` as default verification UX | Replace with high-level verification actions + policy |

### 2.4 NEW (missing today)

| Capability | Target milestone |
|------------|------------------|
| True LangGraph audit graph (plan → analyze → dedupe → report) | M2–M4 |
| Domain models as first-class Pydantic package (Finding, Evidence, Budget, …) | M1 |
| Durable LangGraph checkpointer (SQLite dev / Postgres prod) wired to `thread_id=audit_id` | M3 |
| Application service layer (run coordinator) thin over graph | M3–M4 / M11 |
| Context Manager protocol + artifact offload store | M5 |
| Sandbox worker boundary (no Docker socket in API process) | M6–M7 |
| Internal Tool protocol + MCP adapter (no node→MCP SDK) | M8 |
| OpenTelemetry spans/metrics + redaction | M9 |
| Eval datasets / deterministic judges / CI gates | M10 |
| Agent Harness (AgentSpec, routers, budget, approval, replay) | M11 |
| PR CI: pytest + ruff + mypy | Continuous from M1 |
| Object-storage style Artifact Store abstraction | M3/M5 |

---

## 3. Gap matrix (capability level)

| Target capability | Current | Gap severity | Notes |
|-------------------|---------|--------------|-------|
| LangGraph for state/routing/checkpoint | Custom ReAct + agent tree | **High** | Deps only; no compiled graph |
| Pydantic domain boundary | Partial; many `dict` findings | **High** | Findings often unvalidated dicts |
| Checkpoint ≠ business DB | Mixed: FS JSON + `state_data` text; business tables exist | **High** | Boundaries blur |
| Artifact Store (snippets/logs/patches) | Uploads FS + code_snippet in rows | **Medium** | Large text in DB/state |
| Context Manager | Correlation IDs + MemoryCompressor | **High** | Not a context assembly system |
| Sandbox trust boundary | Socket-mounted API; LLM `run_code` | **High** | See ADR-003 |
| High-level verification actions only | Broad shell/code execution tools | **High** | |
| MCP governed tools | None | **Medium** (Phase 3) | |
| OTEL observability | Custom Tracer | **Medium** | |
| Deterministic evals | Infra unit tests only | **High** for quality | ~966 tests but weak E2E/eval |
| Agent Harness | Scattered in endpoint + orchestrator | **High** | |
| Multi-worker safe runs | BackgroundTasks in API process | **High** for scale | Redis not used as broker |
| Fake-LLM graph E2E | Limited | **High** for M2 acceptance | |
| Resume after node failure | Checkpoint schema unused on happy path | **High** | |
| Dedup beyond title/fingerprint | Fingerprint helper exists | **Medium** | Need multi-signal dedup |
| verification_status = NOT_RUN default for phase 1 | Verification often attempted | **Product** | Phase 1 graph should mark NOT_RUN |

---

## 4. Breaking-change risks (must not break without adapters)

1. **`/api/v1/agent-tasks/*` paths and SSE payload fields** — frontend `agentTasks.ts` / `agentStream.ts` / AgentAudit pages.
2. **Create = auto-start** — `POST /` already schedules `_execute_agent_task`. Adding `POST .../start` must not double-start; frontend may call a non-existent `/start`.
3. **Dual stores** — `audit_issues` (classic) vs `agent_findings` (agent). Do not merge without adapters.
4. **Auth** — Bearer JWT; SSE via fetch stream with credentials.
5. **Field aliases** — e.g. `created_at` → `timestamp`, `ai_confidence` ↔ `confidence`, summary nesting differences.
6. **Owner-only agent ACL** — tightening/loosening is a product decision.
7. **Env var names** — LLM/sandbox/RAG/SSH surface in compose and docs.
8. **Package manager / ORM** — stay on **uv + SQLAlchemy async + Alembic**; no second stack.
9. **Python story** — runtime Docker/local **3.12**; tooling floor 3.11; avoid 3.13-only syntax in backend runtime without aligning images.
10. **In-process cancel/stream** — changing to multi-worker without redesign breaks live UX.

---

## 5. Path mapping: conceptual target → existing tree

**Decision (ADR-001):** Do **not** create `backend/src/deepaudit_agent/` as a second package in early milestones. Evolve under `backend/app/`, mapping target folders onto existing modules.

| Target concept | Existing path |
|----------------|---------------|
| API routes | `backend/app/api/v1/endpoints/agent_tasks.py` (later split) |
| Application services | *partially* inlined in `agent_tasks.py` → extract to `services/agent/application/` |
| Domain models | Spread across `models/agent_task.py` + endpoint schemas → `services/agent/domain/` or `app/schemas/agent/` |
| Graph builder/nodes | **NEW** under `services/agent/graph/` (replace ReAct loop gradually) |
| Context Manager | Evolve `llm/memory_compressor.py` + new `services/agent/context/` |
| Sandbox protocol | Evolve `tools/sandbox_tool.py` → `services/agent/sandbox/` |
| Tools registry | `tools/base.py` + package → registry + MCP adapter |
| LLM gateway | `services/llm/` (already closest to gateway) |
| Observability | `telemetry/tracer.py` → `services/agent/observability/` |
| Harness | **NEW** `services/agent/harness/` wrapping graph |
| Infrastructure repos | `models/*` + future `repositories/` |
| Evals | **NEW** `backend/tests/evals/` |
| Docs | `docs/implementation/*`, `docs/architecture/ADR-*` |

Hatch packages only `app` (`pyproject.toml`); a sibling `src/deepaudit_agent` would force build/Docker/import rewrites across ~1k tests — deferred until optional library extraction.

---

## 6. Four-phase ↔ twelve-milestone mapping

| User phase | Milestones | Primary gaps closed |
|------------|------------|---------------------|
| **Phase 1** LangGraph + Pydantic core | M1–M4 | Domain models, graph skeleton, checkpoint, API facade |
| **Phase 2** Context Manager + Sandbox | M5–M7 | Context packs, isolated verification subgraph |
| **Phase 3** MCP + Tracing + Evals | M8–M10 | Governed tools, OTEL, regression quality |
| **Phase 4** Agent Harness | M11 | Spec, budget, policy, router, replay |

Detailed node list and acceptance criteria: [target-architecture.md](./target-architecture.md).

---

## 7. Compatibility interfaces (freeze list)

Until an explicit API version bump:

```text
POST   /api/v1/agent-tasks/
GET    /api/v1/agent-tasks/
GET    /api/v1/agent-tasks/{id}
POST   /api/v1/agent-tasks/{id}/cancel
GET    /api/v1/agent-tasks/{id}/events
GET    /api/v1/agent-tasks/{id}/stream
GET    /api/v1/agent-tasks/{id}/events/list
GET    /api/v1/agent-tasks/{id}/findings
PATCH  /api/v1/agent-tasks/{id}/findings/{finding_id}
GET    /api/v1/agent-tasks/{id}/summary
GET    /api/v1/agent-tasks/{id}/agent-tree
GET    /api/v1/agent-tasks/{id}/checkpoints
GET    /api/v1/agent-tasks/{id}/checkpoints/{checkpoint_id}
GET    /api/v1/agent-tasks/{id}/report
```

Plus classic `/tasks`, `/scan`, `/projects`, `/auth`, `/config` used by non-agent UI.

New target-style routes (e.g. `/api/audits`) may be **additive** adapters, not replacements, until frontend migrates.

---

## 8. Known documentation / tooling gaps

| Item | Status |
|------|--------|
| `docs/ARCHITECTURE.md` | Empty placeholder |
| README agent path | Claims `app/agents/*`; actual is `app/services/agent/` |
| CLAUDE.md / IMPLEMENTATION_STATUS | Added in M0 |
| PR CI pytest/ruff/mypy | Missing |
| `requirements.txt` vs `uv.lock` | Potential tree-sitter pin drift |
| Checkpoint on happy path | Documented/schema yes; runtime weak |
| pre-commit config | Listed in deps; config file not found |

---

## 9. Open questions (for product / later ADRs)

1. Adopt LangGraph for real vs drop unused deps? → **ADR-001 recommends adopt for workflow, keep role agents as node implementations.**
2. Is multi-worker durable queue required in v3.x or only single-process?
3. User-facing **resume** UX vs history-only checkpoints?
4. Deprecate classic `/tasks` path timeline?
5. Project **member** ACL on agent routes?
6. Keep Redis for future broker or remove from “required” messaging?
7. FE `/start` and single-finding GET — fix FE or add routes?
8. MCP in scope for open-source default installs?
9. Official contributor Python pin: 3.12 vs CONTRIBUTING “3.13+”?
10. How aggressively to restrict `run_code` in Phase 1 (analysis_only default)?

---

## 10. M0 acceptance checklist

- [x] Current audit flow documented
- [x] REUSE / MIGRATE / REPLACE / NEW inventory
- [x] Breaking-change / compatibility list
- [x] Target path mapping onto existing tree
- [x] Four-phase → M0–M11 mapping
- [x] No business code modified in M0
