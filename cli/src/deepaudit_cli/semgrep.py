"""Exact-version, local-rule Semgrep adapter."""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from deepaudit_cli.domain import (
    Evidence,
    Finding,
    Issue,
    Location,
    ScanResult,
    ScanStatus,
    Severity,
)
from deepaudit_cli.scanner import (
    resolve_executable,
    run_capped,
    sanitize,
    scanner_environment,
)

SEMGREP_VERSION = "1.173.0"
DEFAULT_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024


def bundled_rules() -> tuple[Path, ...]:
    return tuple(sorted((Path(__file__).parent / "rules").glob("*.yml")))


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def combined_rule_hash(rules: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for rule in sorted(rules, key=lambda item: item.name):
        digest.update(rule.name.encode())
        digest.update(b"\0")
        digest.update(_file_hash(rule).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _relative_result_path(raw: str, *, cwd: Path, workspace: Path) -> tuple[Path, str]:
    candidate = Path(raw)
    absolute = candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve()
    try:
        relative = absolute.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise ValueError(f"Semgrep returned path outside workspace: {raw}") from exc
    current = workspace
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"Semgrep returned symbolic-link path: {raw}")
    if not absolute.is_file():
        raise ValueError(f"Semgrep result is not a regular workspace file: {raw}")
    return absolute, relative


def _source_span(path: Path, start_line: int, end_line: int) -> tuple[str, str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    snippet = "\n".join(lines[start_line - 1 : end_line])
    return snippet, hashlib.sha256(snippet.encode()).hexdigest()


def _severity(value: object) -> Severity:
    return {
        "ERROR": Severity.HIGH,
        "WARNING": Severity.MEDIUM,
        "INFO": Severity.LOW,
    }.get(str(value).upper(), Severity.INFO)


def _cwe(metadata: Mapping[str, Any]) -> str | None:
    value = metadata.get("cwe")
    first = value[0] if isinstance(value, list) and value else value
    return str(first).split(":", 1)[0].strip() or None if first else None


def _confidence(metadata: Mapping[str, Any]) -> float:
    return {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}.get(
        str(metadata.get("confidence") or "MEDIUM").upper(), 0.7
    )


def _fingerprint(
    rule_id: str,
    cwe_id: str | None,
    relative: str,
    start_line: int,
    end_line: int,
    code_hash: str,
) -> str:
    value = "|".join(
        (
            "deepaudit-finding-v1",
            rule_id.strip().lower(),
            (cwe_id or "").strip().lower(),
            relative.strip().lower(),
            f"{start_line}:{end_line}",
            code_hash,
        )
    )
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def normalize_results(
    payload: Mapping[str, Any],
    *,
    cwd: Path,
    workspace: Path,
    scanner_version: str,
    rule_set_hash: str,
) -> list[Finding]:
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raise ValueError("Semgrep results must be a list")
    findings: list[Finding] = []
    for item in raw_results:
        if not isinstance(item, Mapping):
            raise ValueError("Semgrep result entries must be objects")
        extra = item.get("extra") or {}
        if not isinstance(extra, Mapping):
            raise ValueError("Semgrep result extra must be an object")
        metadata = extra.get("metadata") or {}
        start = item.get("start") or {}
        end = item.get("end") or start
        if not isinstance(metadata, Mapping) or not isinstance(start, Mapping) or not isinstance(end, Mapping):
            raise ValueError("Semgrep result has an invalid object field")
        start_line = max(1, int(start.get("line") or 1))
        end_line = max(start_line, int(end.get("line") or start_line))
        absolute, relative = _relative_result_path(
            str(item.get("path") or ""), cwd=cwd, workspace=workspace
        )
        snippet, code_hash = _source_span(absolute, start_line, end_line)
        rule_id = sanitize(item.get("check_id") or "unknown-rule", max_length=300)
        cwe_id = _cwe(metadata)
        fingerprint = _fingerprint(
            rule_id, cwe_id, relative, start_line, end_line, code_hash
        )
        confidence = _confidence(metadata)
        location = Location(
            file_path=relative,
            start_line=start_line,
            end_line=end_line,
            column_start=max(0, int(start.get("col") or 0)),
            column_end=max(0, int(end.get("col") or 0)),
            code_hash=code_hash,
        )
        evidence = Evidence(
            id=f"ev_{hashlib.sha256(f'{fingerprint}|{code_hash}'.encode()).hexdigest()[:12]}",
            kind="code",
            summary=f"Semgrep rule {rule_id} matched this source span.",
            location=location,
            snippet=snippet,
            confidence=confidence,
            metadata={
                "scanner": "semgrep",
                "scanner_version": scanner_version,
                "rule_set_hash": rule_set_hash,
            },
        )
        title = sanitize(
            metadata.get("title") or rule_id.rsplit(".", 1)[-1].replace("-", " ").title(),
            max_length=300,
        )
        findings.append(
            Finding(
                id=f"fnd_{fingerprint[:12]}",
                display_id="",
                title=title,
                description=sanitize(extra.get("message") or rule_id),
                severity=_severity(extra.get("severity") or "INFO"),
                category=sanitize(metadata.get("category") or "security", max_length=100),
                cwe_id=cwe_id,
                owasp=sanitize(metadata.get("owasp"), max_length=200) or None,
                location=location,
                evidence=(evidence,),
                confidence=confidence,
                analyzer="semgrep",
                rule_id=rule_id,
                fingerprint=fingerprint,
            )
        )
    deduplicated = {finding.fingerprint: finding for finding in findings}
    ordered = sorted(
        deduplicated.values(),
        key=lambda finding: (
            finding.location.file_path,
            finding.location.start_line,
            finding.rule_id,
        ),
    )
    return [replace(finding, display_id=f"FND-{index:03d}") for index, finding in enumerate(ordered, 1)]


class SemgrepScanner:
    def __init__(
        self,
        executable: str | Path,
        *,
        rules: Sequence[Path] | None = None,
        expected_version: str = SEMGREP_VERSION,
        timeout_seconds: float = 300,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> None:
        self.executable = resolve_executable(executable)
        self.rules = tuple(self._validate_rule(rule) for rule in (rules or bundled_rules()))
        if not self.rules:
            raise ValueError("bundled Semgrep rules are missing")
        self.expected_version = expected_version
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.rule_set_hash = combined_rule_hash(self.rules)
        self._version: str | None = None

    @staticmethod
    def _validate_rule(value: Path) -> Path:
        if "://" in str(value) or value.is_symlink():
            raise ValueError("Semgrep rules must be local regular files")
        rule = value.resolve(strict=True)
        if not rule.is_file() or rule.suffix.lower() not in {".yml", ".yaml"}:
            raise ValueError(f"invalid Semgrep rule: {rule.name}")
        return rule

    def version(self) -> str:
        if self._version is not None:
            return self._version
        with tempfile.TemporaryDirectory(prefix="deepaudit-version-") as temporary:
            root = Path(temporary)
            result = run_capped(
                [str(self.executable), "--version"],
                cwd=root,
                env=scanner_environment(root, self.executable),
                timeout_seconds=min(10, self.timeout_seconds),
                output_limit_bytes=min(64 * 1024, self.output_limit_bytes),
            )
        version = result.stdout.decode(errors="replace").strip()
        if result.returncode or result.timed_out or result.output_limited:
            raise RuntimeError("unable to determine Semgrep version")
        if version != self.expected_version:
            raise RuntimeError(f"expected Semgrep {self.expected_version}, got {version!r}")
        self._version = version
        return version

    def scan(
        self,
        workspace: Path,
        relative_files: tuple[str, ...],
        *,
        max_results: int = 2000,
    ) -> ScanResult:
        started = time.perf_counter()
        root = workspace.resolve(strict=True)
        version = self.version()
        targets: list[Path] = []
        for relative in relative_files:
            candidate = root / relative
            path_parts = Path(relative).parts
            current = root
            symlinked = False
            for part in path_parts:
                current /= part
                if current.is_symlink():
                    symlinked = True
                    break
            if Path(relative).is_absolute() or ".." in path_parts or symlinked:
                raise ValueError(f"unsafe scan target: {relative}")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file():
                raise ValueError(f"scan target is not a file: {relative}")
            targets.append(resolved)
        if not targets:
            return ScanResult(
                status=ScanStatus.COMPLETE,
                version=version,
                rule_set_hash=self.rule_set_hash,
                requested_files=0,
                scanned_files=0,
                duration_seconds=time.perf_counter() - started,
            )
        with tempfile.TemporaryDirectory(prefix="deepaudit-scan-") as temporary:
            run_root = Path(temporary)
            argv = [str(self.executable), "scan"]
            for rule in self.rules:
                argv.extend(("--config", str(rule)))
            argv.extend(
                (
                    "--json",
                    "--metrics",
                    "off",
                    "--disable-version-check",
                    "--no-git-ignore",
                    "--no-rewrite-rule-ids",
                    "--quiet",
                    "--jobs",
                    "1",
                    *(str(target) for target in targets),
                )
            )
            result = run_capped(
                argv,
                cwd=run_root,
                env=scanner_environment(run_root, self.executable),
                timeout_seconds=self.timeout_seconds,
                output_limit_bytes=self.output_limit_bytes,
            )
            if result.timed_out:
                return self._error(relative_files, version, "timeout", "Semgrep exceeded the scan timeout", result.duration_seconds)
            if result.output_limited:
                return self._error(relative_files, version, "output_limit", "Semgrep exceeded the output limit", result.duration_seconds)
            if result.returncode:
                return self._error(relative_files, version, "process_failed", sanitize(result.stderr.decode(errors="replace")) or "Semgrep process failed", result.duration_seconds)
            try:
                payload = json.loads(result.stdout)
                if not isinstance(payload, Mapping):
                    raise ValueError("Semgrep JSON root must be an object")
                findings = normalize_results(
                    payload,
                    cwd=run_root,
                    workspace=root,
                    scanner_version=version,
                    rule_set_hash=self.rule_set_hash,
                )
            except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
                return self._error(relative_files, version, "invalid_result", sanitize(exc), result.duration_seconds)
        issues: list[Issue] = []
        raw_errors = payload.get("errors", [])
        if not isinstance(raw_errors, list):
            return self._error(relative_files, version, "invalid_result", "Semgrep errors must be a list", result.duration_seconds)
        for error in raw_errors:
            message = error.get("message") if isinstance(error, Mapping) else error
            issues.append(Issue("semgrep_error", sanitize(message)))
        truncated = len(findings) > max_results
        if truncated:
            findings = findings[:max_results]
            issues.append(Issue("result_limit", f"Semgrep results exceeded limit {max_results}"))
        expected = set(relative_files)
        raw_paths = payload.get("paths", {})
        if not isinstance(raw_paths, Mapping) or not isinstance(raw_paths.get("scanned", []), list):
            return self._error(relative_files, version, "invalid_result", "Semgrep paths must be an object", result.duration_seconds)
        try:
            reported = {
                _relative_result_path(str(raw), cwd=run_root, workspace=root)[1]
                for raw in raw_paths.get("scanned", [])
            }
        except (OSError, ValueError) as exc:
            return self._error(relative_files, version, "unsafe_result", sanitize(exc), result.duration_seconds)
        finding_paths = {finding.location.file_path for finding in findings}
        scanned = reported | finding_paths
        if finding_paths - expected:
            return self._error(relative_files, version, "unexpected_result_path", "Semgrep returned a finding for an unrequested file", result.duration_seconds)
        if scanned != expected:
            issues.append(Issue("coverage_mismatch", f"requested {len(expected)} files but Semgrep reported {len(scanned)}"))
        return ScanResult(
            status=ScanStatus.PARTIAL if issues else ScanStatus.COMPLETE,
            version=version,
            rule_set_hash=self.rule_set_hash,
            requested_files=len(relative_files),
            scanned_files=len(scanned),
            findings=tuple(findings),
            scanned_paths=tuple(sorted(scanned)),
            issues=tuple(issues),
            duration_seconds=result.duration_seconds,
            max_memory_bytes=_max_memory(payload),
        )

    def _error(
        self,
        relative_files: tuple[str, ...],
        version: str,
        code: str,
        message: str,
        duration: float,
    ) -> ScanResult:
        return ScanResult(
            status=ScanStatus.ERROR,
            version=version,
            rule_set_hash=self.rule_set_hash,
            requested_files=len(relative_files),
            scanned_files=0,
            issues=(Issue(code, message),),
            duration_seconds=duration,
        )


def _max_memory(payload: Mapping[str, Any]) -> int | None:
    timing = payload.get("time", {})
    value = timing.get("max_memory_bytes") if isinstance(timing, Mapping) else None
    return value if isinstance(value, int) and value >= 0 else None
