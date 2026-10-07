# IMPLEMENTATION_STATUS

> Living status file for the DeepAudit agent runtime refactor (M0–M11).  
> Update at the end of every milestone.

## Current milestone

**R2–R5 single-host product path** · status: **LOCAL REGRESSIONS PASSED; PRODUCT ACCEPTANCE PENDING** (2026-10-07)

New Agent audits go through the existing `/api/v1/agent-tasks` API. The server default is `engine=graph` (`AGENT_RUNTIME_ENGINE`). A client can still send `engine=react` and run the classic ReAct path. Old rows with no `agent_config.engine` stay on ReAct. `/api/v1/graph-audits` remains the fixture-only surface and still injects `FakeLLM()`.

M0–M11 stay recorded below as the experimental runtime. R1 stays the trustworthiness baseline. R2–R5 below are the product path a configured user can start from the existing create dialog. They are not a multi-host Postgres cutover and not an isolated Docker worker.

| ID | Title | Status |
|----|-------|--------|
| R1 | Execution trustworthiness | Local regressions pass; product acceptance remains pending. |
| R2 | Gateway, authorized snapshot, tools, source windows | Bounded source artifacts, explicit omissions and glob exclusions. RAG is not wired. |
| R3 | Durable resume on one host | File checkpoint + SQLite + pinned source artifacts. OS run lock, shared cancellation and event replay. Other product persistence backends fail closed. |
| R4 | `/api/v1/agent-tasks` on the graph runtime | Create starts the run; paused/failed tasks resume the saved source. Authenticated browser and database acceptance is pending. |
| R5 | Serial analysis, static recheck, experimental MCP | Serial execution; static recheck remains INCONCLUSIVE. True concurrency, isolated Docker verification and product MCP are not delivered. |

## Repository entry points

| Entry | Location | Current role |
| --- | --- | --- |
| Web product | `backend/` + `frontend/` | Existing Agent task API; defaults to LangGraph |
| Graph CLI prototype | `backend/app/cli/` | Backend-dependent local runtime prototype |
| Lightweight CLI | `cli/` | Separate package; standard library + external Semgrep; see [CLI README](cli/README.md) |

Both Python distributions register the command `deepaudit`; use separate environments when installing both.

## Acceptance corrections (2026-10-03)

The 2026-10-02 acceptance report found real defects in the original product landing. [Repair record](docs/REPAIR_2026-10-03.md) maps A1–A9 to this working tree and records validation limits.

Validation recorded on 2026-10-07: backend suite **1158 passed, 8 skipped** with PDF report tests excluded; focused runtime/product/event suite **176 passed**, including **38 new regressions**; offline evals **3/3**; frontend TypeScript passed. Real database/browser, container recovery and PDF acceptance remain pending.

- Snapshots reject symlinks, preserve the exact requested scope, apply glob exclusions, and record file/byte limits and unreadable input. Reports show the full discovered scope and become partial when input was omitted.
- Source content is a persistent artifact referenced by its hash. Resume uses that artifact, keeps progress/cancellation hooks, and refuses missing checkpoints or unverifiable legacy snapshots instead of silently restarting.
- Static pattern rechecks remain inconclusive and do not increase confidence or mark findings verified. Plans report serial execution (`max_parallel=1`).
- Product workers use an OS run lock before preparing a checkout or changing task state. Shared files carry cancellation and event replay; duplicate workers leave the current owner alone. Compose persists runtime files in a named volume.
- Product assembly reads the configured backends and accepts `file` only. This is a single-host implementation; PostgreSQL graph persistence, distributed workers, RAG, production MCP and real Docker verification remain outstanding.

**CLI-L1 lightweight extraction** · status: **IMPLEMENTED / DOGFOOD** (2026-08-20)

- ADR-004 authorizes an independent `cli/` distribution without changing the
  server-side Agent Runtime decisions.
- Base CLI uses only the Python standard library plus an explicit external
  Semgrep `1.173.0` executable.
- Implemented: `doctor`, non-interactive `audit`, terminal/JSON output,
  deterministic manifest, governed subprocess, E0 candidate normalization,
  coverage/partial semantics, stable exit codes, and atomic external output.
