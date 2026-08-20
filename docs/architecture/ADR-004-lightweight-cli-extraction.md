# ADR-004: Extract a dependency-light local audit CLI

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** DeepAudit maintainers / CLI product workstream

## Context

The first CLI prototype proved that the existing LangGraph audit path can run a
deterministic, model-free Semgrep scan. It also proved an undesirable packaging
property: importing that path makes the local CLI inherit the backend's agent,
web, database, report, and AI dependency graph. A minimal experiment still
needed roughly 34 MB of Python packages before installing Semgrep.

The intended product is a local developer tool. Its base command should start
quickly, install independently, work without a server, and report exactly what
was and was not scanned. Durable agent orchestration is useful to the hosted
backend but is not a prerequisite for this local scanner pipeline.

`CLAUDE.md` forbids a second package root until an explicit extraction ADR.
This ADR is that authorization. It does not change the backend architecture or
the M0-M11 decisions for server-side agents.

## Decision

1. Add a separately buildable `cli/` distribution in this monorepo.
2. The base CLI has no third-party Python runtime dependencies. It uses the
   standard library and invokes a locally installed, exact-version Semgrep
   executable with fixed argv, local bundled rules, a sanitized environment,
   bounded time, and bounded output.
3. Reuse audit semantics, not backend runtime code:
   finding fingerprints, E0 candidate evidence, finding state, coverage,
   partial-result disclosure, scanner version pinning, and exit codes.
4. Do not import `backend/app` from `cli/`, and do not import `cli/` from the
   backend. Shared code may be extracted later only after its API stabilizes.
5. Keep the graph-backed prototype on `cli-agent` and as an implementation
   oracle while the lightweight path reaches behavioral parity.
6. TUI and AI reasoning are optional future extras. They must not become base
   dependencies or weaken the deterministic offline scan.

## Package boundary

```text
cli/src/deepaudit_cli/
  main.py       # argparse boundary and stable exit behavior
  pipeline.py   # validate -> manifest -> scan -> filter -> envelope
  manifest.py   # deterministic workspace discovery and coverage
  scanner.py    # governed subprocess primitive
  semgrep.py    # Semgrep adapter and normalization
  domain.py     # dependency-free audit contract
  report.py     # terminal and JSON presentation
  rules/        # local, versioned scanner rules
```

## Security invariants

- No shell invocation and no arbitrary command strings.
- Resolve the scanner executable once; require the supported exact version.
- Do not inherit caller secrets into scanner subprocesses.
- Do not use remote Semgrep rules, metrics, or version checks.
- Reject symlinks, traversal, non-regular files, and result paths outside the
  audited workspace.
- Bound file count, file size, scan duration, output bytes, and result count.
- Never describe E0 static matches as dynamically verified vulnerabilities.
- Report incomplete coverage and use a non-success exit code for partial runs.

## Consequences

### Positive

- Base wheel remains small and has zero Python runtime dependency fan-out.
- CLI releases can be tested and published independently from the backend.
- The local trust boundary is understandable and directly testable.

### Costs

- Some small domain and scanner concepts are intentionally duplicated until a
  stable shared contract exists.
- Semgrep remains a substantial external tool and must be installed separately.
- Changes to finding JSON require compatibility tests across both paths during
  the transition.

## Alternatives considered

| Option | Why rejected |
|--------|--------------|
| Install the full backend | Slow, large, and exposes irrelevant services |
| Keep a minimal LangGraph subset | Still carries orchestration abstractions the scan does not need |
| Rewrite Semgrep in Python | Recreates a mature analyzer and greatly expands scope |
| Remove the prototype immediately | Loses a useful behavioral oracle before parity is demonstrated |

## Exit criteria for replacing the prototype

- Base package imports with only the Python standard library.
- `doctor` and `audit` work from a built wheel.
- Normalized findings and coverage match approved golden fixtures.
- Subprocess, path-jail, timeout, output-cap, and JSON-terminal tests pass.
- Installation and scanner size are documented separately and honestly.
