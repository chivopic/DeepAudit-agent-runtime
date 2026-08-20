"""Shared application service for local, non-interactive security audits."""

from __future__ import annotations

import hashlib
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    CliAuditEnvelope,
    CliAuditSummary,
    CliFindingView,
    CliScope,
    DoctorCheck,
    DoctorEnvelope,
    RepositoryRef,
    RunBudget,
    Severity,
)
from app.services.agent.harness import (
    AgentRuntime,
    AgentSpec,
    PermissionPolicy,
    ToolRouter,
)
from app.services.agent.tooling.scanners import (
    SEMGREP_VERSION,
    ScannerIssue,
    ScannerRequest,
    ScannerResult,
    ScannerStatus,
    SemgrepScanner,
    bundled_semgrep_rules,
    sanitize_untrusted,
)


class CliConfigurationError(ValueError):
    """Invalid local path, option, or output configuration."""


class CliEnvironmentError(RuntimeError):
    """A required local scanner/runtime dependency is unavailable."""


@dataclass(frozen=True)
class LocalAuditOptions:
    workspace: Path
    semgrep_executable: str | Path = "semgrep"
    severity_threshold: Severity = Severity.MEDIUM
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    strict: bool = False
    max_files: int = 500
    max_duration_seconds: int = 300
    max_results: int = 2_000
    max_target_bytes: int = 1 * 1024 * 1024


@dataclass(frozen=True)
class LocalAuditOutcome:
    envelope: CliAuditEnvelope
    exit_code: int


