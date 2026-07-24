# Verification subgraph (M7)

> Package: `backend/app/services/agent/graph/subgraphs/`  
> Status: implemented 2026-07-24

## Flow

```text
START → select_verification_strategy → verify_finding → update_confidence → END
```

## Strategies

| Strategy | Phase 1 default | Outcome |
|----------|-----------------|---------|
| `static_recheck` | yes (`allow_execution=False`) | `SKIPPED` / notes |
| `heuristic` | optional | `INCONCLUSIVE` |
| `sandbox` | only if `allow_execution=True` | CONFIRMED / INCONCLUSIVE / ERROR |
| `human` | optional | `PENDING` |

Phase 1 audit graph still emits findings with `verification_status=NOT_RUN`. This subgraph is opt-in for M7+ experiments.

## Tests

`tests/test_agent_verification_m7.py`
