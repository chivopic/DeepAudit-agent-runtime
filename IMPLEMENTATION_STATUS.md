# IMPLEMENTATION_STATUS

> Living status file for the DeepAudit agent runtime refactor (M0–M11).  
> Update at the end of every milestone.

## Current milestone

**R2–R5 single-host product path + main integration** · status: **INTEGRATED; CI AND PRODUCT ACCEPTANCE PENDING** (2026-10-08)

New Agent audits go through the existing `/api/v1/agent-tasks` API. The server default is `engine=graph` (`AGENT_RUNTIME_ENGINE`). A client can still send `engine=react` and run the classic ReAct path. Old rows with no `agent_config.engine` stay on ReAct. `/api/v1/graph-audits` defaults to `FakeLLM`, also supports server-resolved projects with mandatory ACL checks, and can opt into the real gateway via `GRAPH_AUDITS_USE_REAL_LLM`.

M0–M11 stay recorded below as the experimental runtime. R1 stays the trustworthiness baseline. R2–R5 are the product path in the existing create dialog. The merged main also supplies a generic Postgres checkpointer and a separate sandbox worker. Product graph recovery still uses a single-host file store, and its verification checkbox performs a static recheck.

| ID | Title | Status |
|----|-------|--------|
| R1 | Execution trustworthiness | Local regressions pass; product acceptance remains pending. |
| R2 | Gateway, authorized snapshot, tools, source windows | Bounded source artifacts, explicit omissions and glob exclusions. RAG is not wired. |
| R3 | Durable resume on one host | File checkpoint + SQLite + pinned source artifacts. OS run lock, shared cancellation and event replay. Other product persistence backends fail closed. |
| R4 | `/api/v1/agent-tasks` on the graph runtime | Create starts the run; paused/failed tasks resume the saved source. Authenticated browser and database acceptance is pending. |
| R5 | Serial analysis, static recheck, experimental MCP | Serial execution; static recheck remains INCONCLUSIVE. The sandbox worker exists for legacy sandbox calls; product graph Docker verification, true concurrency and product MCP remain pending. |

## Main integration (2026-10-08)

The merge combines `c3255ab` (main) and `e809a6e` (product repairs and CLI).

- Generic runner checkpoints retain `AGENT_CHECKPOINT_BACKEND=auto|postgres|memory` and the async Postgres factory. Product recovery uses **`AGENT_PRODUCT_CHECKPOINT_BACKEND=file`**, `AGENT_CONTROL_BACKEND=file`, and the shared `AGENT_STATE_DIR`. The separate setting avoids overriding either path's backend policy.
- Run/resume share result persistence; streaming progress, cancellation hooks, pinned product source and failure salvage are retained. Both existing verification node names remain available for saved checkpoints.
- Local import context, CWE deduplication, defence-note filtering and severity thresholds from main are combined with source windows, model-error accounting and coverage reporting. Snapshot-backed analysis never reads omitted imports from a live checkout.
- All three Compose files keep the sandbox worker's Docker socket and add the product runtime volume to the API. CI retains the full backend suite, smoke audit and evals.
- The test figures below describe the feature branch before this merge. Tests have not been rerun locally for the merge; its CI result is pending.
- Merge checks passed: Python syntax for 73 changed files, Ruff for six integrated core modules, endpoint undefined-name checks, Black for eight touched modules, MyPy for runner/builder/routing/product assembly, frontend TypeScript, and Compose/YAML structure. Legacy API lint findings remain outside this merge. Full MyPy checking of the Postgres adapter is limited by missing local `langgraph-checkpoint-postgres` / `psycopg_pool` packages.

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
- Product assembly reads its own configured backends and accepts `file` only. Product Postgres recovery, distributed scheduling, RAG, production MCP and graph Docker verification remain outstanding; main's generic Postgres backend and sandbox worker are retained.

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
# 2026-09-11 re-verified unchanged: **131 passed**
# 2026-09-11 + real-LLM wiring suite: **149 passed** (agent gate)
```

## Checkpointing made real: Postgres backend and true resume (2026-09-11)

`MemorySaver` is a dict. It was the default in the runner's constructor, so
every milestone since M3 ran with checkpoints that died with the process — and
`resume()` only read finished state back rather than continuing, which its own
comment admitted (*"M3: full re-invoke; M4+ may continue mid-graph"*).

### What changed

| Piece | Detail |
|-------|--------|
| Postgres backend | `AsyncPostgresSaver` over a psycopg pool, tables created on first use. The backend now comes from `AGENT_CHECKPOINT_BACKEND` instead of being pinned to `"memory"` in the constructor. |
| DSN handling | The app speaks SQLAlchemy (`postgresql+asyncpg://`); psycopg cannot parse that, so the dialect prefix is stripped and non-Postgres URLs are refused rather than mangled. |
| `resume` | When the checkpoint has un-run nodes, `ainvoke(None, cfg)` continues from it; a resumed run is persisted through the same `_persist` path as a fresh one, so the recovery path cannot drift from the normal one. |

