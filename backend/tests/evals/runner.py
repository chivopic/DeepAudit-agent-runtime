"""Eval runner: execute fixture cases against FakeLLM graph (deterministic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    Finding,
    RepositoryRef,
    RunBudget,
    VerificationStatus,
)
from app.services.agent.graph import compile_audit_graph, empty_audit_state
from app.services.agent.graph.llm import FakeLLM
from app.services.agent.graph.runtime import GraphRuntime, reset_runtime, set_runtime

from tests.evals.evaluators import EvalCase, EvalResult, evaluate_case
from tests.evals.fixtures import ALL_CASES, CI_CASES


@dataclass
class SuiteResult:
    results: list[EvalResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(r.passed for r in self.results)

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.passed),
            "failed": sum(1 for r in self.results if not r.passed),
            "case_ids": [r.case_id for r in self.results if not r.passed],
        }


def case_to_request(case: EvalCase) -> AuditRequest:
    return AuditRequest(
        project_id="eval",
        repository=RepositoryRef(source_type="local", local_path="/tmp/eval/" + case.id),
        languages=[case.language] if case.language else [],
        budget=RunBudget(max_tokens=50_000, max_model_calls=50, max_files=50),
        enable_verification=False,
        title=f"eval:{case.id}",
        config={"eval_case_id": case.id, "files": case.files},
    )


async def run_case(case: EvalCase, *, llm: Optional[FakeLLM] = None) -> EvalResult:
    """Run one eval case through the LangGraph skeleton with fixture files."""
    req = case_to_request(case)
    runtime = GraphRuntime(
        llm=llm or FakeLLM(),
        offline=True,
        extra={"fixture_files": case.files},
    )
    app = compile_audit_graph()
    state = empty_audit_state(audit_id=req.id, request=req, thread_id=req.id)
    token = set_runtime(runtime)
    try:
        result = await app.ainvoke(
            state,
            {"configurable": {"thread_id": req.id, "runtime": runtime}},
        )
    finally:
        reset_runtime(token)

    findings: list[Finding] = list(result.get("normalized_findings") or [])
    # Attach file_path convenience for evaluators that look at location
    for f in findings:
        if f.location is None:
            continue
        # ensure verification policy Phase 1
        if f.verification_status is None:
            f.verification_status = VerificationStatus.NOT_RUN

    status = result.get("status")
    status_s = status.value if isinstance(status, AuditStatus) else str(status or "")
    return evaluate_case(case, findings, status=status_s)


async def run_suite(
    cases: Optional[list[EvalCase]] = None,
    *,
    ci_only: bool = False,
) -> SuiteResult:
    selected = cases or (CI_CASES if ci_only else ALL_CASES)
    results: list[EvalResult] = []
    for case in selected:
        results.append(await run_case(case))
    return SuiteResult(results=results)


def main() -> int:
    """CLI entry: ``python -m tests.evals.runner``."""
    import asyncio
    import json
    import sys

    ci = "--ci" in sys.argv
    suite = asyncio.run(run_suite(ci_only=ci))
    print(json.dumps(suite.summary, indent=2))
    for r in suite.results:
        mark = "PASS" if r.passed else "FAIL"
        print(f"  [{mark}] {r.case_id} findings={r.finding_count} checks={r.checks}")
        if not r.passed:
            for m in r.messages:
                print(f"         - {m}")
    return 0 if suite.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
