"""M10 eval suite tests (deterministic FakeLLM graph)."""

from __future__ import annotations

import pytest

from app.services.agent.domain import Finding, Severity, SourceLocation, VerificationStatus
from tests.evals.evaluators import (
    EvalCase,
    check_cwe_any,
    check_schema,
    check_verification_policy,
    evaluate_case,
)
from tests.evals.fixtures import CI_CASES, ALL_CASES, python_safe_param, python_vulnerable_sqli
from tests.evals.runner import run_case, run_suite


def test_schema_and_verification_evaluators():
    f = Finding(
        title="SQLi",
        description="concat",
        severity=Severity.HIGH,
        location=SourceLocation(file_path="app/db.py", start_line=2, end_line=2),
        cwe_id="CWE-89",
        verification_status=VerificationStatus.NOT_RUN,
    )
    ok, _ = check_schema([f])
    assert ok
    ok, _ = check_verification_policy([f], True)
    assert ok
    ok, _ = check_cwe_any([f], ["CWE-89"])
    assert ok


def test_evaluate_case_safe_no_findings():
    case = python_safe_param()
    r = evaluate_case(case, [])
    assert r.passed
    assert r.checks["count"]


def test_evaluate_case_count_fail():
    case = python_vulnerable_sqli()
    r = evaluate_case(case, [])
    assert not r.passed
    assert r.checks["count"] is False


@pytest.mark.asyncio
async def test_run_case_vulnerable_sqli():
    case = python_vulnerable_sqli()
    result = await run_case(case)
    assert result.finding_count >= 1
    assert result.checks.get("verification_policy") is True
    assert result.checks.get("schema") is True
    assert result.passed, result.messages


@pytest.mark.asyncio
async def test_run_case_safe_param():
    case = python_safe_param()
    result = await run_case(case)
    assert result.checks.get("verification_policy") is True
    # may have 0 findings; must pass
    assert result.passed, result.messages


@pytest.mark.asyncio
async def test_ci_suite_passes():
    suite = await run_suite(ci_only=True)
    assert suite.passed, suite.summary


@pytest.mark.asyncio
async def test_full_suite_smoke():
    # Full suite may have soft CWE mismatches for XSS (CWE-79 vs expected none)
    suite = await run_suite(cases=ALL_CASES)
    # At least CI subset must pass; full suite reports
    assert suite.summary["total"] == len(ALL_CASES)
    ci_ids = {c.id for c in CI_CASES}
    for r in suite.results:
        if r.case_id in ci_ids:
            assert r.passed, (r.case_id, r.messages)
