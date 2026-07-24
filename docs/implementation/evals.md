# Evals + CI (M10)

> Package: `backend/tests/evals/`  
> Status: implemented 2026-07-24

## Layout

```text
backend/tests/evals/
├── fixtures/          # vulnerable + safe cases (in-memory file maps)
├── evaluators/        # schema, location, CWE, verification policy, count
└── runner.py          # run_case / run_suite / CLI (--ci)
```

## Principles

- Deterministic: FakeLLM + graph heuristics; no paid models, no network.
- Phase 1 findings must keep `verification_status=NOT_RUN`.
- CI set (`CI_CASES`): one vulnerable + one safe fixture.

## CI

GitHub Actions workflow `.github/workflows/agent-pytest.yml` runs M1–M11 unit tests + eval CI suite on PRs/pushes affecting `backend/`.

## Tests

`tests/test_agent_evals_m10.py`
