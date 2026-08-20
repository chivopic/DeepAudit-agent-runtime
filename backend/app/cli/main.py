"""Dependency-light CLI-1 commands and presenters."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.services.agent.domain import CliAuditEnvelope, DoctorEnvelope, Severity
from app.services.agent.tooling.scanners import sanitize_untrusted


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deepaudit",
        description="Evidence-backed local security auditing for Python projects.",
    )
    parser.add_argument("--version", action="version", version="deepaudit 0.1.0-dev")
    commands = parser.add_subparsers(dest="command")

    audit = commands.add_parser("audit", help="audit a local workspace")
    audit.add_argument("path", nargs="?", default=".")
    audit.add_argument("--semgrep", default=os.environ.get("DEEPAUDIT_SEMGREP_PATH", "semgrep"))
    audit.add_argument("--format", choices=("terminal", "json"), default="terminal")
    audit.add_argument("--out", type=Path)
    audit.add_argument(
        "--severity",
        choices=tuple(severity.value for severity in Severity),
        default=Severity.MEDIUM.value,
    )
    audit.add_argument("--include", action="append", default=[])
    audit.add_argument("--exclude", action="append", default=[])
    audit.add_argument("--strict", action="store_true")
    audit.add_argument("--max-files", type=int, default=500)
    audit.add_argument("--max-duration", type=int, default=300)
    audit.add_argument("--max-results", type=int, default=2_000)
    audit.add_argument("--max-target-bytes", type=int, default=1 * 1024 * 1024)
    audit.add_argument("--no-color", action="store_true")
    audit.add_argument("--quiet", action="store_true")

    doctor = commands.add_parser("doctor", help="check the local audit runtime")
    doctor.add_argument("--semgrep", default=os.environ.get("DEEPAUDIT_SEMGREP_PATH", "semgrep"))
    doctor.add_argument("--format", choices=("terminal", "json"), default="terminal")
    return parser


def _json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _render_doctor(envelope: DoctorEnvelope) -> str:
    lines = [f"DeepAudit doctor: {envelope.status}"]
    for check in envelope.checks:
        marker = "OK" if check.status == "ok" else "ERROR"
        lines.append(f"[{marker}] {check.name}: {sanitize_untrusted(check.message)}")
    return "\n".join(lines) + "\n"


def _render_audit(envelope: CliAuditEnvelope) -> str:
    coverage = envelope.coverage
    lines = [
        "DeepAudit local security audit",
        f"workspace: {sanitize_untrusted(envelope.scope.root_display)}",
        (
            f"status: {envelope.audit.status.value} | "
            f"coverage: {sanitize_untrusted(coverage.get('status', 'unknown'))} "
            f"({coverage.get('scanned_files', 0)}/{coverage.get('requested_files', 0)} files)"
        ),
        "",
    ]
    if envelope.findings:
        lines.append(f"{len(envelope.findings)} candidate issue(s) at or above threshold")
        for finding in envelope.findings:
            location = finding.location
            place = (
                f"{location.file_path}:{location.start_line}"
                if location and location.start_line
                else location.file_path
                if location
                else "n/a"
            )
            lines.append(
                f"{finding.severity.value.upper():8} {finding.display_id:7} "
                f"{sanitize_untrusted(finding.title, max_length=120)}  "
                f"{sanitize_untrusted(place, max_length=240)}"
            )
    else:
        lines.append("No candidate issues at or above the selected severity threshold.")
    lines.extend(
        (
            "",
            "Results are static scanner signals (E0/candidate), not dynamically verified.",
        )
    )
    issues = [
        *(envelope.metadata.get("scanner_issues") or []),
        *(envelope.metadata.get("manifest_issues") or []),
    ]
    if issues:
        lines.append("Coverage/issues:")
        for issue in issues:
            lines.append(
                f"- {sanitize_untrusted(issue.get('code', 'scanner'))}: "
                f"{sanitize_untrusted(issue.get('message', ''), max_length=300)}"
            )
    return "\n".join(lines) + "\n"


def _validate_output_path(output: Path, workspace: Path) -> Path:
    expanded = output.expanduser()
    if expanded.is_symlink():
        raise ValueError("--out must not be a symbolic link")
    try:
        parent = expanded.parent.resolve(strict=True)
    except OSError as exc:
        raise ValueError("--out parent directory must already exist") from exc
    if not parent.is_dir():
        raise ValueError("--out parent is not a directory")
    target = parent / expanded.name
    try:
        target.relative_to(workspace)
    except ValueError:
        pass
    else:
        raise ValueError("--out must be outside the audited workspace")
    if target.exists() and not target.is_file():
        raise ValueError("--out must be a regular file path")
    return target


def _atomic_write(target: Path, content: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".deepaudit-", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


async def _run_audit(args: argparse.Namespace) -> int:
    from app.services.agent.application.local_audit import (
        CliConfigurationError,
        CliEnvironmentError,
        LocalAuditApplication,
        LocalAuditOptions,
    )

    app = LocalAuditApplication()
    try:
        workspace = app.resolve_workspace(Path(args.path))
        output_target = _validate_output_path(args.out, workspace) if args.out is not None else None
        outcome = await app.audit(
            LocalAuditOptions(
                workspace=workspace,
                semgrep_executable=args.semgrep,
                severity_threshold=Severity(args.severity),
                include_paths=tuple(args.include),
                exclude_paths=tuple(args.exclude),
                strict=args.strict,
                max_files=args.max_files,
                max_duration_seconds=args.max_duration,
                max_results=args.max_results,
                max_target_bytes=args.max_target_bytes,
            )
        )
        output = (
            _json(outcome.envelope) if args.format == "json" else _render_audit(outcome.envelope)
        )
        if output_target is not None:
            _atomic_write(output_target, _json(outcome.envelope))
        sys.stdout.write(output)
        return outcome.exit_code
    except CliConfigurationError as exc:
        sys.stderr.write(f"deepaudit: {sanitize_untrusted(exc)}\n")
        return 2
    except CliEnvironmentError as exc:
        sys.stderr.write(f"deepaudit: {sanitize_untrusted(exc)}\n")
        return 3
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"deepaudit: {sanitize_untrusted(exc)}\n")
        return 2


def _run_doctor(args: argparse.Namespace) -> int:
    from app.services.agent.application.local_audit import LocalAuditApplication

    envelope = LocalAuditApplication().doctor(args.semgrep)
    output = _json(envelope) if args.format == "json" else _render_doctor(envelope)
    sys.stdout.write(output)
    return 0 if envelope.status == "ok" else 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "audit":
        try:
            return asyncio.run(_run_audit(args))
        except KeyboardInterrupt:
            sys.stderr.write("deepaudit: cancelled\n")
            return 130
        except Exception as exc:  # noqa: BLE001 — stable CLI boundary
            sys.stderr.write(
                f"deepaudit: internal error ({sanitize_untrusted(type(exc).__name__)})\n"
            )
            return 5
    if args.command == "doctor":
        try:
            return _run_doctor(args)
        except Exception as exc:  # noqa: BLE001 — stable CLI boundary
            sys.stderr.write(
                f"deepaudit: internal error ({sanitize_untrusted(type(exc).__name__)})\n"
            )
            return 5
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
