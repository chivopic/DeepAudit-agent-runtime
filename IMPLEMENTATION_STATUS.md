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
# 2026-07-24 graph-audits JWT auth: **131 passed** (agent suite)
# 2026-09-11 re-verified unchanged: **131 passed**
# 2026-09-11 + real-LLM wiring suite: **149 passed** (agent gate)
```

## Engine comparison benchmark (2026-09-11)

Built to answer the dual-path question — *should the LangGraph path replace
ReAct, and when* — with numbers rather than impressions. The existing M10 evals
are smoke-grade (`expected_min_findings`, `expected_cwe_any`, `expected_paths`)
and cannot arbitrate between two engines.

Lives in [`backend/tests/evals/benchmark/`](backend/tests/evals/benchmark/README.md);
scoring rules are unit-tested in `backend/tests/test_benchmark_scorer.py` (15).

**Negatives are the point.** Half the corpus is safe code written to *look*
risky, because false-positive rate is unmeasurable without it and an engine
that flags everything would otherwise score perfectly.

### The runner can refuse to answer

ReAct needs an embedding provider (RAG) and the sandbox image (Semgrep, Bandit,
Gitleaks). Without them it is a crippled engine, so the runner checks
preconditions, marks the run `DEGRADED` and **withholds the verdict**. A
benchmark that quietly reports a degraded baseline is worse than no benchmark:
it would "prove" whatever the newer engine happens to do.

### Results after restoring ReAct's scanners

The first run had ReAct crippled — no RAG *and* no external scanners. Semgrep
and Bandit were restored (a stand-in sandbox image; the shipped Dockerfile
cannot build in this environment) and the benchmark re-run.

```text
graph   recall 14/14 (100.0%)  findings 21  FP(safe) 3  unmatched 4  tokens  6172   37.6s
react   recall  8/14 ( 57.1%)  findings 16  FP(safe) 0  unmatched 8  tokens 23588  115.5s
        ⓘ RAG still off — treat ReAct's recall as a lower bound
