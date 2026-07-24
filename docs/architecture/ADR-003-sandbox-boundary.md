# ADR-003: Sandbox Boundary

- **Status:** Accepted (M0)
- **Date:** 2026-07-24

## Context

Verification today uses `SandboxManager` (`tools/sandbox_tool.py`) and the `deepaudit/sandbox` image. Defaults include network `none`, read-only root, non-root user, resource limits, and a custom seccomp profile. Tools such as `run_code` allow the LLM to supply arbitrary code for execution.

**Critical deployment issue:** `docker-compose.yml` mounts **`/var/run/docker.sock` into the backend API container**, so a compromised API process has host Docker control.

Phase 1 of the refactor **must not** execute untrusted code on the default path; Phase 2 introduces governed verification.

## Decision

1. **Phase 1:** Graph path marks all findings `verification_status = NOT_RUN`. No sandbox execution required for M1–M4 acceptance.
2. **Phase 2+:** Sandbox is accessed only through a **`SandboxExecutor` protocol** with `ExecutionSpec` / `ExecutionResult` (Pydantic).
3. **Commands are argv arrays**, never interpolated shell strings from model/user input.
4. **Model-facing API is high-level verification actions** (e.g. `run_selected_test`, `run_static_analyzer`, `apply_patch_and_test`), mapped server-side to allowlisted templates.
5. **Default security policy** (minimum): non-root, rootless where practical, `network=none`, read-only root FS, tmpfs workdir, `cap-drop=ALL`, no-new-privileges, default/custom seccomp, CPU/memory/PID/time/output limits; no privileged/host net/PID; no docker.sock in **execution** containers; no writable mount of the real host repository.
6. **Process architecture target:** API/worker separation — API does **not** hold docker.sock; a dedicated sandbox worker owns container lifecycle.
7. **Repository handling:** copy needed files into ephemeral workspace; destroy after export of logs/artifacts.
8. **Human approval** required for network, installs, non-allowlisted commands, elevated privilege, external targets, ambiguous strategies.
9. **Security policy denials are not auto-retried** by harness retry policies.

## Consequences

### Positive

- Reduces blast radius vs socket-mounted API.
- Aligns verification with evidence quality goals (confirmed / rejected / inconclusive).
- Clear test seams: mock executor in unit tests.

### Negative

- Compose and ops docs must change when socket is removed from API.
- Rootless Docker is not a complete security boundary alone — defense in depth still required.
- LLM freestyle PoC authoring becomes more constrained (by design).

### Transition

- Keep existing sandbox image and much of `SandboxManager` logic behind the new protocol.
- Feature-flag verification levels already present (`analysis_only`, `sandbox`, `generate_poc`).

## Alternatives considered

| Option | Why rejected |
|--------|--------------|
| Keep docker.sock on API permanently | Unacceptable production blast radius |
| Disable sandbox forever | Loses verification differentiation |
| Full VM per run | Cost/complexity beyond current product stage |
| Trust model shell with prompt-only constraints | Insufficient |

## Follow-ups

- M6: protocol + hardened docker executor + worker layout plan
- M7: verification subgraph + approval hooks
- Ops: update compose to remove socket from API when worker lands
