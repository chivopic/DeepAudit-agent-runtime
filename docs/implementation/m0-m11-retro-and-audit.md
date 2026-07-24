# M0–M11 Retrospective & Code Quality Audit

> Date: 2026-07-24  
> Scope: DeepAudit agent runtime refactor (`backend/app/services/agent/*`)  
> Tests at audit time: **121 passed** (M1–M11 unit) after residual hardening

---

## Part A — Process retrospective

### What we set out to do

Replace an implicit multi-agent ReAct stack with a **durable, protocol-driven** runtime:

| Pillar | Decision |
|--------|----------|
| Orchestration | LangGraph only for state / checkpoint / interrupt |
| Domain | Pydantic for models, API, tools, sandbox I/O |
| Persistence | Checkpoint ≠ business DB ≠ Artifact Store |
| Security | Phase 1 = no untrusted exec; `verification_status=NOT_RUN` |
| Migration | Additive dual-path; freeze `/api/v1/agent-tasks/*` |

### What worked

1. **Milestone discipline (M0 first)** — ADRs + target architecture before code prevented a second package root and random refactors.
2. **Additive dual-path** — production ReAct untouched; LangGraph under `/graph-audits` + application façade.
3. **Protocol seams** — LLM, sandbox, tools, store, tracer are all replaceable in tests (FakeLLM, NullSandbox, Mock tools).
4. **Deterministic tests** — FakeLLM + fixture file maps; no paid models / network in default suite.
5. **Security defaults encoded in domain** — SourceLocation rejects absolute/`..`; sandbox allowlist; tool denylist; Finding defaults `NOT_RUN`.
6. **Docs as completion gate** — each milestone updated `IMPLEMENTATION_STATUS.md` + `docs/implementation/*`.

### What hurt / friction

1. **Context compaction mid-stream** — long sessions lost mid-implementation detail; recovered via status file + package map.
2. **LangGraph config injection** — nodes often did not see `configurable.runtime`; fixed with `contextvar` (`set_runtime` / `get_runtime`).
3. **Monolithic module files** — M8/M9/M11 landed as large `__init__.py` packages (workable but harder to review).
4. **Harness / tooling not wired into graph nodes yet** — ~~architecture present only~~ **resolved 2026-07-24** (tools/tracer/budget mid-run).
5. **Experimental HTTP surface** — ~~no auth~~ **JWT + owner ACL landed 2026-07-24** (optional project ACL flag).

### Lessons

- Write **protocol + fake first**, then adapter.
- Prefer **fail closed** (NullSandbox, denylist, FakeLLM-only routes).
- Keep **one status file** as the recovery anchor after context loss.
- Do not mark a milestone done until **tests + docs + status** land together.
- Dual-path is the right migration; full cutover is a separate product decision.

### Suggested next engineering steps (post-M11)

1. Wire `ToolRegistry` + `Tracer` into analyze nodes (real value from M8/M9).
2. Authz on `/graph-audits` (match agent-tasks).
3. Postgres checkpointer + SQLAlchemy business store adapters.
4. Sandbox worker process; remove docker.sock from API compose.
5. Split large packages into modules (`tooling/registry.py`, `harness/runtime.py`, …).

---

## Part B — Code quality audit

Severity key: **C** critical · **H** high · **M** medium · **L** low

### Strengths

- Clear package map aligned with ADRs (domain / graph / persistence / application / context / sandbox / tooling / observability / harness).
- Pydantic validation at domain boundaries (paths, budgets, tool names).
- Phase-1 security story is coherent: NullSandbox, no raw shell, verification skip, secret redaction keys.
- Good test density relative to new surface (121 focused tests after hardening).
- Audit logs for tools omit payloads (arg keys only).

### Findings

#### C1 — Unauthenticated experimental API (still open)

| | |
|--|--|
| **Where** | [`graph_audits.py`](backend/app/api/v1/endpoints/graph_audits.py) |
| **Issue** | Routes have no `get_current_user` / project ACL; anyone who can reach the API can start graph audits and read in-memory results. |
| **Why** | Production agent-tasks are auth-gated; dual-path is not. |
| **Status** | **Open** — requires product auth wiring; not silently “fixed” without FE contract. |
| **Fix** | Add same Depends auth as agent-tasks; reject unauthenticated in non-test profiles. |

#### C2 — Tool path validation incomplete → **fixed**

| | |
|--|--|
| **Where** | `tooling/__init__.py` `_read_snippet` / `_heuristic_scan` |
| **Issue** | Rejected `..` and leading `/` only; Windows drive letters (`C:`) and backslash forms slipped through. |
| **Fix applied** | Normalize `\\` → `/`, reject drive letters, empty paths; same pattern on heuristic_scan. |

#### H1 — Sandbox local path checks weak → **fixed**

