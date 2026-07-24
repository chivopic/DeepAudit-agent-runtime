# Target Architecture: DeepAudit Agent Runtime

> M0 deliverable · 2026-07-24  
> Placement decision: evolve under `backend/app/services/agent/` (and related `app/*`), **not** a second package root.

Related: [current-architecture.md](./current-architecture.md) · [gap-analysis.md](./gap-analysis.md) · ADRs

---

## 1. Goals

Build a **durable, observable, secure** multi-agent audit runtime where:

1. **LangGraph** owns state transitions, node scheduling, parallel merge, checkpoint, interrupt/resume.
2. **Pydantic** owns domain objects, API I/O, tool I/O, and structured LLM outputs.
3. **Business DB** owns queryable audit/finding/report records.
4. **Artifact Store** owns large source, logs, patches, raw model dumps.
5. **Context Manager**, **Sandbox**, **Tools/MCP**, **Observability** plug in via **protocols** — nodes never bind to concrete SDKs.
6. **Agent Harness** (Phase 4) wraps LangGraph; it does **not** reimplement the agent loop.

Phase 1 intentionally ends with findings marked:

```text
verification_status = NOT_RUN
```

No untrusted code execution in the Phase 1 happy path.

---

## 2. Component diagram

```text
                         ┌──────────────────────┐
                         │  FastAPI / Frontend  │
                         │  /api/v1/agent-tasks │  (compat facade)
                         │  optional /api/audits│  (additive later)
                         └──────────┬───────────┘
                                    │
                         ┌──────────▼───────────┐
                         │  Application Layer   │
                         │  Run / Resume / Stop │
                         │  Event fan-out (SSE) │
                         └──────────┬───────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │         Agent Harness          │  (M11; thin before that)
                    │ Budget / Policy / Model / Tools│
                    └───────────────┬────────────────┘
                                    │
                    ┌───────────────▼────────────────┐
                    │        LangGraph Graph         │
                    │ validate → ingest → manifest   │
                    │ → plan → analyze* → aggregate  │
                    │ → dedupe → prioritize → report │
                    │ (+ verify subgraph Phase 2+)   │
                    └──────┬────────┬────────┬───────┘
                           │        │        │
                 ┌─────────▼─┐ ┌────▼────┐ ┌─▼────────────┐
                 │ Context   │ │  Tools  │ │   Sandbox    │
                 │ Manager   │ │ Builtin │ │ Docker Worker│
                 └───────────┘ │ / MCP   │ └──────────────┘
                               └────┬────┘
                                    │
                            ┌───────▼────────┐
                            │ RAG / DB / AST │
                            │ Search / Files │
                            └────────────────┘

  PostgreSQL: business tables + LangGraph checkpoint tables
  Artifact Store: snippets, logs, patches, verification products
  OpenTelemetry: traces / metrics / logs (redacted)
  LangSmith: optional, behind observability interface only
```

---

## 3. Package placement (accurate paths)

```text
backend/app/
├── api/v1/endpoints/
│   └── agent_tasks.py          # Compat facade (keep routes)
├── services/
│   ├── agent/
│   │   ├── domain/             # Pydantic domain (M1)
│   │   ├── graph/              # LangGraph builder, state, nodes (M2+)
│   │   │   ├── builder.py
│   │   │   ├── state.py        # TypedDict + reducers
│   │   │   ├── routing.py
│   │   │   ├── nodes/
│   │   │   └── subgraphs/
│   │   ├── application/        # Run coordinator, commands (M3–M4)
│   │   ├── context/            # Context Manager (M5)
│   │   ├── sandbox/            # Executor protocol + docker (M6)
│   │   ├── tools/              # Existing tools → registry (M8)
│   │   ├── harness/            # AgentSpec runtime (M11)
│   │   ├── observability/      # OTEL facade (M9)
│   │   ├── agents/             # Existing role agents (migrate → nodes)
│   │   └── core/               # Resilience primitives (wire into harness)
│   ├── llm/                    # Existing gateway (LLMGateway evolution)
│   └── rag/                    # Existing retrieval
├── models/                     # SQLAlchemy business models (extend)
└── ...
backend/tests/
├── unit/ | graph/ | integration/ | sandbox/ | evals/
docs/
├── implementation/
├── architecture/ADR-*.md
IMPLEMENTATION_STATUS.md
CLAUDE.md
```

Do **not** introduce `backend/src/deepaudit_agent/` until a library extraction is explicitly approved.

---

## 4. Data flow

```text
1. Client POST agent-task (or future AuditRequest)
2. Application creates business row AgentTask (audit_id = task.id)
3. Harness/runtime starts graph with thread_id = audit_id
4. Nodes return state deltas only
5. Checkpointer persists graph state at boundaries
6. Side effects write:
     - findings / events → PostgreSQL business tables
     - large payloads → Artifact Store → ArtifactRef in state
7. Event bus → SSE (existing EventManager shapes)
8. Cancel / resume use graph interrupt + checkpointer
```

