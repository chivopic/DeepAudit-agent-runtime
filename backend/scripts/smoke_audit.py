#!/usr/bin/env python
"""End-to-end smoke check: does an audit actually work, right now?

Every real bug found in this codebase was found by running it, not by the test
suite:

    a repository over ~20 files died with GraphRecursionError  (fixtures are small)
    total_files was always 0 when polling                      (tests ran sync)
    every LLM finding was dropped on the floor                 (FakeLLM has no ```json fences)
    the SSE stream never closed                                (nothing subscribed)

Run this before claiming a milestone is done.

    uv run python scripts/smoke_audit.py            # FakeLLM, offline, free
    uv run python scripts/smoke_audit.py --real-llm # real model, costs tokens

Exit code is non-zero on the first failed check, so CI or a pre-release hook
can use it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

VULNERABLE = """import os
import pickle


def ping(host):
    os.system("ping -c 1 " + host)


def load(blob):
    return pickle.loads(blob)
"""

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        _failures.append(name)
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real-llm",
        action="store_true",
        help="use the configured model instead of FakeLLM (spends tokens)",
    )
    parser.add_argument("--files", type=int, default=30, help="files to generate")
    args = parser.parse_args()

    print("1. application imports and both audit surfaces are mounted")
    from app.main import app as fastapi_app

    routes = [r.path for r in fastapi_app.routes if hasattr(r, "path")]
    check("agent-tasks routes present", any("agent-tasks" in r for r in routes))
    check("graph-audits routes present", any("graph-audits" in r for r in routes))
    check(
        "graph-audits event stream present",
        any(r.endswith("/events/stream") for r in routes),
    )

    print(f"\n2. audit a workspace of {args.files} files")
    # Deliberately more files than LangGraph's default recursion limit of 25:
    # that limit once killed every real repository.
    workspace = Path(tempfile.mkdtemp(prefix="deepaudit-smoke-"))
    for i in range(args.files):
        (workspace / f"mod{i}.py").write_text(VULNERABLE, encoding="utf-8")

    from app.services.agent.application.facade import GraphAuditFacade
    from app.services.agent.domain import AuditRequest, RunBudget
    from app.services.agent.domain.repository import RepositoryRef
    from app.services.agent.graph.runtime import GraphRuntime

    if args.real_llm:
        from app.services.agent.graph.llm_gateway import LLMServiceGateway
        from app.services.llm.service import LLMService

        llm = LLMServiceGateway(LLMService(user_config=None))
        offline = False
    else:
        from app.services.agent.graph.llm import FakeLLM

        llm = FakeLLM()
        offline = True

    request = AuditRequest(
        repository=RepositoryRef(source_type="local", local_path=str(workspace)),
        languages=["python"],
        budget=RunBudget(max_tokens=200_000, max_model_calls=args.files + 10),
    )
    facade = GraphAuditFacade()
    summary = await facade.start(
        request, runtime=GraphRuntime(llm=llm, offline=offline)
    )

    check(
        "run reached a terminal state",
        summary.get("status") in {"completed", "partial"},
        f"status={summary.get('status')}",
    )
    check(
        "more files analysed than the stock recursion limit",
        int(summary.get("total_files") or 0) >= args.files,
        f"total_files={summary.get('total_files')}",
    )

    print("\n3. results survive persistence (clients poll, they do not watch)")
    polled = await facade.get_task(request.id)
    check("task readable after the run", polled is not None)
    if polled:
        check(
            "file count survives into the stored row",
            int(polled.get("total_files") or 0) >= args.files,
            f"total_files={polled.get('total_files')}",
        )

    findings = await facade.list_findings(request.id)
    check("findings were produced", len(findings) > 0, f"{len(findings)} findings")
    if findings:
        check(
            "findings carry a location",
            all(f.get("file_path") for f in findings),
        )

    print("\n4. events are readable and the stream terminates")
    events = await facade.list_events(request.id)
    check("events recorded", len(events) > 0, f"{len(events)} events")
    check(
        "a terminal event was published",
        any(
            e.get("type") in {"task_complete", "task_error", "task_cancel"}
            for e in events
        ),
    )

    print()
    if _failures:
        print(f"SMOKE FAILED: {len(_failures)} check(s) — " + ", ".join(_failures))
        return 1
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
