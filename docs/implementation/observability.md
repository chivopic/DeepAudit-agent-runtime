# Observability façade (M9)

> Package: `backend/app/services/agent/observability/`  
> Status: implemented 2026-07-24

## Span tree (target)

```text
audit.run
├── graph.node.*
│   ├── context.build
│   ├── llm.call
│   └── tool.call
├── context.compact
├── sandbox.execute
├── verification.run
└── report.generate
```

## API

- `Tracer.span(name, **attrs)` — nested in-memory spans; optional OTEL bridge if `opentelemetry` installed
- `counter` / `histogram` metrics
- `redact_attributes` / `redact_value` — default deny for keys matching password/secret/api_key/token/… and common secret patterns (`sk-…`, `Bearer …`)
- Long strings truncated (>2000 chars)

## Tests

`tests/test_agent_observability_m9.py`
