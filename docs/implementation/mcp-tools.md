# MCP / governed tools (M8)

> Package: `backend/app/services/agent/tooling/`  
> Status: implemented 2026-07-24

## Boundary

```text
Node → ToolProtocol → BuiltinToolAdapter | MCPToolAdapter | MockToolAdapter
                    ↘ ToolRegistry (denylist + optional allowlist + audit log)
```

- Nodes **must not** import MCP SDK or raw shell tools.
- Pydantic I/O: `ToolInput` / `ToolOutput`.
- Hard denylist: `shell.exec`, `bash`, `exec`, `filesystem.write_anywhere`, `run_code`, `docker.exec`, …
- Audit log records tool name, success, duration, arg **keys** only (never full payloads/secrets).

## Adapters

| Class | Role |
|-------|------|
| `BuiltinToolAdapter` | Local allowlisted: `list_files`, `read_snippet`, `heuristic_scan`, `echo` |
| `MCPToolAdapter` | Transport-abstracted MCP client; filters denylist + allowlist |
| `InMemoryMCPTransport` | Test/dev scripted MCP |
| `MockToolAdapter` | Deterministic scripted responses |
| `ToolRegistry` | Route + policy + audit log |

## Tests

`tests/test_agent_tooling_m8.py`