| | |
|--|--|
| **Where** | `sandbox/__init__.py` `read_file_snippet` |
| **Issue** | Lookup was “path in dict only”; no explicit unsafe-path deny for when real FS is later plugged in. |
| **Fix applied** | Same path guard as tooling before file map lookup. |

#### H2 — PermissionPolicy auto-mirrored AgentSpec → **fixed**

| | |
|--|--|
| **Where** | `harness/__init__.py` |
| **Issue** | Default `PermissionPolicy(allow_execution=spec.allow_execution)` meant setting the spec flag alone opened the gate. |
| **Fix applied** | Default policy stays `allow_execution=False`; execution requires explicit policy injection. |

#### H3 — Harness budget / tools unused during run (**open**)

| | |
|--|--|
| **Where** | `AgentRuntime.start` |
| **Issue** | `BudgetManager` only checked pre-start; never `note_model_call` / `note_tool_call` during graph. `ToolRegistry` materialised but not injected into nodes. |
| **Why** | False sense of governance; budgets are decorative. |
| **Fix** | Pass registry + budget into `GraphRuntime.extra` and consume in nodes; fail on exhaust mid-run. |

#### H4 — Cancel was a no-op mid-run → **fixed** (still in-process)

| | |
|--|--|
| **Where** | `AuditRunner` / `GraphRuntime` / `analyze_file` / routing |
| **Issue** | Cancel flag only checked pre-start; nodes never set `cancelled`; route sent cancel to `aggregate_findings` → COMPLETED report. |
| **Fix applied** | `GraphRuntime.cancel_check` + `is_cancelled()`; runner wires flag; analyze/generate_report poll; `finalize_cancelled` terminal node; runner forces `CANCELLED` status. Multi-worker cancel remains open (K4). |

#### H5 — Resume re-runs from scratch (**open**)

| | |
|--|--|
| **Where** | `runner.py` `resume` |
| **Issue** | Incomplete runs call full `run()` again if checkpointer miss — not true mid-graph resume. |
| **Fix** | Document as re-drive; implement `update_state` / continue when checkpointer present. |

#### M1 — ToolRegistry routing is brittle (**open**)

| | |
|--|--|
| **Where** | `ToolRegistry.invoke` |
| **Issue** | Dual-pass adapter scan with string-prefix heuristics for “unknown”; MCP empty `list_tools()` forces try-all. |
| **Fix** | Explicit `supports(name) -> bool` on protocol; drop string matching. |

#### M2 — Global tracer mutable singleton (**open**)

| | |
|--|--|
| **Where** | `observability.get_tracer` / `set_tracer` |
| **Issue** | Process-global; concurrent audits interleave spans / races under ASGI workers. |
| **Fix** | ContextVar-bound tracer (same pattern as GraphRuntime). |

#### M3 — Event bus process-local (**open**)

| | |
|--|--|
| **Where** | `GraphEventBus` |
| **Issue** | History/subscribers not multi-worker safe; fine for tests, wrong for multi-replica. |
| **Fix** | Redis/pub-sub or stick to single-worker with documented limit. |

#### M4 — graph-audits always FakeLLM → **documented + forced offline**

| | |
|--|--|
| **Where** | `graph_audits.py` |
| **Issue** | `FakeLLM if offline else FakeLLM` was dead code; looked like online path. |
| **Fix applied** | Force `offline=True` + FakeLLM; comment until ModelRouter wired. |

#### M5 — Eval status check was vacuous → **fixed**

| | |
|--|--|
| **Where** | `tests/evals/evaluators` |
| **Issue** | `or st == ""` / always-true branches weakened CI. |
| **Fix applied** | Require non-empty status ∈ {completed, failed, cancelled, partial}. |

#### M6 — Redundant redact loop → **fixed**

| | |
|--|--|
| **Where** | `redact_value` |
| **Issue** | Double loop with dead `group in dir(pat)` branch. |
| **Fix applied** | Single `sub("***")` pass. |

#### M7 — Monolithic packages (**open**, style)

| | |
|--|--|
| **Where** | `tooling/__init__.py` (~500 LOC), harness, observability |
| **Issue** | Harder review / circular import risk as nodes import. |
| **Fix** | Split adapters / registry / types. |

#### L1 — BudgetManager.from_spec was instance method → **fixed** (now `@staticmethod`)

#### L2 — Histogram aliases counter (**open**) — fine for Phase 1; document or store kind.

#### L3 — `normalized_findings` has no append reducer (**open**) — OK if single writer; document if parallelize.

#### L4 — Heuristic CWE patterns are demo-grade (**open**) — expected for skeleton; do not market as production detector.

#### H6 — analyze_file opened paths without jail → **fixed**

| | |
|--|--|
| **Where** | `graph/nodes` `_safe_read_under_root` |
| **Issue** | `root / task.target_path` with no `resolve().is_relative_to` — local_path + crafted target could read host files. |
| **Fix applied** | Jail helper rejects `..`, abs, drive letters; requires resolved path under workspace root. |