def _rules_hash(rules: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for rule in rules:
        digest.update(rule.name.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(rule.read_bytes()).hexdigest().encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_message(value: Any, workspace: Path | None = None) -> str:
    message = sanitize_untrusted(value, max_length=2_000)
    replacements = [str(Path.home())]
    if workspace is not None:
        replacements.append(str(workspace))
    for prefix in sorted(set(replacements), key=len, reverse=True):
        if prefix:
            message = message.replace(prefix, "<local-path>")
    return message


class _UnavailableScanner:
    name = "semgrep"

    def __init__(self, *, reason: str, rule_set_hash: str) -> None:
        self.reason = reason
        self.rule_set_hash = rule_set_hash

    async def scan(self, request: ScannerRequest) -> ScannerResult:
        return ScannerResult(
            scanner=self.name,
            status=ScannerStatus.ERROR,
            version=None,
            rule_set_hash=self.rule_set_hash,
            requested_files=len(request.relative_files),
            scanned_files=0,
            issues=(ScannerIssue("scanner_unavailable", self.reason),),
        )


class LocalAuditApplication:
    """Validate scope, assemble governed runtime, and return one canonical result."""

    @staticmethod
    def resolve_workspace(value: Path) -> Path:
        try:
            root = value.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise CliConfigurationError(f"workspace does not exist: {value}") from exc
        if not root.is_dir():
            raise CliConfigurationError(f"workspace is not a directory: {value}")
        return root

    async def audit(self, options: LocalAuditOptions) -> LocalAuditOutcome:
        root = self.resolve_workspace(options.workspace)
        if options.max_files <= 0:
            raise CliConfigurationError("--max-files must be positive")
        if options.max_duration_seconds <= 0:
            raise CliConfigurationError("--max-duration must be positive")
        if options.max_results <= 0 or options.max_target_bytes <= 0:
            raise CliConfigurationError("scanner limits must be positive")

        rules = bundled_semgrep_rules()
        if not rules:
            raise CliEnvironmentError("bundled Semgrep rules are missing")
        try:
            scanner: Any = SemgrepScanner(
                options.semgrep_executable,
                rules=rules,
                timeout_seconds=float(options.max_duration_seconds),
            )
            scanner.version()
        except (OSError, RuntimeError, ValueError) as exc:
            reason = _safe_message(exc, root)
            if options.strict:
                raise CliEnvironmentError(reason) from exc
            scanner = _UnavailableScanner(
                reason=reason,
                rule_set_hash=_rules_hash(rules),
            )

        budget = RunBudget(
            max_files=options.max_files,
            max_duration_seconds=options.max_duration_seconds,
        )
        spec = AgentSpec(
            name="deepaudit-cli",
            provider="none",
            model="none",
            offline=True,
            enable_model_calls=False,
            enable_heuristic_analysis=False,
            allow_execution=False,
            tool_allowlist=[],
            budget=budget,
            metadata={"mode": "non_interactive", "model_calls": "disabled"},
        )
        runtime = AgentRuntime(
            spec=spec,
            tool_router=ToolRouter(),
            permission_policy=PermissionPolicy(
                allow_execution=False,
                allow_network=False,
                allow_raw_shell=False,
            ),
            scanner=scanner,
            workspace_root=root,
            runtime_extra={
                "scanner_max_results": options.max_results,
                "scanner_max_target_bytes": options.max_target_bytes,
            },
        )
        request = AuditRequest(
            repository=RepositoryRef(source_type="local", local_path=str(root)),
            languages=["python"],
            include_paths=list(options.include_paths),
            exclude_paths=list(options.exclude_paths),
            severity_threshold=options.severity_threshold,
            enable_verification=False,
            enable_rag=False,
            llm_provider=None,
            llm_model=None,
            budget=budget,
            config={"strict": options.strict},
        )
        result = await runtime.start(request)
        state = result.raw_state
        manifest = state.get("manifest")
        metadata = dict(state.get("meta") or {})
        coverage = dict(metadata.get("scanner_coverage") or {})
        coverage["manifest"] = {
            "selected_files": len(manifest.files) if manifest else 0,
            "discovered_files": manifest.stats.get("discovered", 0) if manifest else 0,
            "limited": bool(manifest.stats.get("limited")) if manifest else False,
            "incomplete": bool(manifest.stats.get("incomplete")) if manifest else False,
            "issues": list(manifest.stats.get("issues") or []) if manifest else [],
        }
        findings = [
            finding
            for finding in result.findings
            if _severity_rank(finding.severity) >= _severity_rank(options.severity_threshold)
        ]
        views = [
            CliFindingView.model_validate(
                {**finding.model_dump(mode="python"), "display_id": f"FND-{index:03d}"}
            )
            for index, finding in enumerate(findings, start=1)
        ]
        errors = [
            {
                **error.model_dump(mode="json"),
                "message": _safe_message(error.message, root),
            }
            for error in state.get("errors") or []
        ]
        scanner_issues = []
        for issue in metadata.get("scanner_issues") or []:
            scanner_issues.append(
                {
                    **dict(issue),
                    "message": _safe_message(issue.get("message"), root),
                    "path": _safe_message(issue.get("path"), root) if issue.get("path") else None,
                }
            )

        envelope = CliAuditEnvelope(
            audit=CliAuditSummary(id=result.audit_id, status=result.status),
            scope=CliScope(
                root_display=root.name or ".",
                files_selected=len(manifest.files) if manifest else 0,
                files_discovered=(manifest.stats.get("discovered", 0) if manifest else 0),
                languages=sorted(
                    {file.language for file in manifest.files if file.language}
                    if manifest
                    else set()
                ),
            ),
            coverage=coverage,
            budget=state.get("budget") or budget,
            findings=views,
            errors=errors,
            metadata={
                "severity_threshold": options.severity_threshold.value,
                "all_findings_count": len(result.findings),
                "reported_findings_count": len(views),
                "scanner_issues": scanner_issues,
                "manifest_issues": coverage["manifest"]["issues"],
                "model_calls": 0,
                "verification": "not_run",
            },
        )
        return LocalAuditOutcome(
            envelope=envelope,
            exit_code=_exit_code(
                result.status,
                bool(views),
                strict=options.strict,
                scanner_status=coverage.get("status"),
            ),
        )

    def doctor(self, semgrep_executable: str | Path = "semgrep") -> DoctorEnvelope:
        rules = bundled_semgrep_rules()
        checks = [
            DoctorCheck(
                name="python",
                status="ok",
                message=platform.python_version(),
                metadata={"implementation": platform.python_implementation()},
            )
        ]
        if rules:
            checks.append(
                DoctorCheck(
                    name="rules",
                    status="ok",
                    message=f"{len(rules)} bundled rule file(s)",
                    metadata={
                        "files": [rule.name for rule in rules],
                        "rule_set_hash": _rules_hash(rules),
                    },
                )
            )
        else:
            checks.append(
                DoctorCheck(
                    name="rules",
                    status="error",
                    message="bundled Semgrep rules are missing",
                )
            )
        try:
            scanner = SemgrepScanner(semgrep_executable, rules=rules)
            version = scanner.version()
            checks.append(
                DoctorCheck(
                    name="semgrep",
                    status="ok",
                    message=f"Semgrep {version}",
                    metadata={
                        "expected_version": SEMGREP_VERSION,
                        "executable": scanner.executable.name,
                    },
                )
            )
        except (OSError, RuntimeError, ValueError) as exc:
            checks.append(
                DoctorCheck(
                    name="semgrep",
                    status="error",
                    message=_safe_message(exc),
                    metadata={"expected_version": SEMGREP_VERSION},
                )
            )
        status = "error" if any(check.status == "error" for check in checks) else "ok"
        return DoctorEnvelope(status=status, checks=checks)


_SEVERITY_RANK = {
    Severity.INFO: 1,
    Severity.LOW: 2,
    Severity.MEDIUM: 3,
    Severity.HIGH: 4,
    Severity.CRITICAL: 5,
}


def _severity_rank(severity: Severity) -> int:
    return _SEVERITY_RANK[severity]


def _exit_code(
    status: AuditStatus,
    has_findings: bool,
    *,
    strict: bool,
    scanner_status: Any = None,
) -> int:
    if status is AuditStatus.FAILED:
        return 3 if strict and scanner_status == "error" else 5
    if status in {AuditStatus.PARTIAL, AuditStatus.CANCELLED}:
        return 4
    return 1 if has_findings else 0
