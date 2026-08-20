# DeepAudit CLI

A dependency-light local security audit CLI. The Python package has no runtime
dependencies; Semgrep is an explicit external scanner dependency.

Development usage from the repository root:

```bash
PYTHONPATH=cli/src python -m deepaudit_cli doctor --semgrep /path/to/semgrep
PYTHONPATH=cli/src python -m deepaudit_cli audit . --semgrep /path/to/semgrep
```

Exit codes are stable: `0` clean, `1` findings, `2` invalid usage or scope,
`3` missing/broken scanner, `4` partial coverage, and `5` internal failure.
