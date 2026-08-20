"""Synchronous local audit pipeline with no agent runtime dependency."""

from __future__ import annotations

import platform
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from deepaudit_cli.domain import SEVERITY_RANK, ScanStatus, Severity, primitive
from deepaudit_cli.manifest import build_manifest, resolve_workspace
from deepaudit_cli.scanner import sanitize
from deepaudit_cli.semgrep import SEMGREP_VERSION, SemgrepScanner, bundled_rules, combined_rule_hash


class ConfigurationError(ValueError):
    pass


class EnvironmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuditOptions:
    workspace: Path
    semgrep_executable: str | Path = "semgrep"
    severity_threshold: Severity = Severity.MEDIUM
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    max_files: int = 500
    max_duration_seconds: int = 300
    max_results: int = 2000
    max_target_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class AuditOutcome:
    envelope: dict[str, Any]
    exit_code: int


def safe_message(value: object, workspace: Path | None = None) -> str:
    message = sanitize(value, max_length=2000)
    prefixes = {str(Path.home())}
    if workspace is not None:
        prefixes.add(str(workspace))
    for prefix in sorted(prefixes, key=len, reverse=True):
        message = message.replace(prefix, "<local-path>")
    return message


def audit(options: AuditOptions) -> AuditOutcome:
    try:
        root = resolve_workspace(options.workspace)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ConfigurationError(str(exc)) from exc
    if any(value <= 0 for value in (options.max_files, options.max_duration_seconds, options.max_results, options.max_target_bytes)):
        raise ConfigurationError("audit limits must be positive")
    try:
        manifest = build_manifest(
            root,
            include=options.include,
            exclude=options.exclude,
            max_files=options.max_files,
            max_target_bytes=options.max_target_bytes,
        )
        scanner = SemgrepScanner(
            options.semgrep_executable,
            timeout_seconds=options.max_duration_seconds,
        )
        scan = scanner.scan(root, manifest.files, max_results=options.max_results)
    except (FileNotFoundError, PermissionError, RuntimeError) as exc:
        raise EnvironmentError(safe_message(exc, root)) from exc
    except (OSError, ValueError) as exc:
        raise ConfigurationError(safe_message(exc, root)) from exc
    findings = tuple(
        finding
        for finding in scan.findings
        if SEVERITY_RANK[finding.severity] >= SEVERITY_RANK[options.severity_threshold]
    )
    manifest_issues = [primitive(issue) for issue in manifest.issues]
    scanner_issues = [primitive(issue) for issue in scan.issues]
    partial = manifest.incomplete or scan.status is ScanStatus.PARTIAL
    failed = scan.status is ScanStatus.ERROR
    audit_status = "failed" if failed else "partial" if partial else "completed"
    envelope = {
        "schema_version": "1.0",
        "audit": {
            "id": f"audit_{uuid.uuid4().hex[:12]}",
            "status": audit_status,
            "mode": "non_interactive",
        },
        "scope": {
            "root_display": root.name or ".",
            "files_selected": len(manifest.files),
            "files_discovered": manifest.discovered_files,
            "languages": list(manifest.languages),
        },
        "coverage": {
            "scanner": "semgrep",
            "status": scan.status.value,
            "requested_files": scan.requested_files,
            "scanned_files": scan.scanned_files,
            "skipped_files": len(scan.skipped_paths),
            "skipped_paths": list(scan.skipped_paths),
            "duration_seconds": round(scan.duration_seconds, 6),
            "version": scan.version,
            "rule_set_hash": scan.rule_set_hash,
            "max_memory_bytes": scan.max_memory_bytes,
            "manifest": {
                "selected_files": len(manifest.files),
                "discovered_files": manifest.discovered_files,
                "limited": manifest.limited,
                "incomplete": manifest.incomplete,
                "issues": manifest_issues,
            },
        },
        "budget": {
            "max_files": options.max_files,
            "max_duration_seconds": options.max_duration_seconds,
            "max_results": options.max_results,
            "max_target_bytes": options.max_target_bytes,
        },
        "findings": [primitive(finding) for finding in findings],
        "errors": [],
        "artifacts": [],
        "metadata": {
            "severity_threshold": options.severity_threshold.value,
            "all_findings_count": len(scan.findings),
            "reported_findings_count": len(findings),
            "scanner_issues": scanner_issues,
            "manifest_issues": manifest_issues,
            "model_calls": 0,
            "verification": "not_run",
        },
    }
    exit_code = 3 if failed else 4 if partial else 1 if findings else 0
    return AuditOutcome(envelope=envelope, exit_code=exit_code)


def doctor(semgrep_executable: str | Path = "semgrep") -> tuple[dict[str, Any], int]:
    rules = bundled_rules()
    checks: list[dict[str, Any]] = [
        {
            "name": "python",
            "status": "ok",
            "message": platform.python_version(),
            "metadata": {"implementation": platform.python_implementation()},
        },
        {
            "name": "runtime_dependencies",
            "status": "ok",
            "message": "0 third-party Python runtime dependencies",
            "metadata": {},
        },
    ]
    if rules:
        checks.append(
            {
                "name": "rules",
                "status": "ok",
                "message": f"{len(rules)} bundled rule file(s)",
                "metadata": {"files": [rule.name for rule in rules], "rule_set_hash": combined_rule_hash(rules)},
            }
        )
    else:
        checks.append({"name": "rules", "status": "error", "message": "bundled Semgrep rules are missing", "metadata": {}})
    try:
        scanner = SemgrepScanner(semgrep_executable)
        version = scanner.version()
        checks.append(
            {
                "name": "semgrep",
                "status": "ok",
                "message": f"Semgrep {version}",
                "metadata": {"expected_version": SEMGREP_VERSION, "executable": scanner.executable.name},
            }
        )
    except (OSError, RuntimeError, ValueError) as exc:
        checks.append(
            {
                "name": "semgrep",
                "status": "error",
                "message": safe_message(exc),
                "metadata": {"expected_version": SEMGREP_VERSION},
            }
        )
    status = "error" if any(check["status"] == "error" for check in checks) else "ok"
    return {"schema_version": "1.0", "status": status, "checks": checks}, 0 if status == "ok" else 3
