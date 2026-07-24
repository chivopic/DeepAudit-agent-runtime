# Sandbox boundary (M6)

> Package: `backend/app/services/agent/sandbox/`  
> Status: implemented 2026-07-24 · ADR-003

## Executors

| Class | Role |
|-------|------|
| `NullSandboxExecutor` | Phase 1 default — no untrusted exec |
| `LocalAllowlistExecutor` | Dev/test allowlisted high-level actions |
| `DockerSandboxExecutor` | Worker-direction stub; fails closed in API process |

## Policy defaults

- `network=none`, read-only root, `cap-drop=ALL`, no-new-privileges
- **No raw shell** from model (`allow_raw_shell=False`)
- Allowlisted actions only: `run_static_analyzer`, `run_selected_test`, `read_file_snippet`, `list_workspace`, `noop`, `echo`

## Tests

`tests/test_agent_sandbox_m6.py`
