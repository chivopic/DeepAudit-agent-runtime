# IMPLEMENTATION_STATUS

> Living status file for the DeepAudit agent runtime refactor (M0–M11).  
> Update at the end of every milestone.

## Current milestone

**Post-M11 Codex Phase 0/1 hardening** · status: **COMPLETE** (2026-07-24)

M0–M11 remains complete. Phase 0/1 trust + async + budget/MCP/mapper fixes landed same day.

## Milestone roadmap

| ID | Title | Status | Tests |
|----|-------|--------|-------|
| M0 | Architecture assessment | **Done** | docs |
| M1 | Domain models | **Done** | 49 |
| M2 | LangGraph skeleton + Fake LLM | **Done** | 9 |
| M3 | Durable execution / checkpoint | **Done** | 7 |
| M4 | API façade / events | **Done** | 7 |
| M5 | Context Manager | **Done** | 5 |
| M6 | Sandbox worker + policy | **Done** | 6 |
| M7 | Verification subgraph | **Done** | 6 |
| M8 | MCP tools / registry | **Done** | 9 |
| M9 | Observability | **Done** | 6 |
| M10 | Evals + CI | **Done** | 7 |
| M11 | Agent Harness | **Done** | 6 |

## Combined verification (2026-07-24)

```bash
cd backend
uv run pytest \
  tests/test_agent_domain.py \
  tests/test_agent_graph_m2.py \
  tests/test_agent_persistence_m3.py \
  tests/test_agent_facade_m4.py \
  tests/test_agent_context_m5.py \
  tests/test_agent_sandbox_m6.py \
  tests/test_agent_verification_m7.py \
  tests/test_agent_tooling_m8.py \
  tests/test_agent_observability_m9.py \
  tests/test_agent_evals_m10.py \
  tests/test_agent_harness_m11.py \
  -q
# 2026-07-24 post Codex Phase 0/1: **128 passed**
# 2026-07-24 node tools/tracer/budget wiring: **129 passed**
```

## Post-M11 audit hardening (same day)

Retro + quality audit: [`docs/implementation/m0-m11-retro-and-audit.md`](docs/implementation/m0-m11-retro-and-audit.md)

Landed after M11 audit pass:

- Cooperative cancel: `GraphRuntime.cancel_check`, `finalize_cancelled` node, runner status force.
- Path jail on `analyze_file` reads (`_safe_read_under_root`).
- `FilesystemArtifactStore` audit_id sanitize + root jail on get/put.
- `ExecutionStatus.SKIPPED` for NullSandbox non-execution.
- Tests: mid-run cancel, artifact escape, sandbox path traversal.

### Codex Phase 0/1 (2026-07-24)

| Fix | Detail |
|-----|--------|
| Trust boundary | `GRAPH_AUDITS_*` settings; reject client host `local_path`; fixture-only default; fixture path/size caps |
| Async start | `GraphAuditFacade.start_async` → 202; `wait=true` sync for tests |
| Budget terminal | `AuditStatus.PARTIAL`; plan LLM counts budget; report not fake COMPLETED on exhaust |
| MCP fail-closed | discovery required; registry no try-all; empty list_tools cannot invoke |
| Verification | `execution_status` default `SKIPPED` (not SUCCEEDED) on non-exec paths |
| Mapper | `vulnerability_type` / `ai_confidence` / `is_verified` round-trip |

### Node governance wiring (2026-07-24)

Harness tools / tracer / budget_manager are now consumed mid-run:

- `GraphRuntime.tools` / `get_tools()` → `analyze_file` invokes allowlisted `heuristic_scan`
- Node spans: `graph.node.*`, `tool.call`, `llm.call` via injected tracer
- Graph `RunBudget` remains source of truth; harness `BudgetManager` mirrored via `_sync_budget_manager` (no double-count)
- Tests: `test_analyze_uses_tool_registry_and_tracer`, harness e2e span/tool assertions

**Still open (product / next work):** full JWT/project ACL on `/graph-audits` (flag + fixture gate only), true mid-graph resume, multi-worker cancel/events.

## Package map (new under `services/agent/`)

```text
domain/           # M1 Pydantic models + mappers
graph/            # M2 StateGraph + nodes + FakeLLM
  subgraphs/      # M7 verification
persistence/      # M3 checkpointer / business / artifacts
application/      # M3 runner + M4 façade / events / mapping
context/          # M5 ContextManager
sandbox/          # M6 executors + policy
tooling/          # M8 ToolProtocol + Builtin/MCP/Mock + registry
observability/    # M9 Tracer + redaction + metrics
harness/          # M11 AgentSpec + AgentRuntime
```

## API

| Surface | Status |
|---------|--------|
| `/api/v1/agent-tasks/*` | **Unchanged** (production ReAct) |
| `/api/v1/graph-audits/*` | **Added** (LangGraph dual-path, M4) |

## Phase 1 invariants (still held)

1. Happy-path findings: `verification_status=NOT_RUN` until M7 opt-in verify.
2. No untrusted code exec on Phase 1 path (`NullSandboxExecutor`).
3. Raw shell forbidden by sandbox policy and tooling denylist.
4. Checkpoints ≠ business DB listings.
5. Nodes return state deltas; large blobs → ArtifactRef.
6. Secrets redacted in observability attributes.
7. Harness wraps LangGraph; does not reimplement the agent loop.

## Docs added

- `docs/implementation/domain-models.md`
- `docs/implementation/graph-skeleton.md`
- `docs/implementation/durable-execution.md`
- `docs/implementation/api-facade.md`
- `docs/implementation/context-manager.md`
- `docs/implementation/sandbox.md`
- `docs/implementation/verification-subgraph.md`
- `docs/implementation/mcp-tools.md`
- `docs/implementation/observability.md`
- `docs/implementation/evals.md`
- `docs/implementation/agent-harness.md`
- M0 ADRs + architecture docs (prior)

## CI

- `.github/workflows/agent-pytest.yml` — PR/push gate for agent unit tests + eval CI suite

## Explicit non-goals (still deferred)

- Full cutover of production ReAct → LangGraph (keep dual-path)
- SQLAlchemy persistence adapter for business store
- Real Docker worker process (socket out of API)
- Real MCP SDK transport (interface + InMemoryMCPTransport shipped)
- Mandatory OTEL exporter install (optional bridge)
- LLM-as-judge evals
- Human approval UI wiring

## Known residual risks

| ID | Issue | Severity |
|----|-------|----------|
| K1 | Production still ReAct by default | Medium (intentional dual-path) |
| K2 | docker.sock still on API compose | High (ADR-003; worker not deployed) |
| K4 | Cancel mid-flight is cooperative/in-process | Medium |
| K5 | Memory checkpointer default | Medium (dev) |
| K8 | CI gate added; fuller eval suite still local | Low |

## Post-M11 audit (2026-07-24)

See [docs/implementation/m0-m11-retro-and-audit.md](docs/implementation/m0-m11-retro-and-audit.md).

Hardening applied after audit (same day):
- Tool + sandbox path guards (drive letters / traversal)
- Harness `PermissionPolicy` defaults closed (not mirrored from AgentSpec)
- graph-audits forced FakeLLM offline until gateway wiring
- Eval status_terminal no longer vacuous
- Observability redact_value single-pass

Phase 0/1 closed host-path LFI surface + async cancel race + MCP try-all + budget COMPLETED lie + mapper field loss.

Still open (tracked in audit doc): full authz parity, harness enforcement inside nodes, true mid-graph resume, multi-worker event bus.
