# Engine comparison benchmark

The instrument for the dual-path decision: does the LangGraph path detect as
well as the production ReAct path, and at what cost?

```bash
cd backend
uv run python -m tests.evals.benchmark.run_benchmark --engines graph,react --repeat 3
```

**Use `--repeat 3` or more.** Both engines are nondeterministic, and ReAct
badly so — see "Known limits".

Not part of the default test run — it needs a real model and spends tokens.
The scoring rules are unit-tested in `backend/tests/test_benchmark_scorer.py`.

## Corpus

`corpus/vulnerable/` carries planted flaws labelled in `labels.py` by file,
line and CWE. `corpus/safe/` is the direct counterpart of each one, written to
*look* risky — it still talks about SQL, subprocesses, passwords and webhooks —
so it tests discrimination rather than keyword avoidance.

**Negatives are not optional.** Without them only detection can be measured,
and an engine that flags everything would score perfectly.

## How results are counted

| Metric | Meaning |
|--------|---------|
| `recall` | Labelled flaws an engine reported |
| `FP(safe)` | Findings on the safe fixtures — the only unambiguous false positives |
| `unmatched` | Findings on vulnerable files matching no label — suspicious, not damning: planted code can contain incidental issues |
| `tokens`, seconds | Cost |

A label counts as found on same file, line within tolerance, and a compatible
class (CWE id, or a keyword match on the title since engines differ in how
reliably they emit CWEs).

## Preconditions, and why the runner can refuse to answer

ReAct depends on external scanners (Semgrep, Bandit, Gitleaks — all inside the
sandbox image) and on an embedding provider for RAG. Run it without those and
it is a weakened engine, so the runner checks and splits what it finds:

* **Blocking** — the sandbox image or Docker is missing, so the external
  scanners never ran. The run is labelled `DEGRADED` and the verdict is
  **withheld**. Restoring Semgrep moved ReAct from 8/14 to 10/14 on one run,
  so this is not a formality.
* **Advisory** — no embedding provider, so RAG code search is off. RAG helps
  the agent decide *where* to look in a large repository; on a corpus this
  small it can enumerate every file directly. The run still reports, with
  ReAct's recall flagged as a lower bound.

A benchmark that quietly reports a degraded baseline is worse than no
benchmark: it would "prove" whatever the newer engine happens to do.

## Known limits

- **Single-run results are noisy, and unevenly so.** Across identical runs of
  the same corpus:

  | engine | recall over 3 runs |
  |--------|--------------------|
  | graph | 14, 14, 14 — stable |
  | react | 8, 10, 8 — a spread of 2 labels |

  The graph path's false positives still moved (3–5), but its recall did not.
  ReAct's did. One run of ReAct is a coin toss, not a measurement — which is
  why `--repeat` exists and why the runner prints the spread.
- **The corpus tests single-file patterns.** That is the easy case, and it
  flatters a per-file analyser. `vulnerable/reports.py` plus
  `vulnerable/sanitize_util.py` is a first cross-file case (the sanitiser only
  strips single quotes), but the sink is still visibly concatenated, so it does
  not yet isolate multi-file reasoning. Deep dataflow cases are the next thing
  this corpus needs.
- **Detection only.** Verification quality (`verification_status`) is not
  scored; the graph path is Phase 1 and always reports `NOT_RUN`.