---

## 5. LangGraph nodes (Phase 1)

| Node | Responsibility |
|------|----------------|
| `validate_request` | Paths, budgets, reject traversal, create/normalize audit_id |
| `ingest_repository` | Snapshot, commit hash, size limits, exclude build/vendor |
| `build_manifest` | Path, language, size, hash, auditability |
| `plan_audit` | Structured `AuditPlan` (scope, priority, budget split, stop rules) |
| `analyze_file` | Parallel unit; structured findings only; no code exec |
| `aggregate_findings` | Normalize; drop evidence-less items |
| `deduplicate_findings` | CWE + path + lines + dataflow + root cause + code hash |
| `prioritize_findings` | Severity, evidence, reachability, multi-analyzer agreement |
| `generate_report` | Unverified report; explicit NOT_RUN / gaps / failures |

### Later nodes (Phase 2+)

```text
prioritize_findings
  → select_verification_strategy
  → verify_finding (sandbox subgraph)
  → update_confidence
  → generate_report
```

Human approval gates: network, install scripts, non-whitelist commands, external targets, high privilege.

---

## 6. State model

Prefer **TypedDict** for LangGraph state; **Pydantic** for objects inside:

```python
class AuditState(TypedDict, total=False):
    audit_id: str
    request: AuditRequest
    repository: RepositorySnapshot
    manifest: RepositoryManifest
    plan: AuditPlan
    candidate_files: list[FileArtifact]
    candidate_findings: Annotated[list[CandidateFinding], add]
    normalized_findings: list[Finding]
    verified_findings: Annotated[list[VerifiedFinding], add]
    context_snapshot: ContextSnapshot
    budget: RunBudget
    errors: Annotated[list[NodeError], add]
    status: AuditStatus
    report: AuditReport
```

Rules:

- Nodes return **deltas**, not full state copies.
- Parallel lists use **reducers**.
- No giant single Pydantic State class.
- No raw multi-MB source in checkpoint — only `ArtifactRef`.

### Core domain models (M1 minimum)

```text
AuditRequest, RepositoryRef, RepositorySnapshot, RepositoryManifest,
FileArtifact, AuditPlan, AuditTask, CandidateFinding, Finding, Evidence,
SourceLocation, VerificationRequest, VerificationResult, FixProposal,
ArtifactRef, RunBudget, ModelUsage, NodeError, AuditReport
```

**Finding** must include: id, title, description, severity, confidence, cwe_id, file_path, start_line, end_line, evidence, attack_preconditions, impact, recommendation, verification_status, source_agent, source_model, created_at.

**Evidence** must include: file_path, start_line, end_line, code_hash, snippet_artifact_id, reasoning_summary, data_flow, references.

---

## 7. Persistence boundaries

| Store | Responsibility | Technology |
|-------|----------------|------------|
| LangGraph Checkpointer | Node state, resume, interrupt | Dev: SQLite; Prod: Postgres (`thread_id = audit_id`) |
| Business DB | audits/tasks, events, findings, reports, tree UI | Existing PostgreSQL + Alembic |
| Artifact Store | source snippets, logs, patches, raw LLM dumps | Start: filesystem under uploads; later S3-compatible |
| Vector DB | RAG chunks | Existing Chroma |
| Observability | traces/metrics | OTEL exporters |

**Never** query checkpoints for product listings or dashboards.

---

## 8. Context Manager boundary (Phase 2)

```text
Pinned · Active · RunningSummary · Retrieved · ArtifactRefs
        → ContextManager.build_context → ContextPack
        → optional compact() under token budget
```

Nodes **must not** freely concatenate unbounded history.

Never lose on compaction: finding IDs, paths, lines, code hashes, user decisions, verification results, pending tasks, artifact IDs, security policy.

---

## 9. Sandbox trust boundary (Phase 2)

```text
API process  ──queue/RPC──▶  Sandbox Worker  ──▶  ephemeral containers
     ✗ no docker.sock                          ✓ isolated runtime
```

Defaults: non-root, rootless where possible, `network=none`, read-only root, tmpfs workdir, `cap-drop=ALL`, no-new-privileges, seccomp, CPU/mem/PID/time/output limits.

Forbidden: privileged, host net/PID, docker.sock in exec container, writable host repo mount, shell-string user input, auto network, auto install scripts.

Model sees **high-level actions** only (`run_selected_test`, `run_static_analyzer`, …), not raw shell.

See [ADR-003](../architecture/ADR-003-sandbox-boundary.md).

---

## 10. MCP boundary (Phase 3)

