"""Governed static scanner protocol and local Semgrep adapter.

Graph and application code consume ``ScannerProtocol`` only. This module is
the sole subprocess boundary for CLI-1 scanners: argv-only, resolved binaries,
local rules, a sanitised environment, bounded output, and workspace path jail.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.services.agent.domain import (
    CandidateFinding,
    Evidence,
    EvidenceLevel,
    FindingState,
    Severity,
    SourceLocation,
)

SEMGREP_VERSION = "1.173.0"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_OUTPUT_LIMIT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_TARGET_BYTES = 1 * 1024 * 1024
DEFAULT_MAX_RESULTS = 2_000
_SYSTEM_CA_CANDIDATES = (
    Path("/etc/ssl/cert.pem"),  # macOS and some Linux distributions
    Path("/etc/ssl/certs/ca-certificates.crt"),  # Debian/Ubuntu
    Path("/etc/pki/tls/certs/ca-bundle.crt"),  # Fedora/RHEL
)


class ScannerStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass(frozen=True)
class ScannerIssue:
    code: str
    message: str
    path: str | None = None
    retriable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "retriable": self.retriable,
        }


@dataclass(frozen=True)
class ScannerRequest:
    workspace_root: Path
    relative_files: tuple[str, ...]
    max_results: int = DEFAULT_MAX_RESULTS
    max_target_bytes: int = DEFAULT_MAX_TARGET_BYTES

    def __post_init__(self) -> None:
        if not self.workspace_root.is_absolute():
            raise ValueError("workspace_root must be absolute")
        if self.max_results <= 0 or self.max_target_bytes <= 0:
            raise ValueError("scanner limits must be positive")


@dataclass(frozen=True)
class ScannerResult:
    scanner: str
    status: ScannerStatus
    version: str | None
    rule_set_hash: str
    requested_files: int
    scanned_files: int
    candidates: tuple[CandidateFinding, ...] = ()
    scanned_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    issues: tuple[ScannerIssue, ...] = ()
    duration_seconds: float = 0.0
    max_memory_bytes: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def coverage_dict(self) -> dict[str, Any]:
        return {
            "scanner": self.scanner,
            "status": self.status.value,
            "requested_files": self.requested_files,
            "scanned_files": self.scanned_files,
            "skipped_files": len(self.skipped_paths),
            "skipped_paths": list(self.skipped_paths),
            "duration_seconds": round(self.duration_seconds, 6),
            "version": self.version,
            "rule_set_hash": self.rule_set_hash,
            "max_memory_bytes": self.max_memory_bytes,
        }


@runtime_checkable
class ScannerProtocol(Protocol):
    name: str

    async def scan(self, request: ScannerRequest) -> ScannerResult: ...


@dataclass(frozen=True)
class ProcessResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool = False
    output_limited: bool = False


_ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_OSC_RE = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)


def sanitize_untrusted(value: Any, *, max_length: int = 4_000) -> str:
    """Remove terminal controls from paths, tool messages, and stderr."""
    text = str(value or "")
    text = _ANSI_OSC_RE.sub("", text)
    text = _ANSI_CSI_RE.sub("", text)
    cleaned: list[str] = []
    cleaned_length = 0
    for character in text:
        code = ord(character)
        if character in {"\n", "\r", "\t"} or (code >= 32 and code != 127):
            part = character
        else:
            part = f"\\x{code:02x}"
        cleaned.append(part)
        cleaned_length += len(part)
        if cleaned_length >= max_length:
            break
    result = "".join(cleaned)
    if len(result) > max_length:
        result = result[:max_length]
    return result


def resolve_executable(value: str | Path) -> Path:
    """Resolve the scanner once to prevent later PATH substitution."""
    raw = str(value)
    candidate = Path(raw).expanduser()
    if candidate.parent != Path("."):
        resolved = candidate.resolve(strict=True)
    else:
        located = shutil.which(raw)
        if located is None:
            raise FileNotFoundError(f"Semgrep executable not found: {raw}")
        resolved = Path(located).resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise PermissionError(f"Semgrep path is not executable: {resolved}")
    return resolved


def build_scanner_env(temp_root: Path, binary: Path) -> dict[str, str]:
    """Return the complete child environment; caller secrets are not copied."""
    environment = {
        "HOME": str(temp_root),
        "TMPDIR": str(temp_root),
        "PATH": os.pathsep.join((str(binary.parent), "/usr/bin", "/bin")),
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
        "SEMGREP_ENABLE_VERSION_CHECK": "0",
        "SEMGREP_SEND_METRICS": "off",
    }
    # Semgrep's native telemetry client initializes an X509 store even when
    # metrics and version checks are disabled. Supply a known system trust
    # store without inheriting user-controlled SSL environment variables.
    for candidate in _SYSTEM_CA_CANDIDATES:
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_file():
            environment["SSL_CERT_FILE"] = str(resolved)
            break
    return environment


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows fallback
            process.kill()
    except PermissionError:
        process.kill()
    except ProcessLookupError:
        pass


def run_capped(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
    output_limit_bytes: int,
) -> ProcessResult:
    """Run an absolute executable without shell, bounding time and output."""
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError("scanner executable must be an absolute path")
    if timeout_seconds <= 0 or output_limit_bytes <= 0:
        raise ValueError("timeout and output limit must be positive")

    started = time.perf_counter()
    process = subprocess.Popen(  # noqa: S603 - resolved executable and fixed argv
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None

    streams = {"stdout": bytearray(), "stderr": bytearray()}
    selector = selectors.DefaultSelector()
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, data=name)

    timed_out = False
    output_limited = False
    deadline = started + timeout_seconds
    try:
        while selector.get_map():
            if time.perf_counter() >= deadline:
                timed_out = True
                _kill_process_group(process)
            for key, _ in selector.select(timeout=0.05):
                try:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.data].extend(chunk)
                if sum(map(len, streams.values())) > output_limit_bytes:
                    output_limited = True
                    _kill_process_group(process)
            if (timed_out or output_limited) and process.poll() is not None:
                continue
    finally:
        selector.close()
        _kill_process_group(process)
        process.wait()

    stdout = bytes(streams["stdout"][:output_limit_bytes])
    remaining = max(0, output_limit_bytes - len(stdout))
    stderr = bytes(streams["stderr"][:remaining])
    return ProcessResult(
        argv=tuple(argv),
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=time.perf_counter() - started,
        timed_out=timed_out,
        output_limited=output_limited,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _combined_rule_hash(rules: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for rule in sorted(rules, key=lambda item: item.name):
        digest.update(rule.name.encode())
        digest.update(b"\0")
        digest.update(_file_sha256(rule).encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_targets(
    request: ScannerRequest,
) -> tuple[list[Path], list[str], list[ScannerIssue]]:
    root = request.workspace_root.resolve(strict=True)
    targets: list[Path] = []
    skipped: list[str] = []
    issues: list[ScannerIssue] = []
    for raw_relative in request.relative_files:
        relative = str(raw_relative).strip().replace("\\", "/")
        path_parts = Path(relative).parts
        if (
            not relative
            or Path(relative).is_absolute()
            or ".." in path_parts
            or (len(relative) > 1 and relative[1] == ":")
        ):
            skipped.append(sanitize_untrusted(relative))
            issues.append(
                ScannerIssue("unsafe_path", "rejected unsafe relative path", path=relative)
            )
            continue
        unresolved = root / relative
        current = root
        symlinked = False
        for part in path_parts:
            current /= part
            if current.is_symlink():
                symlinked = True
                break
        if symlinked:
            skipped.append(relative)
            issues.append(ScannerIssue("symlink", "symbolic links are not scanned", path=relative))
            continue
        try:
            target = unresolved.resolve(strict=True)
            target.relative_to(root)
        except (FileNotFoundError, ValueError):
            skipped.append(relative)
            issues.append(
                ScannerIssue(
                    "outside_workspace", "path is missing or outside workspace", path=relative
                )
            )
            continue
        if not target.is_file():
            skipped.append(relative)
            issues.append(
                ScannerIssue("not_regular_file", "target is not a regular file", path=relative)
            )
            continue
        if target.stat().st_size > request.max_target_bytes:
            skipped.append(relative)
            issues.append(
                ScannerIssue(
                    "target_too_large",
                    f"target exceeds {request.max_target_bytes} bytes",
                    path=relative,
                )
            )
            continue
        targets.append(target)
    return targets, skipped, issues


def _relative_result_path(raw_path: str, *, cwd: Path, workspace: Path) -> tuple[Path, str]:
    candidate = Path(raw_path)
    absolute = candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve()
    try:
        relative = absolute.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise ValueError(f"Semgrep returned path outside workspace: {raw_path}") from exc
    unresolved = workspace / relative
    if unresolved.is_symlink() or not absolute.is_file():
        raise ValueError(f"Semgrep result is not a regular workspace file: {raw_path}")
    return absolute, relative


def _source_span(path: Path, start_line: int, end_line: int) -> tuple[str, str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = "\n".join(lines[start_line - 1 : end_line])
    return selected, hashlib.sha256(selected.encode()).hexdigest()


def _severity(value: str) -> Severity:
    return {
        "ERROR": Severity.HIGH,
        "WARNING": Severity.MEDIUM,
        "INFO": Severity.LOW,
    }.get(value.upper(), Severity.INFO)


def _cwe_id(metadata: Mapping[str, Any]) -> str | None:
    values = metadata.get("cwe")
    first = values[0] if isinstance(values, list) and values else values
    if not first:
        return None
    return str(first).split(":", 1)[0].strip() or None


def _confidence(metadata: Mapping[str, Any]) -> float:
    return {"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.5}.get(
        str(metadata.get("confidence") or "MEDIUM").upper(), 0.7
    )


def _fingerprint(
    *,
    rule_id: str,
    cwe_id: str | None,
    relative_path: str,
    start_line: int,
    end_line: int,
    code_hash: str,
) -> str:
    raw = "|".join(
        (
            "deepaudit-finding-v1",
            rule_id.strip().lower(),
            (cwe_id or "").strip().lower(),
            relative_path.strip().lower(),
            f"{start_line}:{end_line}",
            code_hash,
        )
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def normalize_semgrep_results(
    payload: Mapping[str, Any],
    *,
    cwd: Path,
    workspace: Path,
    scanner_version: str,
    rule_set_hash: str,
) -> list[CandidateFinding]:
    candidates: list[CandidateFinding] = []
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        raise ValueError("Semgrep results must be a list")
    for item in raw_results:
        if not isinstance(item, Mapping):
            raise ValueError("Semgrep result entries must be objects")
        extra = item.get("extra") or {}
        if not isinstance(extra, Mapping):
            raise ValueError("Semgrep result extra must be an object")
        metadata = extra.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise ValueError("Semgrep result metadata must be an object")
        start = item.get("start") or {}
        end = item.get("end") or start
        if not isinstance(start, Mapping) or not isinstance(end, Mapping):
            raise ValueError("Semgrep result locations must be objects")
        start_line = int(start.get("line") or 1)
        end_line = int(end.get("line") or start_line)
        absolute, relative = _relative_result_path(
            str(item.get("path") or ""), cwd=cwd, workspace=workspace
        )
        snippet, code_hash = _source_span(absolute, start_line, end_line)
        rule_id = str(item.get("check_id") or "unknown-rule")
        cwe_id = _cwe_id(metadata)
        fingerprint = _fingerprint(
            rule_id=rule_id,
            cwe_id=cwe_id,
            relative_path=relative,
            start_line=start_line,
            end_line=end_line,
            code_hash=code_hash,
        )
        title = sanitize_untrusted(
            metadata.get("title") or rule_id.rsplit(".", 1)[-1].replace("-", " ").title()
        )
        message = sanitize_untrusted(extra.get("message") or rule_id)
        confidence = _confidence(metadata)
        location = SourceLocation(
            file_path=relative,
            start_line=start_line,
            end_line=end_line,
            column_start=int(start.get("col") or 0),
            column_end=int(end.get("col") or 0),
            code_hash=code_hash,
        )
        evidence = Evidence(
            id=f"ev_{hashlib.sha256(f'{fingerprint}|{code_hash}'.encode()).hexdigest()[:12]}",
            kind="code",
            summary=f"Semgrep rule {rule_id} matched this source span.",
            location=location,
            snippet=snippet,
            level=EvidenceLevel.E0,
            confidence=confidence,
            metadata={
                "scanner": "semgrep",
                "scanner_version": scanner_version,
                "rule_set_hash": rule_set_hash,
            },
        )
        candidates.append(
            CandidateFinding(
                id=f"cand_{fingerprint[:12]}",
                title=title,
                description=message,
                severity=_severity(str(extra.get("severity") or "INFO")),
                category=str(metadata.get("category") or "security"),
                cwe_id=cwe_id,
                owasp=str(metadata.get("owasp") or "") or None,
                location=location,
                evidence=[evidence],
                evidence_level=EvidenceLevel.E0,
                finding_state=FindingState.CANDIDATE,
                confidence=confidence,
                analyzer="semgrep",
                rule_id=rule_id,
                fingerprint=fingerprint,
                metadata={"rule_set_hash": rule_set_hash},
            )
        )
    return candidates


class SemgrepScanner:
    """Fixed-version Semgrep OSS adapter using only local rule files."""

    name = "semgrep"

    def __init__(
        self,
        executable: str | Path,
        *,
        rules: Sequence[Path],
        expected_version: str = SEMGREP_VERSION,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        output_limit_bytes: int = DEFAULT_OUTPUT_LIMIT_BYTES,
    ) -> None:
        self.executable = resolve_executable(executable)
        self.rules = tuple(self._validate_rule(rule) for rule in rules)
        if not self.rules:
            raise ValueError("at least one local Semgrep rule file is required")
        self.expected_version = expected_version
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.rule_set_hash = _combined_rule_hash(self.rules)
        self._version: str | None = None

    @staticmethod
    def _validate_rule(rule: Path) -> Path:
        if "://" in str(rule):
            raise ValueError("remote Semgrep rules are not allowed")
        expanded = rule.expanduser()
        if expanded.is_symlink():
            raise ValueError(f"Semgrep rule must not be a symlink: {expanded}")
        resolved = expanded.resolve(strict=True)
        if not resolved.is_file():
            raise ValueError(f"Semgrep rule must be a regular local file: {resolved}")
        if resolved.suffix.lower() not in {".yml", ".yaml"}:
            raise ValueError(f"Semgrep rule must be YAML: {resolved}")
        return resolved

    def version(self) -> str:
        if self._version is not None:
            return self._version
        with tempfile.TemporaryDirectory(prefix="deepaudit-semgrep-version-") as temp_name:
            temp_root = Path(temp_name)
            result = run_capped(
                [str(self.executable), "--version"],
                cwd=temp_root,
                env=build_scanner_env(temp_root, self.executable),
                timeout_seconds=min(10.0, self.timeout_seconds),
                output_limit_bytes=min(64 * 1024, self.output_limit_bytes),
            )
        version = result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode != 0 or result.timed_out or result.output_limited:
            raise RuntimeError("unable to determine Semgrep version")
        if version != self.expected_version:
            raise RuntimeError(f"expected Semgrep {self.expected_version}, got {version!r}")
        self._version = version
        return version

    async def scan(self, request: ScannerRequest) -> ScannerResult:
        return await asyncio.to_thread(self._scan_sync, request)

    def _scan_sync(self, request: ScannerRequest) -> ScannerResult:
        started = time.perf_counter()
        workspace = request.workspace_root.resolve(strict=True)
        targets, skipped, target_issues = _safe_targets(request)
        version = self.version()
        if not targets:
            status = ScannerStatus.PARTIAL if skipped else ScannerStatus.COMPLETE
            return ScannerResult(
                scanner=self.name,
                status=status,
                version=version,
                rule_set_hash=self.rule_set_hash,
                requested_files=len(request.relative_files),
                scanned_files=0,
                skipped_paths=tuple(skipped),
                issues=tuple(target_issues),
                duration_seconds=time.perf_counter() - started,
            )

        with tempfile.TemporaryDirectory(prefix="deepaudit-semgrep-") as temp_name:
            temp_root = Path(temp_name)
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
                    *[str(target) for target in targets],
                )
            )
            result = run_capped(
                argv,
                cwd=temp_root,
                env=build_scanner_env(temp_root, self.executable),
                timeout_seconds=self.timeout_seconds,
                output_limit_bytes=self.output_limit_bytes,
            )
            if result.timed_out:
                issue = ScannerIssue("timeout", "Semgrep exceeded the scan timeout", retriable=True)
                return self._failed_result(request, version, skipped, target_issues, issue, started)
            if result.output_limited:
                issue = ScannerIssue("output_limit", "Semgrep exceeded the output limit")
                return self._failed_result(request, version, skipped, target_issues, issue, started)
            if result.returncode != 0:
                stderr = sanitize_untrusted(result.stderr.decode("utf-8", errors="replace"))
                issue = ScannerIssue("process_failed", stderr or "Semgrep process failed")
                return self._failed_result(request, version, skipped, target_issues, issue, started)
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                issue = ScannerIssue("invalid_json", f"Semgrep returned invalid JSON: {exc}")
                return self._failed_result(request, version, skipped, target_issues, issue, started)
            if not isinstance(payload, Mapping):
                issue = ScannerIssue("invalid_schema", "Semgrep JSON root must be an object")
                return self._failed_result(request, version, skipped, target_issues, issue, started)

            issues = list(target_issues)
            raw_errors = payload.get("errors", [])
            if not isinstance(raw_errors, list):
                issue = ScannerIssue("invalid_schema", "Semgrep errors must be a list")
                return self._failed_result(request, version, skipped, issues, issue, started)
            for raw_error in raw_errors:
                issues.append(
                    ScannerIssue(
                        "semgrep_error",
                        sanitize_untrusted(
                            (raw_error.get("message") or raw_error)
                            if isinstance(raw_error, Mapping)
                            else raw_error
                        ),
                    )
                )
            try:
                candidates = normalize_semgrep_results(
                    payload,
                    cwd=temp_root,
                    workspace=workspace,
                    scanner_version=version,
                    rule_set_hash=self.rule_set_hash,
                )
            except (AttributeError, KeyError, OSError, TypeError, ValueError) as exc:
                issue = ScannerIssue("unsafe_result", sanitize_untrusted(exc))
                return self._failed_result(request, version, skipped, issues, issue, started)

        truncated = len(candidates) > request.max_results
        candidates = candidates[: request.max_results]
        if truncated:
            issues.append(
                ScannerIssue(
                    "result_limit",
                    f"Semgrep results exceeded limit {request.max_results}",
                )
            )
        expected_paths = {target.relative_to(workspace).as_posix() for target in targets}
        try:
            candidate_paths = {
                candidate.location.file_path
                for candidate in candidates
                if candidate.location is not None
            }
            raw_paths = payload.get("paths", {})
            if not isinstance(raw_paths, Mapping):
                raise ValueError("Semgrep paths must be an object")
            raw_scanned = raw_paths.get("scanned", [])
            if not isinstance(raw_scanned, list):
                raise ValueError("Semgrep scanned paths must be a list")
            reported_paths = {
                self._normalize_scanned_path(raw, workspace, temp_root) for raw in raw_scanned
            }
        except (OSError, TypeError, ValueError) as exc:
            issue = ScannerIssue("unsafe_result", sanitize_untrusted(exc))
            return self._failed_result(request, version, skipped, issues, issue, started)
        unexpected_candidates = candidate_paths - expected_paths
        if unexpected_candidates:
            issue = ScannerIssue(
                "unexpected_result_path",
                "Semgrep returned a finding for a file that was not requested",
                path=sorted(unexpected_candidates)[0],
            )
            return self._failed_result(request, version, skipped, issues, issue, started)
        scanned_paths = tuple(sorted(candidate_paths | reported_paths))
        coverage_mismatch = set(scanned_paths) != expected_paths
        if coverage_mismatch:
            missing = sorted(expected_paths - set(scanned_paths))
            unexpected = sorted(set(scanned_paths) - expected_paths)
            issues.append(
                ScannerIssue(
                    "coverage_mismatch",
                    (
                        f"requested {len(expected_paths)} targets but Semgrep reported "
                        f"{len(scanned_paths)}; missing={missing[:5]}, "
                        f"unexpected={unexpected[:5]}"
                    ),
                )
            )
        status = (
            ScannerStatus.PARTIAL
            if skipped or issues or truncated or coverage_mismatch
            else ScannerStatus.COMPLETE
        )
        return ScannerResult(
            scanner=self.name,
            status=status,
            version=version,
            rule_set_hash=self.rule_set_hash,
            requested_files=len(request.relative_files),
            scanned_files=len(scanned_paths),
            candidates=tuple(candidates),
            scanned_paths=scanned_paths,
            skipped_paths=tuple(skipped),
            issues=tuple(issues),
            duration_seconds=result.duration_seconds,
            max_memory_bytes=_max_memory_bytes(payload),
            metadata={
                "stderr": sanitize_untrusted(result.stderr.decode("utf-8", errors="replace")),
                "forwarded_environment_keys": sorted(
                    build_scanner_env(Path("/tmp"), self.executable)
                ),
                "remote_rules": False,
            },
        )

    @staticmethod
    def _normalize_scanned_path(raw: str, workspace: Path, cwd: Path) -> str:
        _, relative = _relative_result_path(str(raw), cwd=cwd, workspace=workspace)
        return relative

    def _failed_result(
        self,
        request: ScannerRequest,
        version: str,
        skipped: Sequence[str],
        existing_issues: Sequence[ScannerIssue],
        issue: ScannerIssue,
        started: float,
    ) -> ScannerResult:
        return ScannerResult(
            scanner=self.name,
            status=ScannerStatus.ERROR,
            version=version,
            rule_set_hash=self.rule_set_hash,
            requested_files=len(request.relative_files),
            scanned_files=0,
            skipped_paths=tuple(skipped),
            issues=(*existing_issues, issue),
            duration_seconds=time.perf_counter() - started,
        )


def bundled_semgrep_rules() -> tuple[Path, ...]:
    rule_dir = Path(__file__).resolve().parents[1] / "rules"
    return tuple(sorted(rule_dir.glob("*.yml")))


def _max_memory_bytes(payload: Mapping[str, Any]) -> int | None:
    timing = payload.get("time", {})
    if not isinstance(timing, Mapping):
        return None
    value = timing.get("max_memory_bytes")
    return value if isinstance(value, int) and value >= 0 else None