### Failing loudly vs. degrading quietly

`postgres` is explicit and **fails hard** if the database is unreachable:
asking for durability and silently getting a dict is how a crash turns into
lost work.

`auto` is best-effort — it falls back to memory — but logs a WARNING saying
checkpoints will not survive a restart. Connect timeouts are 5s, not the
driver's 30s, and an unreachable DSN is remembered per-process so the next
runner fails over immediately instead of paying the timeout again. That last
detail is not cosmetic: without it the test suite went from 16s to 5m20s.

### Verified

```text
cross-process   a child process writes checkpoints, is killed, and this
                process reads them back — impossible with MemorySaver
resume          interrupted at aggregate_findings (next=('aggregate_findings',),
                0 findings) → runner.resume() → COMPLETED, 5 findings
```

**A note on method.** The first attempts at the resume test killed a child
process to create an interrupted checkpoint, and produced nonsense: `kill -9`
on the `uv run` wrapper orphaned the real Python process, which kept running
and finished the audit. Three readings that looked like "resume does not fire"
were actually reads of completed threads. The test now creates the interrupted
state deterministically with `interrupt_before` — an unreliable setup makes an
unreliable test, whatever it appears to prove.

## Process: the repeated defect, and three guards against it (2026-09-11)

Four features in this repository were built, tested, marked **Done**, and never
connected to anything:

| Feature | Symptom |
|---------|---------|
| `FakeLLM` → `ModelRouter` | the graph path could not reach a real model at all |
| `severity_threshold` | the API accepted the parameter and discarded it |
| M7 verification subgraph | every finding stayed `NOT_RUN` whatever was requested |
| **M11 Agent Harness** | still unreferenced by production code today |

Unit tests cannot catch this. They import the thing they test, so the thing is
always reachable from where they stand. Green tests were precisely what made
the gap invisible.

### 1. CI runs the whole suite

It ran **295 of 1254** tests — a hand-maintained list of filenames. The eight
failures found at the start of this work lived on `main` because the list did
not name the files holding them, and every new test file had to be remembered
into CI by hand.

Now `pytest`, plus the smoke check below, plus the eval suite.

### 2. A reachability test, run from production's side

`backend/tests/test_production_wiring.py` asserts every package under
`app/services/agent/` is imported by code outside its own package and outside
`tests/`. Exceptions live in `KNOWN_UNWIRED` and need a written reason, so
adding one is a deliberate act rather than an omission.

It currently reports one: **`harness`**. `AgentRuntime`, `ModelRouter`,
`PermissionPolicy` and `BudgetManager` are referenced only by tests.
`graph_audits` reaches the real model through `LLMServiceGateway` instead, so
`ModelRouter` — the harness's intended wiring point — is dead. **Connect it or
drop it**; leaving it is what this test exists to make visible.

### 3. A smoke script that actually runs an audit

`backend/scripts/smoke_audit.py`. Every real bug in this codebase was found by
running it, not by the suite:

| Bug | Why the tests missed it |
|-----|-------------------------|
| `GraphRecursionError` over ~20 files | fixtures hold a handful |
| `total_files` always 0 | tests used the sync path, clients poll |
| every LLM finding dropped | `FakeLLM` emits no ```` ```json ```` fences |
| the SSE stream never closed | nothing had ever subscribed |

So it audits **30 files** (deliberately past LangGraph's stock recursion limit
of 25), checks the results survive persistence, and checks the event stream
terminates. `--real-llm` runs it against the configured model.

## Verification: M7 was unreachable, and measuring it exposed a scoring bug (2026-09-11)

### The subgraph was never called

M7 shipped a verification subgraph with six tests and was marked **Done**. It
was imported by exactly one file: its own test. `enable_verification` was a
field no code read, and the route hardcoded it to `False`, so every finding
came back `NOT_RUN` no matter what the request asked for.

Same shape as the `FakeLLM` wiring and `severity_threshold`: built, tested,
never connected.

It now runs as a node between `prioritize_findings` and `generate_report`, and
the behaviour ladder is enforced rather than assumed:

| Request | Runtime | Status |
|---------|---------|--------|
| `enable_verification=False` | any | `not_run` — Phase 1 invariant 1 preserved |
| `enable_verification=True` | offline | `skipped` — ADR-003: no untrusted execution on the default path |
| `enable_verification=True` | online | execution attempted |

A failing verifier costs the verification, not the audit.

### Measuring it caught a bug in the benchmark, not the engine

The first `--verify` run reported ReAct as `not_run=14  (nothing verified)`.
That was **a measurement artifact**. ReAct has no `verification_status` field
at all — it uses `is_verified` / `needs_verification` / `verdict` — and the
scorer defaulted a missing key to `not_run`, which reads as "this engine never
verifies". The scorer now normalises both vocabularies and says `unreported`
when neither is present.

Worth recording as a method note: a benchmark that assumes one engine's
vocabulary will quietly score the other engine wrong.

### With that fixed, verification is the sharpest difference between them

```text
graph   recall 16/17  findings 22  FP 0  unmatched 6   verification: inconclusive=22
react   recall  6/17  findings  6  FP 0  unmatched 0   verification: confirmed=6
```

**ReAct's sandbox verification works as a filter.** It cut its own output to
six findings and confirmed every one — nothing spurious, nothing unmatched.
The graph path runs verification but confirms nothing: its executor cannot
actually exploit anything, so everything lands `inconclusive`.

For a security product, "six confirmed exploitable issues" is a different
product from "twenty-two things that might be issues". This is the strongest
argument yet that the two paths are complementary rather than competing.

**Caveat:** the corpus is snippets, so both numbers are floors — a function
with no entrypoint cannot be exploited by either engine. ReAct confirming six
of them at all is the notable part.

## Cross-file context: the graph path now reads the guards it relies on (2026-09-11)

The benchmark had established that the graph path does not reason across
files — its "cross-file" hits were speculation, and tightening the prompt for
exploitability made them disappear. The cause was simple: `analyze_file` sent
one file's text and nothing else, so a guard defined elsewhere could not be
judged. The call looked defended, and the model had to guess.

It now resolves the **local** modules a file imports and includes their source.

| Piece | Detail |
|-------|--------|
| `_local_imports` | Python relative and absolute, JS/TS `import` and `require`. Local only — `validators.py` is worth reading, `os` and `requests` are not. A file never imports itself. |
| `_load_import_context` | Reads through the **same jail** as the file under analysis, so a crafted import string cannot become a path traversal. Works from fixtures as well as from disk. |
| Budget | 3 modules, 1500 chars each. The point is to judge a guard, not to paste the repository into every prompt. |

### The reasoning changed, not just the score

Before, a cross-file hit read `Potential path traversal in attachment
download` — a pattern, hedged. Now:

```text
Path traversal via incomplete '..' check in is_safe_path
SSRF via incomplete host blocklist in link preview fetcher
Autoescaping disabled by LEGACY_TEMPLATE_MODE, enabling SSTI
```

It names the helper, and says *why* the guard fails — an incomplete check, a
deny-list where an allowlist was needed, a constant set in another file.

### Measured, three runs

| | before context | after context |
|---|---|---|
| cross-file | 1/3 | **3/3, 3/3, 2/3** |
| recall | 16.3 / 17 mean | **16.7 / 17 mean** |
| false positives on safe code | 0 | **0 — no new noise** |
| tokens | ~11k | ~14k (**+30%, the honest cost**) |
| wall time | ~52s | ~53s |

The autoescape case is still the flaky one (2/3): it needs the model to connect
a boolean constant to escaping behaviour, which is the longest inferential hop
in the corpus.

## Finding quality: defences are not defects (2026-09-11)

The benchmark's negative fixtures exposed what was actually wrong with the
graph path's output: it reported the model's *descriptions of defences* as
findings — `SSRF mitigated by strict host allowlist`, `Path traversal mitigated
via resolve_within helper`. A report full of non-issues is worse than a short
one; it teaches people to skim.

Three causes, three fixes:

| Cause | Fix |
|-------|-----|
| The prompt never said what counts as a finding — it just asked for "findings" | Asks for exploitable defects only, and says explicitly not to report defended code, mitigations or general observations; return `[]` when there is nothing |
| Nothing filtered the model's output | `_is_defence_note` drops phrases like "mitigated by" / "not a vulnerability". Conservative: anything hinting the defence is *incomplete* ("bypass", "insufficient", "however", "可绕过") is kept, because dropping a real finding costs far more |
| The `SELECT * FROM` heuristic fired on every parameterised query | `_line_builds_a_string` requires interpolation or concatenation on the line — a bare SELECT is just SQL |

### `severity_threshold` was accepted and ignored

Unrelated find while fixing the above: `AuditRequest.severity_threshold` existed
in the domain model and appeared **nowhere else in the codebase**. The API took
the parameter and silently discarded it. It is now applied in
`aggregate_findings`, and the drop count is reported in the node event rather
than being invisible.

### Measured, three runs each

```text
before   FP(safe) 2–7 (varying)   findings 28–31
after    FP(safe) 0, 0, 0         findings 20–21
```

**False positives eliminated**, and a third of the noise with them.

The cost is honest and worth recording: cross-file recall on the autoescape
case fell from 3/3 to 1/3. That is itself evidence — the graph path's
"cross-file" hits were low-confidence speculation, and once the prompt demands
exploitability it stops speculating. It does not reason across files; it
guessed, and guessing scored.

Remaining `unmatched` findings (~4) are on vulnerable files and not
automatically wrong — planted code contains incidental issues.

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

### Cross-file cases: designing them is where the work is

`corpus/*/xfile/` adds three cases where the dangerous call sits behind a guard
that *looks* protective, and only the helper in another file shows it is not.

**The first attempt did not measure what it claimed.** The broken helpers were
self-evidently broken (`return True  # TODO`, `AUTOESCAPE = False`), so an
engine could score a hit by reading the helper alone. `--show-xfile` — which
prints *where* each hit was reported — exposed it: the autoescape "cross-file"
hit was reported on the settings file itself.

Rewritten so each helper reads as competent code in isolation: the SSRF policy
blocks cloud metadata endpoints (thoughtful, but a deny-list, so still
bypassable), and the template flag is `LEGACY_TEMPLATE_MODE = True`, which says
nothing about escaping on its own.

### With that fixed, the two engines separate cleanly

| | `graph` | `react` |
|---|---|---|
| recall | 17/17, stable | ~9/17, varies |
| cross-file | 3/3 — one still reported on the helper | 2/3, with mechanism named |
| false positives on safe code | 2–7, varies | **0 in every run** |
| tokens / wall time | ~10k / ~60s | ~17–21k / ~175s |

The texture matters more than the totals. ReAct's cross-file findings read
*"SSRF — bypassable host_allowed policy"* and *"SSTI — user-controlled template
source plus autoescaping off"*: it connected the files, and the second call is
arguably better than the label it was scored against. Yet it misses obvious
single-file patterns (`os.system` with concatenation, `innerHTML`) that the
graph path never misses.

**They fail differently.** A broad cheap net versus a narrow deep reader. That
is an argument for routing work to the right engine, not for replacing one with
the other — and this corpus is still far too small to settle it.

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

**Still open after R2–R5:** product multi-host Postgres resume, a SQLAlchemy graph business store, connecting graph verification to the existing sandbox worker, RAG, and registering stdio MCP on the product tool router. Single-host file resume is covered in the R2–R5 section.

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
| `/api/v1/graph-audits/*` | **Experimental graph API**. Defaults to `FakeLLM`; supports authorized server-resolved projects and an opt-in real gateway. Rejects client host paths. |

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
| K2 | ~~docker.sock on API compose~~ | **Fixed** 2026-09-11 (dedicated sandbox worker retained in merge) |
| K4 | Product cancellation is shared and cooperative; an in-flight model call finishes before the next unit stops | Medium |
| K5 | Product checkpoints remain single-host files; the generic runner also supports Postgres | Medium (product Postgres migration pending) |
| K8 | CI runs the full backend suite + smoke + evals, including product regressions | Configured; merged CI result pending |
| K11 | Product assembly wires the M11 harness, ModelRouter and governance hooks | Integrated 2026-10-08 |
| K9 | ~~GraphRecursionError over ~20 files~~ | **Fixed**; budget-derived bound retained |
| K10 | ~~total_files always 0 when polling~~ | **Fixed**; persisted file count retained |

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
