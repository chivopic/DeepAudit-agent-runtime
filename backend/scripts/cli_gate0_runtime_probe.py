#!/usr/bin/env python3
"""Prove the Agent Harness can run without the backend infrastructure stack."""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import sys
import time
from typing import Any

from app.services.agent.domain import AuditRequest, AuditStatus, RepositoryRef
from app.services.agent.harness import default_runtime

FORBIDDEN_DISTRIBUTIONS = {
    "chromadb",
    "docker",
    "fastapi",
    "redis",
    "sqlalchemy",
    "weasyprint",
}


def installed_distribution_names() -> set[str]:
    return {
        str(distribution.metadata["Name"]).lower()
        for distribution in importlib.metadata.distributions()
        if distribution.metadata["Name"]
    }


async def run_probe() -> dict[str, Any]:
    installed_forbidden = sorted(FORBIDDEN_DISTRIBUTIONS & installed_distribution_names())
    if installed_forbidden:
        raise RuntimeError(
            "infrastructure distributions are installed: " + ", ".join(installed_forbidden)
        )

    runtime = default_runtime(
        files={"app.py": "def run(value): return eval(value)"},
        allow_execution=False,
    )
    request = AuditRequest(
        repository=RepositoryRef(
            source_type="local",
            local_path="/deepaudit-gate0/nonexistent-fixture",
        ),
        languages=["python"],
        enable_verification=False,
        enable_rag=False,
    )
    started = time.perf_counter()
    result = await runtime.start(request)
    duration = time.perf_counter() - started
    if result.status is not AuditStatus.COMPLETED or not result.findings:
        raise RuntimeError(
            f"minimal runtime audit failed: status={result.status.value}, "
            f"findings={len(result.findings)}"
        )

    loaded_forbidden = sorted(
        name for name in sys.modules if name.split(".", 1)[0].lower() in FORBIDDEN_DISTRIBUTIONS
    )
    if loaded_forbidden:
        raise RuntimeError("infrastructure modules loaded: " + ", ".join(loaded_forbidden))

    return {
        "schema_version": "deepaudit.cli.gate0.runtime.v1",
        "status": "go",
        "python": sys.version.split()[0],
        "duration_seconds": round(duration, 6),
        "finding_count": len(result.findings),
        "installed_forbidden_distributions": installed_forbidden,
        "loaded_forbidden_modules": loaded_forbidden,
    }


def main() -> int:
    try:
        result = asyncio.run(run_probe())
    except Exception as exc:  # noqa: BLE001 - probe emits a structured No-Go result
        print(
            json.dumps(
                {
                    "schema_version": "deepaudit.cli.gate0.runtime.v1",
                    "status": "no_go",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