- Verification: 19 CLI tests, Ruff, strict MyPy, real Semgrep 1/1 longitudinal
  scan, 17 KB wheel, and clean `--no-deps` install in a fresh venv.
- Design record: [`docs/CLI_LIGHTWEIGHT_DESIGN.md`](docs/CLI_LIGHTWEIGHT_DESIGN.md).

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
# 2026-07-24 graph-audits JWT auth: **131 passed** (agent suite)
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

### Graph-audits JWT auth (2026-07-24)

- All `/api/v1/graph-audits/*` routes require `deps.get_current_user` (parity with agent-tasks).
- Start stamps `AuditRequest.user_id`; subsequent routes enforce owner (or superuser).
- Optional `GRAPH_AUDITS_ENFORCE_PROJECT_ACL` (default False) checks Project owner/member when `project_id` set.
- Tests: unauthenticated 401/403, owner happy path, cross-user 403.

**Still open after R2–R5:** multi-host Postgres resume, a SQLAlchemy graph business store, an isolated Docker verification worker, RAG, and registering stdio MCP on the product tool router. Single-host file resume is covered in the R2–R5 section.

## R1 execution trustworthiness (2026-10-02)

Landed on the experimental graph runner. The production ReAct path and the `/api/v1/agent-tasks` SSE shapes are unchanged. `get_langchain_tool()` in `tools/base.py` is unchanged: its `langchain.tools` import is still incompatible, and no production caller uses it.

Behavior:

- `graph/limits.py` sets `recursion_limit` from the caller's budget: 9 pipeline nodes + bounded analysis units + 8 terminal/slack steps. When both caps are positive, the unit count is `min(max_files, max_model_calls)`. `max_files == 0` means no file cap, so the model-call cap is the bound. A 100-file budget therefore uses 117, not a fixed multi-thousand ceiling. `AuditRunner.ainvoke` also sets `durability="sync"` so the previous superstep is flushed before the next.
- `ModelUsage` records `attempt_count`, `success_count`, `failure_count`, `unknown_token_calls`, and `invalid_output_count`. `call_count` remains successful responses with known tokens. A raised call consumes one model-call attempt and counts the tokens as unknown.
- `plan_audit` and `analyze_file` write `NodeError` plus `meta["analysis_coverage"]`. A planner failure still builds the deterministic plan and is stored as `planner_error`. A tool failure is a degraded unit. Non-JSON or a malformed findings array is invalid structured output. An acknowledgement object such as `{"ok": true}` is an empty finding list.
- `generate_report` maps a closed failure (planned work, no findings, nothing succeeded, no degraded units, and either failed units or a planner error) to `FAILED`. A coverage gap, budget stop, truncation, or heuristic fallback is `PARTIAL` and names the unfinished units. Files dropped by `max_files` inside `build_manifest` are `coverage.omitted_units` with reason `file_budget`. Files left in `pending_task_ids` after the model-call cap stops the router are `skipped_units` with reason `budget_exhausted`. A successful plan that then exhausts the budget stays `PARTIAL`.
- A file longer than 4000 characters is listed in `truncated_units` on the default graph. The product assembly turns on line-ranged windows; see the R2–R5 section.
- If the graph raises, `AuditRunner` reads the last checkpoint and persists the findings, events, usage, and error message. The stored status is `FAILED`.

Regression coverage is `backend/tests/test_agent_graph_r1.py`: budget-shaped step ceiling, usage addition, cancel-callback init, 16/30/100-file completion, full model timeout, heuristic degradation, partial model failure, invalid output, preview truncation, file-budget omissions, model-call queue leftovers, and exception salvage.

Verification on 2026-10-02 with `backend/.venv` (Python 3.12.13). No database, Docker, paid model, or browser:

```bash
cd backend
.venv/bin/python -m pytest \
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
  tests/test_agent_graph_r1.py \
  -q
# 147 passed
```

The five agent-init tests in `tests/agent/test_agents.py`, the two event-stream tests that patch `get_agent_config`, and `tests/test_executor.py::TestDynamicAgentExecutor::test_constructor_reads_config_when_timeout_is_none` passed in the same environment (117 passed together with the domain, graph, persistence, harness, and facade suites).

The backend suite with report generation excluded:

