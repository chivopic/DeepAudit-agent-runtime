# Domain models (M1)

> Package: `backend/app/services/agent/domain/`  
> Status: implemented 2026-07-24

## Purpose

Pydantic v2 models are the **single source of truth** for:

- LangGraph node inputs/outputs (embedded in `AuditState` from M2)
- Structured LLM responses (planner, analyzer, reporter)
- Tool I/O schemas (later milestones)
- API mapping (adapters only — freeze existing `/api/v1/agent-tasks/*` shapes)

ORM models stay under `app.models`. Map at the application boundary.

## Modules

| Module | Types |
|--------|--------|
| `enums.py` | `Severity`, `VerificationStatus`, `FindingStatus`, `AuditStatus`, `ExecutionStatus`, `ArtifactKind`, `NodeErrorCode` |
| `common.py` | `ArtifactRef`, `SourceLocation`, `ModelUsage`, `NodeError`, `RunBudget` |
| `repository.py` | `RepositoryRef`, `RepositorySnapshot`, `FileArtifact`, `RepositoryManifest` |
| `plan.py` | `AuditTaskSpec`, `AuditPlan` |
| `finding.py` | `Evidence`, `CandidateFinding`, `Finding`, `VerifiedFinding` |
| `verification.py` | `VerificationRequest`, `VerificationResult`, `FixProposal` |
| `audit.py` | `AuditRequest`, `AuditReportSection`, `AuditReport` |
| `mappers.py` | `finding_from_legacy_dict`, `finding_to_legacy_dict`, `fingerprint_components` |

## Phase 1 invariants

1. New `Finding` defaults: `verification_status = NOT_RUN`, `status = NEW`.
2. `VerificationRequest.allow_execution` defaults to `False` (no untrusted exec).
3. `SourceLocation` / path fields reject absolute paths, drive letters, and `..` traversal.
4. Large payloads use `ArtifactRef` (uri + metadata), not embedded blobs in graph state.
5. `RunBudget.consume_*` methods return **new** instances (immutable-style updates).

## Compatibility

- Frontend and `agent_tasks` endpoints continue to see legacy flat dict keys (`file_path`, `line_start`, …).
- Use `finding_to_legacy_dict` / `finding_from_legacy_dict` only at API/persistence boundaries.
- Do not change public API response field names in M1.

## Tests

```bash
cd backend && uv run pytest tests/test_agent_domain.py -q
```

## Non-goals (M1)

- No LangGraph graph or nodes
- No ORM/Alembic changes
- No API route changes
- No wiring into OrchestratorAgent