```

Restoring Semgrep moved ReAct from 8/14 to 10/14 on one run — and back to 8/14
on the next. Which is the headline finding:

| engine | recall across 3 identical runs |
|--------|-------------------------------|
| graph | 14, 14, 14 — stable |
| react | 8, 10, 8 — a spread of 2 labels |

**ReAct is erratic on this corpus; the graph path is not.** The graph path's
false positives still moved (3–5 across runs), but its recall did not. The
runner now takes `--repeat` and prints the spread, because a single ReAct run
is a coin toss rather than a measurement.

The graph path's false positives were all from the built-in heuristic pattern
scanner (`Possible SQL string` on parameterised queries) or from the model
flagging mitigations as findings — not from missing a real flaw. ReAct scored 0
false positives in every run: it is consistently the more conservative engine.

### This corrects an earlier assumption in this file

The dual-path notes assumed the graph path would be a clear regression because
it makes one truncated LLM call per file. On single-file patterns that is not
what the data shows: it detects more, more consistently, at roughly a quarter
of the tokens and a third of the wall time.

That is **not** a case for switching. This corpus is the easy case and flatters
a per-file analyser; it does not test the multi-file dataflow reasoning where
an iterative agent should win, and it does not score verification at all.

**Before any switch decision:** cross-file cases that actually isolate dataflow
reasoning, a ReAct run with RAG enabled, and `--repeat` on everything.

## K2 closed: docker.sock off the API (2026-09-11)

ADR-003 called the socket-on-API arrangement an unacceptable production blast
radius and specified the fix (#6: "API does not hold docker.sock; a dedicated
sandbox worker owns container lifecycle"). That worker now exists, and the
socket is gone from the API in all three compose files.

### Shape

| Piece | Role |
|-------|------|
| `app/sandbox_worker/` | The only code that touches docker.sock. Runs as its own container. |
| `contract.py` | The narrow wire contract. The client says *what* to run; it can never say *how*. |
| `sandbox_backend.py` | API-side client. `RemoteWorkerBackend` when `SANDBOX_WORKER_URL` is set, else the legacy in-process Docker client for local dev. |

`SandboxManager` keeps its public API, so all ~10 existing tool call sites are
untouched — only three lines in it ever touched Docker.

### What the boundary actually buys

A compromised API can spend sandbox capacity. It cannot:

- **choose the image, mounts, user, capabilities or privileges** — the request
  model has no such fields, and the worker builds the container from server
  settings alone;
- **mount an arbitrary host path** — `workdir` is resolved (symlinks included)
  and must stay inside `SANDBOX_WORKSPACE_ROOT`, so asking for `/etc` fails;
- **turn on networking** — `network` is an allowlist and anything but `none`
  needs `SANDBOX_ALLOW_NETWORK` (ADR-003 #8);
- **set loader or proxy environment variables** — `LD_*`, `PATH`, `PYTHONPATH`,
  `NODE_OPTIONS`, `*_PROXY` are refused by the contract.

The worker fails closed: no `SANDBOX_WORKER_TOKEN` configured means it refuses
to execute at all, and the API refuses to fall back to a local socket when the
worker is unreachable.

### Path translation

The worker asks the **host** daemon to mount the workspace, so the path has to
be valid on the host. `SANDBOX_WORKSPACE_ROOT` (`/var/lib/deepaudit/workspace`
in compose) is bind-mounted at the same path in both containers, and
`_get_project_root` now clones there instead of a container-private `/tmp`.

### Verified against a real Docker daemon

```text
api has docker client: False        (before and after initialize)
1. plain command in a container  -> ok, uid 1000
2. tool command, jailed workspace-> ok, reads /workspace
3. workdir "/etc"                -> refused: outside workspace root
4. workdir symlink -> /etc       -> refused: outside workspace root
5. network=bridge                -> refused: needs SANDBOX_ALLOW_NETWORK
6. timeout                       -> container killed
```

Compose resolved with `docker compose config`: `backend` has no socket and
`sandbox-worker` has it, on the internal network with no published ports.

Tests: `backend/tests/test_sandbox_worker.py` (28, in the CI gate).
Full backend suite: **1170 passed, 8 skipped, 0 failed**.

**Residual:** the in-process Docker client still exists in `SandboxManager` as
the local-development fallback, reachable only when `SANDBOX_WORKER_URL` is
unset. The shipped compose files always set it.

## Real-stack verification: two bugs fixtures could not catch (2026-09-11)

`source_type="project"` shipped with unit tests only. Running it for real —
Postgres, Alembic migrations, a seeded user and project, a JWT, a genuine clone
of this repository over HTTP — found two bugs that no fixture test could reach.

### K9 · GraphRecursionError on any repository over ~20 files

`AuditRunner.run` never set LangGraph's `recursion_limit`, so it used the stock
default of 25. Every `analyze_file` iteration is a super-step, so an audit died
with `GraphRecursionError` once it passed roughly twenty files — the entire run
failed, findings and all.

Invisible until now because fixtures hold a handful of files and test budgets
stop the loop first. The first real repository hit it immediately.

The limit is now derived from whichever budget cap actually bounds the loop
(`max_files` or `max_model_calls`), plus fixed overhead, floored at LangGraph's
25 and capped by `GRAPH_AUDITS_RECURSION_LIMIT_CAP` (default 2000). Erring high
is safe — the budget still stops the run; erring low kills it.

### K10 · `total_files` always 0 on the polling path

`GraphAuditFacade.get_task` initialised `total = 0` and never assigned it, while
`_result_to_task_dict` derived the count from the manifest. Start is async (202)
by default, so **every real client polls `get_task`** and saw `total_files: 0`
and `indexed_files: 0` regardless of repository size.

The file count is now persisted into `graph_snapshot` and read back on poll.

### Verified

Auditing this repository through the real HTTP surface:

```text
no-auth                -> 401
no project_id          -> 400  project_id is required for source_type="project"
host local_path "/etc" -> 400  client host local_path is rejected
start                  -> 202

