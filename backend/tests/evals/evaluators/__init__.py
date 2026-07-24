"""Deterministic evaluators for audit graph outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.agent.domain import Finding, Severity, VerificationStatus


@dataclass
class EvalCase:
    """One fixture case description."""

    id: str
    language: str
    category: str  # vulnerable | safe
    files: dict[str, str]
    expected_min_findings: int = 0
    expected_cwe_any: list[str] = field(default_factory=list)
    expected_paths: list[str] = field(default_factory=list)
    expect_not_run_verification: bool = True
    notes: str = ""


@dataclass
class EvalResult:
    case_id: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    finding_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def _finding_path(f: Finding) -> Optional[str]:
    if f.location and f.location.file_path:
        return f.location.file_path
    return f.metadata.get("file_path") if f.metadata else None


def check_schema(findings: list[Finding]) -> tuple[bool, str]:
    """All findings must be valid Finding models (already parsed) with required fields."""
    for f in findings:
        path = _finding_path(f)
        if not f.title or not path:
            return False, f"missing title/file_path on {f.id}"
        if f.severity is None:
            return False, f"missing severity on {f.id}"
    return True, "schema ok"


def check_location(findings: list[Finding], expected_paths: list[str]) -> tuple[bool, str]:
    if not expected_paths:
        return True, "no path expectation"
    found_paths = {p for f in findings if (p := _finding_path(f))}
    missing = [p for p in expected_paths if p not in found_paths]
    if missing and findings:
        # soft: if we have findings, at least one expected path should match
        if not any(p in found_paths for p in expected_paths):
            return False, f"expected paths not in findings: {missing}"
    return True, "location ok"


def check_verification_policy(findings: list[Finding], expect_not_run: bool) -> tuple[bool, str]:
    if not expect_not_run:
        return True, "verification policy n/a"
    bad = [f.id for f in findings if f.verification_status is not VerificationStatus.NOT_RUN]
    if bad:
        return False, f"Phase1 findings must be NOT_RUN, got: {bad}"
    return True, "verification NOT_RUN ok"


def check_count(findings: list[Finding], minimum: int) -> tuple[bool, str]:
    n = len(findings)
    if n < minimum:
        return False, f"expected >= {minimum} findings, got {n}"
    return True, f"count {n} >= {minimum}"


def check_cwe_any(findings: list[Finding], cwes: list[str]) -> tuple[bool, str]:
    if not cwes:
        return True, "no cwe expectation"
    if not findings and not cwes:
        return True, "ok"
    found = {(f.cwe_id or "").upper() for f in findings}
    # accept CWE-89 or 89 forms
    normalized = set()
    for c in found:
        normalized.add(c)
        if c.startswith("CWE-"):
            normalized.add(c.replace("CWE-", ""))
    for want in cwes:
        w = want.upper()
        if w in normalized or w.replace("CWE-", "") in normalized:
            return True, f"matched cwe {want}"
    if not findings:
        return False, f"no findings to match CWE {cwes}"
    # soft fail message only when findings exist but no CWE match
    return False, f"none of {cwes} in {found}"


def evaluate_case(
    case: EvalCase,
    findings: list[Finding],
    *,
    status: Optional[str] = None,
) -> EvalResult:
    checks: dict[str, bool] = {}
    messages: list[str] = []

    ok, msg = check_schema(findings)
    checks["schema"] = ok
    messages.append(msg)

    ok, msg = check_count(findings, case.expected_min_findings)
    checks["count"] = ok
    messages.append(msg)

    ok, msg = check_location(findings, case.expected_paths)
    checks["location"] = ok
    messages.append(msg)

    ok, msg = check_verification_policy(findings, case.expect_not_run_verification)
    checks["verification_policy"] = ok
    messages.append(msg)

    if case.expected_cwe_any:
        ok, msg = check_cwe_any(findings, case.expected_cwe_any)
        checks["cwe"] = ok
        messages.append(msg)

    if case.category == "safe":
        # safe fixtures should not invent high severity noise when min is 0
        high = [f for f in findings if f.severity in {Severity.CRITICAL, Severity.HIGH}]
        checks["safe_no_critical_noise"] = len(high) == 0 or case.expected_min_findings > 0
        if not checks["safe_no_critical_noise"]:
            messages.append(f"safe case produced high findings: {[f.title for f in high]}")

    if status is not None and str(status).strip():
        st = str(status).lower()
        # completed / failed / cancelled / partial are terminal for Phase-1 graph
        checks["status_terminal"] = st in {
            "completed",
            "failed",
            "cancelled",
            "partial",
        }
        if not checks["status_terminal"]:
            messages.append(f"unexpected status: {status}")

    passed = all(checks.values()) if checks else False
    return EvalResult(
        case_id=case.id,
        passed=passed,
        checks=checks,
        messages=messages,
        finding_count=len(findings),
    )