```bash
cd backend
.venv/bin/python -m pytest -q --tb=line --disable-warnings --ignore=tests/test_report_generator.py
# 1102 passed, 8 skipped
```

Before R1 the same command was 1080 passed, 8 failed, 8 skipped. The 8 failures were the agent-init and `get_agent_config` cases above. The 14 new R1 tests account for the rest of the increase (1080 + 8 + 14 = 1102).

Ruff selectors `E,F,B,C4` are clean on `limits.py`, `runner.py`, and `graph/nodes/__init__.py`. Ruff and Black are clean on `limits.py` and `tests/test_agent_graph_r1.py`. Legacy files were not reformatted. `common.py` still has a pre-existing Black wrap on `SourceLocation.code_hash`; that line was left as it was. `mypy --follow-imports=silent` on `limits.py`, `common.py`, and `runner.py` reports two pre-existing notes on `AuditRunner._graph` (missing return annotation, unused type ignore). It reported no new error in the R1 modules.

Not verified in the R1 round: Postgres, Docker, a real model, the browser, the frontend build, and the CLI suite. WeasyPrint still cannot be imported here (`libgobject-2.0-0` is missing), so `tests/test_report_generator.py` was excluded from collection. That exclusion is an environment gap. It is not evidence that PDF export works or that the report code changed.

## R2–R5 original landing (2026-10-02; historical)

The following records the original implementation. The acceptance corrections above supersede its statements about verification, parallelism, leases and source loading.

A user with a project and a model key can create an Agent audit from the existing dialog. The dialog defaults to LangGraph. Pattern confirmation is a checkbox and stays off. ReAct is the other engine button. Creating the task already schedules `_execute_agent_task`. There is still no `POST /{id}/start` route. Paused or failed graph tasks expose `POST /{id}/resume`.

What the graph path does:

- `LLMServiceGateway` calls `LLMService.chat_completion_raw`. Usage is copied. The response raw payload is only `{has_usage: bool}`. A missing key does not build a gateway and does not run FakeLLM as a successful model audit. The task stays partial, pattern analysis still runs, and the report says no model API key was configured.
- Project bytes come from the server snapshot of the project root (`load_authorized_snapshot`). The graph request uses the literal `project://authorized`. Client host paths and git URLs stay rejected on `/graph-audits`.
- Product runs set context windows, finding location checks, pattern scan, and cross-file linking when there is more than one file. Parallel width is 2 for multiple files, capped at 8. Default unit tests stay at width 1 unless they opt in.
- Checkpoints are `FileCheckpointSaver` under `AGENT_STATE_DIR` (default `./data/agent_runtime`, relative to the process cwd). Business resume rows are stdlib sqlite (`graph_audit_records`). The UI still reads `AgentTask`, `AgentFinding`, and `AgentEvent`. A second process can continue a paused thread; the two-process test shows `a.py` analyzed only in process A and `b.py` only in process B. `create_checkpointer(backend="postgres")` raises and does not fall back to memory. Alembic head remains `008_add_files_with_findings`.
- One worker holds a file lease. A second owner gets `LeaseBusy`, and the task is marked failed with an error event. Events during the run are the existing SSE `info` and `progress` events and replay by sequence. The activity log uses short Chinese steps, one updating file-progress line, clue lines for pattern hits, and a closing sentence with files read, elapsed time, and a clear statement that pattern hits are not confirmed vulnerabilities. A task that already finished keeps the log it stored. Resume replaces that task's findings and resets severity counts so a continuation does not insert duplicates.
- Verification stays `NOT_RUN` unless `graph_verification` is stored on the task. When it is on, the conditional node runs `LocalAllowlistExecutor` in process (pattern hits for `eval(`, `os.system`, `innerHTML`, `SELECT *`). That can mark a finding confirmed. It is not an isolated Docker worker. `DockerSandboxExecutor` still returns FAILED.
- `StdioMCPTransport` speaks JSON-RPC to an operator-supplied argv (`shell=False`). A local script test lists and calls a tool. The product tool router does not register it.
- Tracer lookup prefers a ContextVar, so two concurrent tasks do not share one mutable tracer. `agent_registry.clear()` runs only for the ReAct branch.
- CI evals: `py-vuln-sqli`, `py-safe-param`, `py-cross-file`. `python -m tests.evals.runner --ci` exits non-zero when a case fails.