#### H7 — FilesystemArtifactStore path escape → **fixed**

| | |
|--|--|
| **Where** | `persistence/artifact_store.py` |
| **Issue** | Unsanitized `audit_id` subdir; `get_bytes` trusted `ref.uri` arbitrarily. |
| **Fix applied** | Sanitize audit segment; resolve + `is_relative_to(root)` on put/get. |

#### M8 — NullSandbox reported SUCCEEDED on skip → **fixed**

| | |
|--|--|
| **Where** | `NullSandboxExecutor` + `ExecutionStatus.SKIPPED` |
| **Issue** | Skipped actions looked like successful verification signals. |
| **Fix applied** | New enum value `SKIPPED`; null executor returns it. |

### Residual risks (product)

| ID | Risk | Sev |
|----|------|-----|
| K1 | Production still ReAct default | Medium (intentional) |
| K2 | docker.sock on API compose | High (ops; ADR-003) |
| K4 | Cancel still in-process (not multi-worker) | Medium |
| K5 | Memory checkpointer default | Medium (dev) |
| **A1** | graph-audits unauthenticated | **High** until auth |
| **A2** | Harness policies not enforced in nodes | High for “governance” claims |
| **A3** | Resume is re-drive, not mid-graph | Medium |

### Audit fixes already landed this session

1. Tool path drive-letter / backslash guards  
2. Sandbox snippet path guards  
3. PermissionPolicy default closed  
4. `BudgetManager.from_spec` staticmethod  
5. Redact loop cleanup  
6. Eval status_terminal tightened  
7. graph-audits forced offline FakeLLM + comment  
8. Cooperative cancel → `finalize_cancelled` + CANCELLED status  
9. Workspace file-read path jail in analyze_file  
10. Artifact store root jail + audit_id sanitize  
11. NullSandbox `SKIPPED` (not SUCCEEDED)  

### What we did *not* change (intentionally)

- ~~Auth on `/graph-audits`~~ JWT + per-audit owner (optional project ACL flag)  
- ~~Full wiring of tools/tracer into graph nodes~~ mid-run governance landed  
- True multi-worker cancel / durable event bus  
- Postgres checkpointer production cutover  

### Explore-agent residual (M1–M7) merged 2026-07-24

Confirmed Critical/High themes: unauth graph-audits + local_path LFI surface; cancel no-op; FakeLLM defaults; path jail; artifact URI trust. Items 8–11 above close the code-path Criticals that do not need product FE changes; **A1** remains product-gated.
- Production ReAct code paths  

---

## Part C — Quality scorecard (subjective)

| Area | Score | Notes |
|------|-------|-------|
| Architecture fit | 9/10 | Matches ADRs; dual-path correct |
| Security defaults | 8/10 | JWT + owner on graph-audits; fixture-only; harness mid-run |
| Testability | 9/10 | Deterministic suite solid |
| Production readiness of LangGraph path | 4/10 | Experimental; FakeLLM; in-memory stores |
| Code maintainability | 6/10 | Large modules; some brittle registry routing |
| Docs / process | 9/10 | Status + implementation docs complete |

**Bottom line:** The M0–M11 stack is a **sound skeleton** with the right boundaries and a strong deterministic test harness. It is **not** yet a drop-in production replacement for ReAct. Highest-value remaining: **true mid-graph resume**, **multi-worker cancel/events**, **Postgres adapters**.

---

## Part D — Codex re-audit remediations (2026-07-24)

Codex second-pass findings implemented as **Phase 0/1**:

| Codex theme | Fix |
|-------------|-----|
| C1 unauth + client `local_path` | `GRAPH_AUDITS_ENABLED` / `FIXTURE_ONLY`; reject absolute host paths; require `fixture_files`; size caps |
| Sync start blocks cancel | `start_async` + HTTP 202; optional `wait` for tests |
| Budget → fake COMPLETED | `AuditStatus.PARTIAL`; plan LLM `consume_model_call`; `generate_report` terminal PARTIAL |
| MCP empty list still invoke | discovery cache + allowlist; registry routes only known tools |
| verification `SUCCEEDED` on skip | default + static/human/not_run paths use `ExecutionStatus.SKIPPED` |
| Mapper field loss | `vulnerability_type` / `ai_confidence` / `is_verified` in from/to legacy |

**Tests:** 128 → 129 (node wiring) → **131** (JWT auth) on agent suite M1–M11.

Post Phase 0/1 same-day: tools/tracer/budget node wiring; JWT + owner ACL on `/graph-audits` (optional `GRAPH_AUDITS_ENFORCE_PROJECT_ACL`).

Remaining product-gated: true mid-graph resume, multi-worker events, Postgres business/checkpointer adapters.
