"""Adapters between domain Finding models and legacy agent API dict shapes.

Compatibility notes (freeze list):
- AgentFinding API historically uses flat keys such as file_path, line_start,
  severity string, verification_status, etc.
- Domain models use nested SourceLocation and enums.
- Map only at application/API boundaries; graph nodes use domain types.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from .enums import FindingStatus, Severity, VerificationStatus
from .finding import Evidence, Finding
from .common import SourceLocation


_SEVERITY_ALIASES = {
    "crit": Severity.CRITICAL,
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "med": Severity.MEDIUM,
    "medium": Severity.MEDIUM,
    "low": Severity.LOW,
    "info": Severity.INFO,
    "informational": Severity.INFO,
}


def _parse_severity(value: Any) -> Severity:
    if isinstance(value, Severity):
        return value
    if value is None:
        return Severity.MEDIUM
    s = str(value).strip().lower()
    return _SEVERITY_ALIASES.get(s, Severity.MEDIUM)


def _parse_finding_status(value: Any) -> FindingStatus:
    if isinstance(value, FindingStatus):
        return value
    if value is None:
        return FindingStatus.NEW
    s = str(value).strip().lower()
    try:
        return FindingStatus(s)
    except ValueError:
        return FindingStatus.NEW


def _parse_verification_status(value: Any) -> VerificationStatus:
    if isinstance(value, VerificationStatus):
        return value
    if value is None:
        return VerificationStatus.NOT_RUN
    s = str(value).strip().lower().replace("-", "_")
    # Legacy aliases
    aliases = {
        "not_verified": VerificationStatus.NOT_RUN,
        "unverified": VerificationStatus.NOT_RUN,
        "true_positive": VerificationStatus.CONFIRMED,
        "false_positive": VerificationStatus.REJECTED,
    }
    if s in aliases:
        return aliases[s]
    try:
        return VerificationStatus(s)
    except ValueError:
        return VerificationStatus.NOT_RUN


def fingerprint_components(
    *,
    file_path: Optional[str],
    start_line: Optional[int],
    title: str,
    rule_id: Optional[str] = None,
    cwe_id: Optional[str] = None,
) -> str:
    """Stable fingerprint for dedupe across analyzers."""
    parts = [
        (file_path or "").replace("\\", "/").strip().lower(),
        str(start_line or 0),
        (rule_id or "").strip().lower(),
        (cwe_id or "").strip().lower(),
        " ".join(title.strip().lower().split()),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def finding_from_legacy_dict(data: dict[str, Any]) -> Finding:
    """Build a domain Finding from a legacy AgentFinding-like dict."""
    file_path = data.get("file_path") or data.get("path") or data.get("file")
    start_line = data.get("line_start") or data.get("start_line") or data.get("line")
    end_line = data.get("line_end") or data.get("end_line")
    location = None
    if file_path:
        try:
            location = SourceLocation(
                file_path=str(file_path),
                start_line=int(start_line) if start_line is not None else None,
                end_line=int(end_line) if end_line is not None else None,
                function_name=data.get("function_name") or data.get("function"),
                class_name=data.get("class_name"),
            )
        except (ValueError, TypeError):
            location = None

    title = str(data.get("title") or data.get("name") or "Untitled finding")
    description = str(
        data.get("description")
        or data.get("detail")
        or data.get("message")
        or title
    )
    snippet = data.get("code_snippet") or data.get("snippet") or data.get("code")
    evidence: list[Evidence] = []
    if snippet:
        evidence.append(
            Evidence(
                kind="code",
                summary="code snippet",
                location=location,
                snippet=str(snippet)[:20_000],
            )
        )

    fp = data.get("fingerprint")
    if not fp:
        fp = fingerprint_components(
            file_path=str(file_path) if file_path else None,
            start_line=int(start_line) if start_line is not None else None,
            title=title,
            rule_id=data.get("rule_id") or data.get("rule"),
            cwe_id=data.get("cwe_id") or data.get("cwe"),
        )

    conf = data.get("confidence")
    try:
        confidence = float(conf) if conf is not None else 0.5
    except (TypeError, ValueError):
        confidence = 0.5
    # Legacy sometimes uses 0-100
    if confidence > 1.0:
        confidence = min(1.0, confidence / 100.0)

    # Prefer explicit category; fall back to legacy vulnerability_type / type.
    category = (
        data.get("category")
        or data.get("vulnerability_type")
        or data.get("type")
    )

    # Prefer confidence; accept ai_confidence (0-1 or 0-100).
    if data.get("confidence") is None and data.get("ai_confidence") is not None:
        try:
            confidence = float(data["ai_confidence"])
            if confidence > 1.0:
                confidence = min(1.0, confidence / 100.0)
        except (TypeError, ValueError):
            pass

    vstatus = _parse_verification_status(
        data.get("verification_status") or data.get("verification")
    )
    # Legacy is_verified=True → confirmed (only when no explicit verification_status)
    if (
        data.get("verification_status") is None
        and data.get("verification") is None
        and data.get("is_verified") is True
    ):
        vstatus = VerificationStatus.CONFIRMED

    meta = dict(data.get("metadata") or {})
    if data.get("vulnerability_type") and "vulnerability_type" not in meta:
        meta["vulnerability_type"] = data.get("vulnerability_type")
    if data.get("is_verified") is not None and "is_verified" not in meta:
        meta["is_verified"] = bool(data.get("is_verified"))

    kwargs: dict[str, Any] = {
        "audit_id": (
            str(data["audit_id"])
            if data.get("audit_id")
            else (str(data["task_id"]) if data.get("task_id") else None)
        ),
        "title": title,
        "description": description,
        "severity": _parse_severity(data.get("severity")),
        "status": _parse_finding_status(data.get("status")),
        "verification_status": vstatus,
        "category": category,
        "cwe_id": data.get("cwe_id") or data.get("cwe"),
        "owasp": data.get("owasp"),
        "location": location,
        "evidence": evidence,
        "confidence": confidence,
        "risk_score": (
            float(data["risk_score"]) if data.get("risk_score") is not None else 0.0
        ),
        "analyzer": data.get("analyzer") or data.get("source"),
        "rule_id": data.get("rule_id") or data.get("rule"),
        "fingerprint": fp,
        "recommendation": data.get("recommendation") or data.get("remediation"),
        "references": list(data.get("references") or []),
        "tags": list(data.get("tags") or []),
        "metadata": meta,
    }
    if data.get("id"):
        kwargs["id"] = str(data["id"])
    return Finding(**kwargs)


def finding_to_legacy_dict(finding: Finding) -> dict[str, Any]:
    """Serialize a domain Finding to a legacy-compatible API dict."""
    loc = finding.location
    snippet = None
    for ev in finding.evidence:
        if ev.snippet:
            snippet = ev.snippet
            break

    return {
        "id": finding.id,
        "audit_id": finding.audit_id,
        "task_id": finding.audit_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity.value,
        "status": finding.status.value,
        "verification_status": finding.verification_status.value,
        "category": finding.category,
        "vulnerability_type": finding.category or finding.rule_id or "other",
        "cwe_id": finding.cwe_id,
        "owasp": finding.owasp,
        "file_path": loc.file_path if loc else None,
        "line_start": loc.start_line if loc else None,
        "line_end": loc.end_line if loc else None,
        "function_name": loc.function_name if loc else None,
        "class_name": loc.class_name if loc else None,
        "code_snippet": snippet,
        "confidence": finding.confidence,
        "ai_confidence": finding.confidence,
        "is_verified": finding.verification_status.value == "confirmed",
        "risk_score": finding.risk_score,
        "analyzer": finding.analyzer,
        "rule_id": finding.rule_id,
        "fingerprint": finding.fingerprint,
        "recommendation": finding.recommendation,
        "suggestion": finding.recommendation,
        "references": list(finding.references),
        "tags": list(finding.tags),
        "metadata": dict(finding.metadata),
        "created_at": finding.created_at.isoformat() if finding.created_at else None,
        "updated_at": finding.updated_at.isoformat() if finding.updated_at else None,
    }