Frontend: both create dialogs send `engine` and `graph_verification`. The audit header shows LangGraph or ReAct, and a Resume button for paused or failed graph tasks. Task lists label `partial` and `paused` instead of treating them as waiting. After `pnpm install --frozen-lockfile`, `tsc --noEmit` passed, and the Agent Audit helper tests plus `agentTasks` API tests passed (79). A logged-in browser walkthrough was not run.

Not claimed:

- Postgres checkpoint or a SQLAlchemy graph store.
- Automated model quality. Tests inject a fake gateway or omit the key. A later local trial with a user-saved model key finished partial; that run is not a CI check.
- Docker verification, host-network sandbox, or RAG.
- A logged-in browser walkthrough. No browser tool was available in this session.
- WeasyPrint PDF export (`libgobject-2.0-0` is still missing).
- The independent `cli/` package. It stays zero-backend-deps.

```bash
cd backend
.venv/bin/python -m pytest tests/test_agent_product_r2.py \
  tests/test_agent_graph_r1.py tests/test_agent_graph_m2.py \
  tests/test_agent_persistence_m3.py tests/test_agent_harness_m11.py \
  tests/test_agent_tooling_m8.py tests/test_agent_observability_m9.py \
  tests/test_agent_facade_m4.py -q
# 84 passed

.venv/bin/python -m mypy --follow-imports=silent \
  app/services/agent/graph/gateway.py \
  app/services/agent/graph/context_windows.py \
  app/services/agent/graph/verify_node.py \
  app/services/agent/application/project_source.py \
  app/services/agent/application/assembly.py \
  app/services/agent/application/product_audit.py \
  app/services/agent/persistence/file_checkpointer.py \
  app/services/agent/persistence/sqlite_store.py \
  app/services/agent/persistence/control.py \
  app/services/agent/tooling/mcp_stdio.py
# Success: no issues found in 10 source files

.venv/bin/python -m tests.evals.runner --ci
# total 3, passed 3, including py-cross-file

.venv/bin/python -m pytest -q --tb=line --ignore=tests/test_report_generator.py
# 1117 passed, 8 skipped
```

Activity log copy, same day: `activity_log.py` and `product_audit.py` passed ruff, black, and mypy. `tests/test_agent_product_r2.py` then passed 16. Frontend `tsc --noEmit` passed. A task that already finished keeps the log text it stored. A `partial` task's progress bar is 100 because the run has stopped; `analyzed_files / total_files` still shows how many files were read. `paused` keeps the phase-weighted position. The analysis-progress denominator is the queued manifest, then the plan, before a larger snapshot total.

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
| `/api/v1/agent-tasks/*` | **Product path.** New tasks default to `engine=graph`. `engine=react` is the classic fallback. Response and SSE field names stay the same. `POST /{id}/resume` continues a paused or failed graph task. `POST /{id}/start` is still absent; create already schedules the run. |
| `/api/v1/graph-audits/*` | **Fixture path** (M4). Still injects `FakeLLM()` and rejects client host paths. |

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

- Removing the ReAct fallback (it stays available as `engine=react`)
- SQLAlchemy persistence adapter for the graph business store (resume proof is the sqlite file)
- Real Docker worker process (socket out of API). In-process pattern recheck stays inconclusive
- Registering stdio MCP on the product tool router (transport is tested; InMemory transport remains the unit default)
- Mandatory OTEL exporter install (optional bridge)
- LLM-as-judge evals
- Human approval UI wiring

## Known residual risks

| ID | Issue | Severity |
|----|-------|----------|
| K1 | Old tasks with no `engine` key still run ReAct. New tasks default to graph | Low |
| K2 | docker.sock still on API compose | High (ADR-003; worker not deployed) |
| K4 | Shared cancellation is cooperative; an in-flight model call finishes before the next unit is stopped | Medium |
| K5 | Product checkpoints are a single-host file. Postgres is fail-closed | Medium |
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

The linked historical audit predates the product path. Current resume and event replay support workers sharing one host directory; distributed persistence and full product acceptance remain open.