FINAL STATUS: partial | files: 437 | findings: 79 | tokens: 31281
```

`partial` is correct here: the 25-model-call budget was exhausted. Findings are
real model output persisted through the business store, e.g. *"Potential path
traversal in zip extraction"* and *"First registered user is automatically
granted superuser"*.

Regression tests: `backend/tests/test_agent_realrepo_regressions.py` (8 tests,
in the CI gate). Full backend suite: **1142 passed, 8 skipped, 0 failed**.

## Usable graph path: dedupe, real repositories, frontend API (2026-09-11)

Follow-up to the LLM wiring below, closing the three gaps it left open.

### 1. Cross-analyzer de-duplication

One flaw was reported twice — once by the model, once by the pattern scanner —
because the fingerprint included the free-text title ("OS Command Injection in
ping()" vs "OS Command Injection").

`deduplicate_findings` now runs two passes: the existing exact-fingerprint pass,
then a semantic pass keyed on **file + line + CWE**. To make that key exist, the
analyze prompt asks the model for a `cwe` field, normalised through
`_normalize_cwe` ("cwe 78", `78`, "CWE-78" → `CWE-78`; anything without a number
is dropped rather than guessed).

Merging loses nothing: evidence from both analyzers is unioned, confidence and
severity take the stronger assessment, `analyzer` becomes `heuristic+llm`, and
`metadata.merged_from` records the contributors. Narrative fields come from the
model, which reads surrounding code; classification follows confidence.

Findings without a CWE are never merged — a wrong merge would silently delete a
real finding.

### 2. Real repositories, without trusting a client path

New `source_type="project"`:

- requires `project_id`, and the project ACL check is **mandatory** — passing
  `require=True` means `GRAPH_AUDITS_ENFORCE_PROJECT_ACL=false` cannot weaken it;
- the workspace is derived entirely from the stored Project record and
  materialised by the same `_get_project_root` the production ReAct path uses;
- the client still cannot name a path: `local_path` remains rejected, and the
  request carries only a synthetic `project://<id>` locator.

Cloning a repository can take minutes, so it runs in the graph's **ingest**
phase via a `workspace_resolver` closure rather than during the HTTP request.
Everything needing the request-scoped DB session is read eagerly while building
that closure. `_resolve_workspace` now treats any `scheme://` locator as "not a
directory", which also hardens the pre-existing fixture path.

`GRAPH_AUDITS_FIXTURE_ONLY` is unchanged and still governs the `local_path`
branch.

### 3. Frontend API layer + SSE

- `frontend/src/shared/api/graphAudits.ts`: typed client (start / get / cancel /
  resume / findings / events / stream URL), 9 tests.
