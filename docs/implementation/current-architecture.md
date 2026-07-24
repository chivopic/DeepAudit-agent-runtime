# DeepAudit Current Architecture (M0 Survey)

> Survey date: 2026-07-24  
> Branch: `v3.0.0`  
> Product version: `3.0.4`  
> Scope: read-only repository assessment for the agent runtime refactor.

---

## 1. Repository layout

```text
DeepAudit/
├── backend/                 # FastAPI app (package: app)
│   ├── app/
│   │   ├── api/v1/endpoints/
│   │   ├── core/            # config, security, encryption
│   │   ├── db/              # SQLAlchemy async session
│   │   ├── models/          # ORM models
│   │   ├── schemas/         # Pydantic API schemas (partial)
│   │   ├── services/
│   │   │   ├── agent/       # Multi-agent audit runtime
│   │   │   ├── llm/         # LLM gateway (LiteLLM + adapters)
│   │   │   ├── rag/         # Chroma embedding / retrieval
│   │   │   ├── scanner.py   # Traditional non-agent scan path
│   │   │   └── ...
│   │   └── main.py
│   ├── alembic/versions/
│   ├── tests/
│   ├── pyproject.toml       # uv / hatchling
│   └── uv.lock
├── frontend/                # React 18 + Vite + TypeScript
├── docker/sandbox/          # Vulnerability verification image
├── docs/                    # Existing product/architecture docs
├── rules/                   # Audit rule packs
├── scripts/
├── supabase/                # Optional Supabase migrations (legacy path)
└── docker-compose*.yml
```

---

## 2. Stack snapshot

| Layer | Technology | Notes |
|-------|------------|-------|
| Python | 3.12 (`.python-version`), requires `>=3.11` | |
| Package manager | **uv** (`uv.lock`, `pyproject.toml`) + hatchling | Also ships `requirements.txt` / lock for compatibility |
| API | FastAPI + Uvicorn | Entry: `backend/app/main.py` → `app.main:app` |
| Config | `pydantic-settings` `Settings` in `app/core/config.py` | `.env` file, `extra="ignore"` |
| ORM | SQLAlchemy 2.x **async** + asyncpg | `app/db/session.py` |
| Migrations | Alembic | `backend/alembic/` |
| Cache / queue-ish | Redis 7 | Compose service; used for agent task support (not Celery) |
| LLM | LiteLLM + custom adapters | `app/services/llm/` |
| Declared agent libs | `langchain`, `langgraph` in deps | **No runtime `import langgraph` / `StateGraph` found** |
| Vector store | ChromaDB | `VECTOR_DB_PATH` |
| Sandbox | Docker Python SDK | Compose mounts host `docker.sock` into backend |
| Frontend | React 18, Vite, TypeScript, Tailwind, Radix, Ant-style UI pieces | `pnpm` / npm lock present |
| SSE | `sse-starlette` + custom EventManager | Agent task streaming |
| Tests | pytest + pytest-asyncio | `backend/tests/`, agent-focused unit tests |
| Lint/format | Black, Ruff, MyPy (backend); Biome + tsc (frontend) | Declared in `pyproject.toml` / frontend scripts |
| CI | Docker image publish / release workflows | No full unit-test gate observed in workflows surveyed |

### Dev / run commands (from repo)

```bash
# Compose (primary)
docker compose up -d

# Backend (inside container or local)
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Backend tests
cd backend && pytest

# Frontend
cd frontend && pnpm install && pnpm dev
```

---

## 3. Backend entry and API surface

- App factory: [`backend/app/main.py`](../../backend/app/main.py)
- API prefix: `/api/v1` (`settings.API_V1_STR`)
- Health: `GET /health`
- Router registry: [`backend/app/api/v1/api.py`](../../backend/app/api/v1/api.py)

