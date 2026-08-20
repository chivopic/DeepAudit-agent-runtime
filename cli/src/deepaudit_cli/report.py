"""Terminal and JSON presentation for the stable local audit envelope."""

from __future__ import annotations

import json
from typing import Any

from deepaudit_cli.scanner import sanitize


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_doctor(envelope: dict[str, Any]) -> str:
    lines = [f"DeepAudit doctor: {envelope['status']}"]
    for check in envelope["checks"]:
        marker = "OK" if check["status"] == "ok" else "ERROR"
        lines.append(f"[{marker}] {check['name']}: {sanitize(check['message'])}")
    return "\n".join(lines) + "\n"

def render_audit(envelope: dict[str, Any]) -> str:
    coverage = envelope["coverage"]
    scope = envelope["scope"]
    findings = envelope["findings"]
    lines = [
        "DeepAudit local security audit",
        f"workspace: {sanitize(scope['root_display'])}",
        (
            f"status: {envelope['audit']['status']} | "
            f"coverage: {coverage['status']} "
            f"({coverage['scanned_files']}/{coverage['requested_files']} files)"
        ),
        "",
    ]
    if findings:
        lines.append(f"{len(findings)} candidate issue(s) at or above threshold")
        for finding in findings:
            location = finding["location"]
            place = f"{location['file_path']}:{location['start_line']}"
            lines.append(
                f"{finding['severity'].upper():8} {finding['display_id']:7} "
                f"{sanitize(finding['title'], max_length=120)}  "
                f"{sanitize(place, max_length=240)}"
            )
    else:
        lines.append("No candidate issues at or above the selected severity threshold.")
    lines.extend(("", "Results are static scanner signals (E0/candidate), not dynamically verified."))
    issues = [
        *envelope["metadata"].get("scanner_issues", []),
        *envelope["metadata"].get("manifest_issues", []),
    ]
    if issues:
        lines.append("Coverage/issues:")
        for issue in issues:
            lines.append(
                f"- {sanitize(issue.get('code', 'audit'))}: "
                f"{sanitize(issue.get('message', ''), max_length=300)}"
            )
    return "\n".join(lines) + "\n"
