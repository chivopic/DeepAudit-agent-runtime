"""Command-line boundary for the dependency-light package."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from deepaudit_cli import __version__
from deepaudit_cli.domain import Severity
from deepaudit_cli.manifest import resolve_workspace
from deepaudit_cli.pipeline import (
    AuditOptions,
    ConfigurationError,
    EnvironmentError,
    audit,
    doctor,
)
from deepaudit_cli.report import json_text, render_audit, render_doctor
from deepaudit_cli.scanner import sanitize


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="deepaudit",
        description="Evidence-backed local security auditing for Python projects.",
    )
    root.add_argument("--version", action="version", version=f"deepaudit {__version__}")
    commands = root.add_subparsers(dest="command")
    audit_parser = commands.add_parser("audit", help="audit a local workspace")
    audit_parser.add_argument("path", nargs="?", default=".")
    audit_parser.add_argument(
        "--semgrep", default=os.environ.get("DEEPAUDIT_SEMGREP_PATH", "semgrep")
    )
    audit_parser.add_argument("--format", choices=("terminal", "json"), default="terminal")
    audit_parser.add_argument("--out", type=Path)
    audit_parser.add_argument(
        "--severity",
        choices=tuple(severity.value for severity in Severity),
        default=Severity.MEDIUM.value,
    )
    audit_parser.add_argument("--include", action="append", default=[])
    audit_parser.add_argument("--exclude", action="append", default=[])
    audit_parser.add_argument("--max-files", type=int, default=500)
    audit_parser.add_argument("--max-duration", type=int, default=300)
    audit_parser.add_argument("--max-results", type=int, default=2000)
    audit_parser.add_argument("--max-target-bytes", type=int, default=1024 * 1024)
    doctor_parser = commands.add_parser("doctor", help="check the local audit runtime")
    doctor_parser.add_argument(
        "--semgrep", default=os.environ.get("DEEPAUDIT_SEMGREP_PATH", "semgrep")
    )
    doctor_parser.add_argument("--format", choices=("terminal", "json"), default="terminal")
    return root


def _validate_output_path(output: Path, workspace: Path) -> Path:
    expanded = output.expanduser()
    if expanded.is_symlink():
        raise ValueError("--out must not be a symbolic link")
    parent = expanded.parent.resolve(strict=True)
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


def _audit_command(args: argparse.Namespace) -> int:
    workspace = resolve_workspace(Path(args.path))
    output = _validate_output_path(args.out, workspace) if args.out else None
    outcome = audit(
        AuditOptions(
            workspace=workspace,
            semgrep_executable=args.semgrep,
            severity_threshold=Severity(args.severity),
            include=tuple(args.include),
            exclude=tuple(args.exclude),
            max_files=args.max_files,
            max_duration_seconds=args.max_duration,
            max_results=args.max_results,
            max_target_bytes=args.max_target_bytes,
        )
    )
    if output:
        _atomic_write(output, json_text(outcome.envelope))
    sys.stdout.write(
        json_text(outcome.envelope)
        if args.format == "json"
        else render_audit(outcome.envelope)
    )
    return outcome.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    command_parser = parser()
    args = command_parser.parse_args(argv)
    try:
        if args.command == "audit":
            return _audit_command(args)
        if args.command == "doctor":
            envelope, exit_code = doctor(args.semgrep)
            sys.stdout.write(
                json_text(envelope) if args.format == "json" else render_doctor(envelope)
            )
            return exit_code
        command_parser.print_help(sys.stderr)
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("deepaudit: cancelled\n")
        return 130
    except (ConfigurationError, OSError, ValueError) as exc:
        sys.stderr.write(f"deepaudit: {sanitize(exc)}\n")
        return 2
    except EnvironmentError as exc:
        sys.stderr.write(f"deepaudit: {sanitize(exc)}\n")
        return 3
    except Exception as exc:  # noqa: BLE001 - stable CLI boundary
        sys.stderr.write(f"deepaudit: internal error ({sanitize(type(exc).__name__)})\n")
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