| Prefix | Module | Role |
|--------|--------|------|
| `/auth` | `auth.py` | JWT login / register |
| `/users` | `users.py` | User management |
| `/projects` | `projects.py`, `members.py` | Projects & membership |
| `/tasks` | `tasks.py` | Classic audit tasks (`AuditTask` / `AuditIssue`) |
| `/scan` | `scan.py` | ZIP upload, instant scan, report PDF |
| `/config` | `config.py` | User/system config |
| `/database` | `database.py` | DB admin helpers |
| `/prompts` | `prompts.py` | Prompt templates |
| `/rules` | `rules.py` | Audit rules |
| `/agent-tasks` | `agent_tasks.py` | **Primary multi-agent audit API** |
| `/embedding` | `embedding_config.py` | Embedding provider config |
| `/ssh-keys` | `ssh_keys.py` | SSH keys for private git |

### Agent-task API (frontend-critical)

Implemented in [`backend/app/api/v1/endpoints/agent_tasks.py`](../../backend/app/api/v1/endpoints/agent_tasks.py) (~3.5k LOC, orchestration + HTTP in one module):

| Method | Path | Notes |
|--------|------|-------|
| POST | `/agent-tasks/` | Create **and** auto-start via `BackgroundTasks` |
| GET | `/agent-tasks/` | List |
| GET | `/agent-tasks/{task_id}` | Detail |
| POST | `/agent-tasks/{task_id}/cancel` | Cancel |
| GET | `/agent-tasks/{task_id}/events` | SSE stream |
| GET | `/agent-tasks/{task_id}/stream` | Enhanced SSE (thinking tokens) |
| GET | `/agent-tasks/{task_id}/events/list` | Poll events |
| GET | `/agent-tasks/{task_id}/findings` | Findings list |
| PATCH | `/agent-tasks/{task_id}/findings/{finding_id}` | Update finding status |
| GET | `/agent-tasks/{task_id}/summary` | Summary stats |
| GET | `/agent-tasks/{task_id}/agent-tree` | Dynamic agent tree |
| GET | `/agent-tasks/{task_id}/checkpoints` | Checkpoint list |
| GET | `/agent-tasks/{task_id}/checkpoints/{id}` | Checkpoint detail |
| GET | `/agent-tasks/{task_id}/report` | Report payload |

Frontend clients:

- [`frontend/src/shared/api/agentTasks.ts`](../../frontend/src/shared/api/agentTasks.ts)
- [`frontend/src/shared/api/agentStream.ts`](../../frontend/src/shared/api/agentStream.ts)
- UI: [`frontend/src/pages/AgentAudit/`](../../frontend/src/pages/AgentAudit/)

> Note: frontend `startAgentTask` posts `/agent-tasks/{id}/start`, but the surveyed router does **not** expose a dedicated `/start` route; create already starts the task. Compatibility must be preserved or the frontend fixed intentionally.

---

## 4. Data model (business DB)

Database: **PostgreSQL 15** (`postgresql+asyncpg://...`).

### Classic audit path

| Table / model | File | Purpose |
|---------------|------|---------|
| `audit_tasks` | `models/audit.py` | Non-agent scan tasks |
| `audit_issues` | `models/audit.py` | File/line issues |

### Agent audit path

| Table / model | File | Purpose |
|---------------|------|---------|
| `agent_tasks` | `models/agent_task.py` | Task lifecycle, budgets, phase stats |
| `agent_events` | same | SSE-backed event log |
| `agent_findings` | same | Vulnerability findings |
| `agent_checkpoints` | same | Serialized agent state blobs (`state_data` Text/JSON) |
| `agent_tree_nodes` | same | Dynamic agent tree visualization |

Related: `projects`, `users`, `user_config`, `prompt_templates`, `audit_rules`, analysis history tables.

Migrations of note:

- `006_add_agent_tables.py`
- `007_add_agent_checkpoint_tables.py`
- `008_add_files_with_findings.py`

### Artifact storage (today)

- ZIP / clone working trees under `./uploads` (volume `backend_uploads`)
- Vector DB under `./data/vector_db`
- File-system agent checkpoints under `./agent_checkpoints` (optional path in `AgentStatePersistence`)
- No dedicated object-storage abstraction for snippets/logs/patches

