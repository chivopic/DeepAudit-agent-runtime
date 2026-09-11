# Engine comparison benchmark

The instrument for the dual-path decision: does the LangGraph path detect as
well as the production ReAct path, and at what cost?

```bash
cd backend
uv run python -m tests.evals.benchmark.run_benchmark --engines graph,react
```

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

ReAct depends on an embedding provider (RAG) and on the sandbox image
(Semgrep, Bandit, Gitleaks). Run it without those and it is a crippled engine —
so the runner checks for them, labels the run `DEGRADED`, and **withholds the
verdict** rather than printing numbers that would "prove" the new engine wins.

A benchmark that quietly reports a degraded baseline is worse than no
benchmark. If you see `VERDICT WITHHELD`, fix the preconditions and re-run.

## Known limits

- **Single-run results are noisy.** Model nondeterminism moved the graph
  engine's false positives between 2 and 5 across two runs of an identical
  corpus. Treat one run as a smoke reading, not a measurement; repeat before
  concluding anything.
- **The corpus tests single-file patterns.** That is the easy case, and it
  flatters a per-file analyser. `vulnerable/reports.py` plus
  `vulnerable/sanitize_util.py` is a first cross-file case (the sanitiser only
  strips single quotes), but the sink is still visibly concatenated, so it does
  not yet isolate multi-file reasoning. Deep dataflow cases are the next thing
  this corpus needs.
- **Detection only.** Verification quality (`verification_status`) is not
  scored; the graph path is Phase 1 and always reports `NOT_RUN`.