- New backend endpoint `GET /api/v1/graph-audits/{id}/events/stream`. The event
  bus already emits AgentEvent-shaped frames (`graph_event_to_sse` maps the
  graph's `kind` onto the `type` vocabulary), so the existing frontend stream
  handling works unchanged.

**Deliberately NOT done:** the AgentAudit page is not switched over.
`src/pages/AgentAudit/index.tsx` depends on agent-tasks-only endpoints
(`getAgentTree`, `getAgentCheckpoints`, report export) that graph-audits does
not implement; an engine switch today would route users to a page missing those
panels. Needs graph-audits equivalents or a dedicated results view — a product
decision, not a code gap.

### Verified against a real model

Same DeepSeek fixture as below, after de-duplication:

```text
STATUS: completed | tokens_used: 1922
FINDINGS: 3          (was 6 for the same 3 planted flaws)
  heuristic+llm | critical | SQL Injection via string-formatted query   | line 6
  heuristic+llm | critical | OS Command Injection in ping()             | line 10
  heuristic+llm | critical | Insecure Deserialization via pickle.loads  | line 13
```

Agent gate: **183 passed**. Full backend suite: **1134 passed, 8 skipped, 0
failed**. Frontend: **340 passed**.

## Real LLM wiring for the graph path (2026-09-11)

The LangGraph path was hard-wired to `FakeLLM`, so it could run a graph but
could not actually audit anything. It now reaches the production LLM gateway —
**behind a default-off flag**.

| Change | Detail |
|--------|--------|
| `graph/llm_gateway.py` | New `LLMServiceGateway` implements the `LLMGateway` protocol over `app.services.llm.LLMService`. Credentials are never held here: `LLMService` resolves them per user from stored config, as the ReAct path does. |
| `GRAPH_AUDITS_USE_REAL_LLM` | New setting, **default `False`**. `_resolve_llm()` returns `FakeLLM` (offline) unless explicitly enabled, so this surface still cannot reach paid APIs by default. |
| Tolerant findings parsing | `analyze_file` only parsed findings when the reply started with `[`. Real models fence JSON in ```` ```json ````, so **every LLM finding was being silently dropped**. Now goes through the shared `AgentJsonParser.parse_any` (fence stripping + json-repair); also unwraps `{findings: [...]}`. |
| CI | `tests/test_agent_llm_gateway.py` (18 tests) added to the gate. |

### Verified against a real model (out of band, not a committed test)

DeepSeek `deepseek-chat`, small Python fixture with three planted flaws:

```text
STATUS: completed | tokens_used: 1329
FINDINGS: 6
  llm       | critical | SQL Injection via string-formatted query    | line 6
  llm       | critical | OS Command Injection in ping()              | line 10
  llm       | critical | Insecure Deserialization with pickle.loads  | line 13
  heuristic | high     | OS Command Injection                        | line 10
  heuristic | high     | Insecure Deserialization                    | line 13
  heuristic | medium   | Possible SQL string                         | line 6
```

Token accounting flows into `RunBudget`, and Phase 1 invariant 1 holds
(`verification_status=not_run` on every finding).

**Still open after this change:**

- `GRAPH_AUDITS_FIXTURE_ONLY` remains `True`, so the *route* still accepts only
  inline fixtures. Real-repository auditing over HTTP needs that trust boundary
  reopened deliberately — untouched here.
- Findings are not de-duplicated: the LLM and the heuristic tool report the same
  flaw twice (6 findings for 3 flaws above).
- Frontend still does not call `/api/v1/graph-audits/*`. Production remains ReAct.

## Legacy suite repair (2026-09-11)

The M0–M11 gate was green, but the **full** backend suite was not: 8 tests
failed. All 8 predated the agent-runtime work (reproduced on `main`), so they
were latent, not regressions. Now fixed:

```bash
cd backend && uv run pytest -q
# 2026-09-11: **1082 passed, 8 skipped, 0 failed**
# 2026-09-11 after real-LLM wiring: **1100 passed, 8 skipped, 0 failed**
```

| Fix | Detail |
|-----|--------|
| `BaseAgent._cancel_callback` | Initialiser had slipped into the body of `cancel()`, so the attribute only existed after `cancel()`/`set_cancel_callback()` had been called; any earlier `is_cancelled` read raised `AttributeError` and aborted the run. Moved to `__init__` beside `_cancelled`. |
| `tests/test_executor.py` | Dropped a stub patching `executor.get_agent_config`, a config fallback that no longer exists; the test now asserts the real contract (`default_timeout` defaults to 600). |
| `tests/test_event_manager_deep.py` | Dropped two stubs patching `event_manager.get_agent_config` (SSE heartbeat is now a hard-coded 30s). Sequence filtering is asserted via a terminal event; the queue fallback via a short `wait_for`. |

**Compatibility impact:** `base.py` is on the production ReAct path. Production
was masked from the bug because the only construction site
(`agent_tasks.py:455-459`) always calls `set_cancel_callback` before running;
the fix removes that ordering dependency. No signature or API change.

**Not addressed** (unchanged, still open): graph-audits is hard-wired to
`FakeLLM` (`graph_audits.py:232`) and defaults to `GRAPH_AUDITS_FIXTURE_ONLY`,
so the LangGraph path cannot audit a real repository; the frontend does not
call `/api/v1/graph-audits/*` at all. Production remains ReAct (K1). Repo-wide
Ruff/Black/MyPy remain unclean on legacy code and are not gated by CI.

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

**Still open (product / next work):** true mid-graph resume, multi-worker cancel/events, Postgres business store adapter.

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
| K2 | ~~docker.sock on API compose~~ | **Fixed** 2026-09-11 (sandbox worker) |
| K4 | Cancel mid-flight is cooperative/in-process | Medium |
| K5 | Memory checkpointer default | Medium (dev) |
| K8 | CI gate added; comparison benchmark added (needs full-strength ReAct to arbitrate) | Low |
| K9 | ~~GraphRecursionError over ~20 files~~ | **Fixed** 2026-09-11 |
| K10 | ~~total_files always 0 when polling~~ | **Fixed** 2026-09-11 |

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