---

## 5. Current agent runtime

### 5.1 Design style

- **Custom multi-agent ReAct loop**, not LangGraph `StateGraph`.
- Hierarchy: `OrchestratorAgent` → `ReconAgent` | `AnalysisAgent` | `VerificationAgent`.
- Each agent inherits `BaseAgent` (`agents/base.py`) with message history, tool execution, event emission.
- Docs claim LangGraph; dependency includes `langgraph`; **runtime code does not build or invoke a LangGraph graph**. `StreamHandler` has LangGraph-shaped event adapters prepared for a future/partial integration.

### 5.2 Execution flow (actual)

```text
POST /agent-tasks/
    → insert AgentTask (PENDING)
    → BackgroundTasks.add_task(_execute_agent_task)
        → SandboxManager.initialize()
        → EventManager queue + AgentEventEmitter
        → resolve project root (zip extract / git clone / SSH)
        → LLMService(user_config)
        → _initialize_tools(...)  # RAG index may run here
        → create Recon / Analysis / Verification / Orchestrator
        → Orchestrator.run(...)  # LLM ReAct: dispatch_agent | summarize | finish
        → persist findings, tree, stats
        → emit completion events
```

Phases tracked on `AgentTask`: planning → indexing → reconnaissance → analysis → verification → reporting.

### 5.3 Core modules under `app/services/agent/`

| Area | Path | Status |
|------|------|--------|
| Agents | `agents/{base,orchestrator,recon,analysis,verification}.py` | Production path |
| State | `core/state.py` (`AgentState` Pydantic) | Full message history in state |
| Registry / tree | `core/registry.py`, `core/executor.py`, `core/graph_controller.py` | Custom “graph” = agent tree controller |
| Persistence | `core/persistence.py` + ORM checkpoints | FS + optional DB; not LangGraph checkpointer |
| Resilience | `retry`, `circuit_breaker`, `rate_limiter`, `fallback`, `validation` | Reusable |
| Context | `core/context.py` | Correlation ID / task id **tracing**, not LLM context packing |
| Events | `event_manager.py`, `streaming/*` | SSE + DB events |
| Tools | `tools/*` | File, pattern, smart_scan, RAG, sandbox, run_code, external scanners |
| Knowledge | `knowledge/*` | Framework + vulnerability pattern modules |
| RAG service | `services/rag/*` | Embeddings, splitter, indexer, retriever |
| LLM | `services/llm/*` | Factory, adapters, `MemoryCompressor`, tokenizer, prompt cache |
| Telemetry | `telemetry/tracer.py` | Custom in-process Tracer (file/CSV), **not OpenTelemetry** |

### 5.4 LLM integration

- Unified-ish gateway: `LLMService` + `LLMFactory` + LiteLLM adapter + native adapters (Baidu, Doubao, MiniMax, …).
- Config priority: user DB config → env `Settings`.
- Structured output: ad-hoc JSON parse + `json-repair`; **not consistently Pydantic-validated model outputs**.
- Memory: `MemoryCompressor` keeps system + recent messages; natural-language style compression thresholds (~100k tokens).

### 5.5 Sandbox (today)

- Image: `deepaudit/sandbox:latest` built from `docker/sandbox/`.
- Manager: `tools/sandbox_tool.py` (`SandboxManager`, `SandboxConfig`).
- Defaults: network `none`, read-only root, non-root user, memory/CPU limits, cap-drop list, no-new-privileges.
- Custom seccomp profile present: `docker/sandbox/seccomp.json`.
- **Critical deployment coupling**: backend container mounts **host Docker socket** (`docker-compose.yml`).
- Tool surface includes `run_code` (LLM-authored code executed in sandbox) and higher-level verification helpers — closer to “execute generated code” than “only high-level verification actions”.

### 5.6 Task execution model

