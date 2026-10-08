#!/usr/bin/env python3
"""Run the repeatable CLI feasibility vertical slice.

This is a Gate 0 probe, not the product scanner adapter. It deliberately uses
the shared Finding/Evidence domain models while keeping Semgrep execution
argv-only, offline, environment-sanitised, bounded, and workspace read-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.agent.domain import (
    Evidence,
    Finding,
    FindingStatus,
    Severity,
    SourceLocation,
    VerificationStatus,
    fingerprint_components,
)

EXPECTED_SEMGREP_VERSION = "1.173.0"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_OUTPUT_LIMIT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class ProcessResult:
    """Bounded subprocess result used by the feasibility probe."""

    argv: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool = False
    output_limited: bool = False


def build_scanner_env(temp_root: Path, binary: Path) -> dict[str, str]:
    """Build an explicit allowlist; no caller credentials are inherited."""
    return {
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


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:  # pragma: no cover - Windows fallback
            process.kill()
    except PermissionError:
        # Some managed environments forbid signalling a process group even
        # when the direct child is owned by the caller.
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
    """Run fixed argv without a shell, bounding time and combined output."""
    if not argv or not Path(argv[0]).is_absolute():
        raise ValueError("scanner executable must be an absolute path")
    if timeout_seconds <= 0 or output_limit_bytes <= 0:
        raise ValueError("timeout and output limit must be positive")

    started = time.perf_counter()
    process = subprocess.Popen(  # noqa: S603 - fixed argv and resolved executable
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
                # Pipes may still have a final short chunk; the selector drains it.
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


def resolve_executable(value: str) -> Path:
    """Resolve once so a later PATH change cannot select another executable."""
    candidate = Path(value).expanduser()
    if candidate.parent != Path("."):
        resolved = candidate.resolve(strict=True)
    else:
        located = shutil.which(value)
        if located is None:
            raise FileNotFoundError(f"Semgrep executable not found: {value}")
        resolved = Path(located).resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise PermissionError(f"Semgrep path is not executable: {resolved}")
    return resolved


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_digest(root: Path) -> str:
    """Hash names, symlink destinations, and regular-file contents."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"link\0")
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest.update(b"file\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        elif path.is_dir():
            digest.update(b"dir\0")
        digest.update(b"\0")
    return digest.hexdigest()


def _path_from_result(raw_path: str, *, cwd: Path, workspace: Path) -> tuple[Path, str]:
    candidate = Path(raw_path)
    absolute = candidate.resolve() if candidate.is_absolute() else (cwd / candidate).resolve()
    try:
        relative = absolute.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise ValueError(f"Semgrep returned a path outside the workspace: {raw_path}") from exc
    if absolute.is_symlink() or not absolute.is_file():
        raise ValueError(f"Semgrep result is not a regular workspace file: {raw_path}")
    return absolute, relative


def _snippet(path: Path, start_line: int, end_line: int) -> tuple[str, str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = "\n".join(lines[start_line - 1 : end_line])
    return selected, hashlib.sha256(selected.encode("utf-8")).hexdigest()


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
    prefix = str(first).split(":", 1)[0].strip()
    return prefix or None


def normalize_semgrep(
    payload: Mapping[str, Any],
    *,
    cwd: Path,
    workspace: Path,
    scanner_version: str,
    rule_hash: str,
) -> list[Finding]:
    """Create stable E0/candidate objects from Semgrep OSS JSON."""
    findings: list[Finding] = []
    for item in payload.get("results", []):
        extra = item.get("extra") or {}
        metadata = extra.get("metadata") or {}
        start = item.get("start") or {}
        end = item.get("end") or start
        start_line = int(start.get("line") or 1)
        end_line = int(end.get("line") or start_line)
        absolute, relative = _path_from_result(
            str(item.get("path") or ""), cwd=cwd, workspace=workspace
        )
        snippet, code_hash = _snippet(absolute, start_line, end_line)
        rule_id = str(item.get("check_id") or "unknown-rule")
        message = str(extra.get("message") or rule_id)
        title = rule_id.rsplit(".", 1)[-1].replace("-", " ").title()
        cwe_id = _cwe_id(metadata)
        fingerprint = fingerprint_components(
            file_path=relative,
            start_line=start_line,
            title=title,
            rule_id=rule_id,
            cwe_id=cwe_id,
        )
        evidence_id = hashlib.sha256(f"{fingerprint}|{code_hash}".encode()).hexdigest()[:12]
        evidence = Evidence(
            id=f"ev_{evidence_id}",
            kind="code",
            summary=f"Semgrep rule {rule_id} matched this source span.",
            location=SourceLocation(
                file_path=relative,
                start_line=start_line,
                end_line=end_line,
                column_start=int(start.get("col") or 0),
                column_end=int(end.get("col") or 0),
                code_hash=code_hash,
            ),
            snippet=snippet,
            confidence=0.9,
            metadata={
                "evidence_level": "E0",
                "rule_hash": rule_hash,
                "scanner": "semgrep",
                "scanner_version": scanner_version,
            },
        )
        severity = _severity(str(extra.get("severity") or "INFO"))
        findings.append(
            Finding(
                id=f"fnd_{fingerprint[:12]}",
                title=title,
                description=message,
                severity=severity,
                status=FindingStatus.NEW,
                verification_status=VerificationStatus.NOT_RUN,
                category=str(metadata.get("category") or "security"),
                cwe_id=cwe_id,
                location=evidence.location,
                evidence=[evidence],
                confidence=0.9,
                risk_score={
                    Severity.HIGH: 80.0,
                    Severity.MEDIUM: 55.0,
                    Severity.LOW: 30.0,
                    Severity.INFO: 10.0,
                }[severity],
                analyzer="semgrep",
                rule_id=rule_id,
                fingerprint=fingerprint,
                metadata={
                    "evidence_level": "E0",
                    "finding_state": "candidate",
                    "rule_hash": rule_hash,
                },
            )
        )
    return findings


def _stable_finding_payload(finding: Finding) -> dict[str, Any]:
    """Exclude wall-clock fields so repeated fixture output is comparable."""
    return finding.model_dump(
        mode="json",
        exclude={"created_at", "updated_at"},
        exclude_none=True,
    )


def run_probe(semgrep_value: str) -> dict[str, Any]:
    backend_root = Path(__file__).resolve().parents[1]
    fixture_root = backend_root / "tests" / "fixtures" / "cli_gate0"
    workspace = (fixture_root / "target").resolve(strict=True)
    rule = (fixture_root / "rules" / "python-dangerous-eval.yml").resolve(strict=True)
    target = (workspace / "app.py").resolve(strict=True)
    semgrep = resolve_executable(semgrep_value)

    before = workspace_digest(workspace)
    with tempfile.TemporaryDirectory(prefix="deepaudit-gate0-") as temp_name:
        temp_root = Path(temp_name)
        scanner_env = build_scanner_env(temp_root, semgrep)
        version_result = run_capped(
            [str(semgrep), "--version"],
            cwd=temp_root,
            env=scanner_env,
            timeout_seconds=10.0,
            output_limit_bytes=64 * 1024,
        )
        scanner_version = version_result.stdout.decode("utf-8", errors="replace").strip()
        if version_result.returncode != 0 or scanner_version != EXPECTED_SEMGREP_VERSION:
            raise RuntimeError(
                f"expected Semgrep {EXPECTED_SEMGREP_VERSION}, got {scanner_version!r}"
            )

        argv = [
            str(semgrep),
            "scan",
            "--config",
            str(rule),
            "--json",
            "--metrics",
            "off",
            "--disable-version-check",
            "--no-git-ignore",
            "--no-rewrite-rule-ids",
            "--quiet",
            str(target),
        ]
        scan_result = run_capped(
            argv,
            cwd=temp_root,
            env=scanner_env,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            output_limit_bytes=DEFAULT_OUTPUT_LIMIT_BYTES,
        )

    after = workspace_digest(workspace)
    if scan_result.timed_out:
        raise TimeoutError("Semgrep fixture scan timed out")
    if scan_result.output_limited:
        raise RuntimeError("Semgrep fixture scan exceeded the output limit")
    if scan_result.returncode != 0:
        error = scan_result.stderr.decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"Semgrep fixture scan failed: {error}")

    raw = json.loads(scan_result.stdout)
    if raw.get("errors"):
        raise RuntimeError(f"Semgrep returned errors: {raw['errors']}")
    rule_hash = file_sha256(rule)
    findings = normalize_semgrep(
        raw,
        cwd=temp_root,
        workspace=workspace,
        scanner_version=scanner_version,
        rule_hash=rule_hash,
    )
    scanned_files = raw.get("paths", {}).get("scanned", [])
    expected = len(findings) == 1 and len(scanned_files) == 1
    expected = expected and findings[0].location is not None
    expected = expected and findings[0].location.start_line == 5
    expected = expected and findings[0].rule_id == "deepaudit.python.dangerous-eval"
    unchanged = before == after
    if not expected or not unchanged:
        raise RuntimeError(
            f"fixture contract failed: findings={len(findings)}, unchanged={unchanged}"
        )

    max_memory = raw.get("time", {}).get("max_memory_bytes")
    return {
        "schema_version": "deepaudit.cli.gate0.v1",
        "status": "go",
        "scanner": {
            "name": "semgrep",
            "version": scanner_version,
            "executable": str(semgrep),
            "rule_id": findings[0].rule_id,
            "rule_sha256": rule_hash,
            "remote_rules": False,
            "forwarded_environment_keys": sorted(scanner_env),
        },
        "execution": {
            "shell": False,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
            "output_limit_bytes": DEFAULT_OUTPUT_LIMIT_BYTES,
            "stdout_bytes": len(scan_result.stdout),
            "stderr_bytes": len(scan_result.stderr),
            "duration_seconds": round(scan_result.duration_seconds, 6),
            "semgrep_max_memory_bytes": max_memory,
            "workspace_sha256_before": before,
            "workspace_sha256_after": after,
            "workspace_unchanged": unchanged,
        },
        "coverage": {
            "requested_files": 1,
            "scanned_files": len(scanned_files),
            "finding_count": len(findings),
            "status": "complete",
        },
        "findings": [_stable_finding_payload(finding) for finding in findings],
        "notes": [
            "Evidence Level and Finding State are provisional metadata until Phase 1 extends the shared domain.",
            "The snippet and stable fingerprint are computed locally because Semgrep OSS redacts them.",
        ],
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--semgrep",
        default="semgrep",
        help=f"Semgrep {EXPECTED_SEMGREP_VERSION} executable name or path",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run_probe(args.semgrep)
    except Exception as exc:  # noqa: BLE001 - probe emits a structured No-Go result
        print(
            json.dumps(
                {
                    "schema_version": "deepaudit.cli.gate0.v1",
                    "status": "no_go",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