```text
Node → Tool Protocol → BuiltinToolAdapter | MCPToolAdapter | MockToolAdapter
```

No business node imports MCP SDK. All tools: Pydantic in/out, timeout, authz, audit log, trace, error normalization. No `shell.exec` / `filesystem.write_anywhere`.

---

## 11. Tracing structure (Phase 3)

```text
audit.run
├── graph.node.*
│   ├── context.build
│   ├── llm.call
│   └── tool.call
├── context.compact
├── sandbox.execute
├── verification.run
└── report.generate
```

Attributes: audit.id, thread.id, node.name, model.*, tool.name, finding.id, tokens.*, cost.*, duration.ms, retry.count, result.status.

Default **deny** logging: full source, full prompts, API keys, tokens/passwords, full tool dumps (dev flag may expand).

---

## 12. Evals structure (Phase 3)

```text
backend/tests/evals/
├── fixtures/{python,javascript,java,mixed}/{vulnerable,safe}/
├── evaluators/   # schema, location, hash, policy, precision/recall
└── runner.py
```

Prefer deterministic checks before LLM-as-judge. CI: small set on every commit; fuller set on PR; complete suite scheduled.

---

## 13. Harness boundary (Phase 4)

```text
AgentSpec → AgentRuntime.start/resume/cancel/stream_events
  ModelRouter · ToolRouter · ContextPolicy · PermissionPolicy
  BudgetManager · RetryPolicy · ArtifactRegistry · EventBus · HumanApproval
        ↓
   LangGraph compiled graph
```

Harness **governs** execution; LangGraph **executes** the workflow.

---

## 14. API strategy

### Compatibility (required)

Keep existing `/api/v1/agent-tasks/*` as façade over application layer.

### Additive target (optional later)

```http
POST   /api/audits
GET    /api/audits/{audit_id}
GET    /api/audits/{audit_id}/findings
GET    /api/audits/{audit_id}/events
GET    /api/audits/{audit_id}/report
POST   /api/audits/{audit_id}/resume
POST   /api/audits/{audit_id}/cancel
```

SSE event names may extend: `audit.started`, `node.started`, `node.completed`, `finding.created`, `audit.compacted`, `verification.started`, `audit.completed`, `audit.failed` — map onto existing frontend types during transition.

---

## 15. Phased migration plan

| Milestone | Content | Suggested commit |
|-----------|---------|------------------|
| **M0** | Survey + docs + ADRs | `docs(agent): document target architecture` |
| **M1** | Pydantic domain + config | `feat(agent-core): add validated domain models` |
| **M2** | Graph skeleton + Fake LLM | `feat(agent-core): add audit graph skeleton` |
| **M3** | Checkpointer + business persistence + resume | `feat(agent-core): add durable execution` |
| **M4** | API façade, events, cancel/resume | `feat(agent-api): expose audit lifecycle` |
| **M5** | Context Manager + artifact offload | `feat(context): add managed context compaction` |
| **M6** | Sandbox worker + policy | `feat(sandbox): add isolated verification runner` |
| **M7** | Verification subgraph | `feat(verification): verify findings in sandbox` |
| **M8** | Tool registry + MCP adapter | `feat(mcp): add governed tool integration` |
| **M9** | OTEL + metrics + redaction | `feat(observability): trace agent execution` |
| **M10** | Eval datasets + CI gate | `test(evals): add agent quality regression suite` |
| **M11** | Agent Harness | `feat(harness): add governed agent runtime` |

### Per-milestone loop

```text
Read code → plan → tests first → implement → format/lint/type/test → docs + IMPLEMENTATION_STATUS → done
```

No unrelated refactors; no second ORM/config; no real paid models in default tests.

---

## 16. Phase 1 acceptance (reminder for M1–M4)

- End-to-end graph run with **Fake LLM**
- Real models only via LLM gateway
- Resume from checkpoint after node failure
- Idempotent business writes for same `audit_id`
- API independent of LangGraph internal state shape
- All findings Pydantic-validated
- No arbitrary code execution in Phase 1 path
- Core domain + routing tests green

---

## 17. Mapping existing roles → graph

| Today | Tomorrow |
|-------|----------|
| `_execute_agent_task` prep (clone/index) | `ingest_repository` + `build_manifest` (+ indexing side effect) |
| Orchestrator plan/dispatch | `plan_audit` + graph edges (deterministic skeleton first; LLM plan structured) |
| ReconAgent | Optional plan inputs / specialized analyze prep |
| AnalysisAgent tools loop | `analyze_file` (+ tools via registry) |
| VerificationAgent | Phase 2 `verify_finding` subgraph |
| `_save_findings` | Application repository after aggregate/dedupe |
| EventEmitter | Harness/event bus → existing SSE |
| Agent tree UI | Optional projection from graph runs + tree nodes table |