- **Not Celery / ARQ**: FastAPI `BackgroundTasks` + in-memory maps (`_running_tasks`, `_running_asyncio_tasks`).
- Redis present for agent support; not a full durable work queue for audit graphs.
- Cancel via flags + asyncio task cancel.

---

## 6. Frontend contract highlights

Must-not-break shapes (from `agentTasks.ts`):

- Task fields: status, phases, file/finding/token counters, severity counts, progress_percentage, error_message.
- Finding fields: vulnerability_type, severity, title, file_path, line_start/end, code_snippet, is_verified, confidence/ai_confidence, suggestion, poc fields, status.
- Event fields: event_type, phase, message, tool_*, sequence, timestamp (alias from `created_at`), metadata.
- SSE reconnection via `after_sequence`.

---

## 7. Testing and quality gates

- Strong unit coverage on agent **infrastructure** (state, retry, rate limit, circuit breaker, persistence, event manager, stream handler, LLM factory).
- Limited true **graph E2E with fake LLM** for full audit pipeline as a durable state machine.
- No dedicated eval fixture suite under `tests/evals/`.
- CI emphasizes Docker publish/release more than pytest gates.

---

## 8. Existing documentation (pre-M0)

| Doc | Role |
|-----|------|
| `docs/AGENT_AUDIT_ARCHITECTURE.md` | Detailed multi-agent design (authoritative for current agent intent) |
| `docs/AGENT_AUDIT.md` | Agent usage |
| `docs/AGENT_DEPLOYMENT_CHECKLIST.md` | Deploy checklist |
| `docs/CONFIGURATION.md`, `DEPLOYMENT.md`, `LLM_PROVIDERS.md` | Ops |
| `docs/PAPER_ARCHITECTURE.md` | Research-oriented architecture |
| `docs/ARCHITECTURE.md` | **Empty / placeholder** |
| `docs/implementation/*`, `docs/architecture/ADR-*` | **Created by M0** |

No `CLAUDE.md` or `IMPLEMENTATION_STATUS.md` existed before M0.

---

## 9. Current audit flow (sequence)

```text
User selects project → Create agent task
  → Clone/extract repository
  → (Optional) RAG indexing of code chunks
  → Orchestrator LLM loop
        ├─ dispatch recon  → structure / tech stack / hotspots
        ├─ dispatch analysis → tools + LLM findings
        └─ dispatch verification → sandbox / PoC
  → Save AgentFinding rows + events + tree + stats
  → Frontend consumes SSE + findings + report
```

Parallel path: classic `/scan` and `/tasks` for non-agent scans (`AuditIssue`).

---

## 10. Security-relevant observations (as-is)

1. Docker socket mounted into API container (large blast radius if API is compromised).
2. LLM can author arbitrary code via `run_code` (mitigated by container defaults, but still broad).
3. `command: str` style APIs exist in sandbox manager (shell-oriented), not only argv arrays.
4. Checkpoints may serialize large message histories into DB/FS.
5. Tracing may retain more content than production redaction policy should allow.
6. JWT + encrypted secrets for SSH keys in user config — keep encryption module when migrating.

---

## 11. Diagram: current runtime

```text
┌────────────┐   REST/SSE    ┌──────────────────────────────┐
│  Frontend  │──────────────▶│  FastAPI  /api/v1/agent-tasks │
└────────────┘               └──────────────┬───────────────┘
                                            │ BackgroundTasks
                                            ▼
                                 ┌──────────────────────┐
                                 │  _execute_agent_task │
                                 └──────────┬───────────┘
                      ┌─────────────────────┼─────────────────────┐
                      ▼                     ▼                     ▼
               Project root            LLMService            SandboxManager
               (git/zip)               (LiteLLM)             (Docker SDK)
                      │                     │                     │
                      ▼                     ▼                     ▼
                 RAG/Chroma          OrchestratorAgent      sandbox container
                                        │ recon/analysis/verify
                                        ▼
                                 PostgreSQL agent_* tables
                                 EventManager → SSE
```
