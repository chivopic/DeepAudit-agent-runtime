"""Contract tests for the governed Semgrep scanner boundary."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.services.agent.domain import EvidenceLevel, FindingState
from app.services.agent.tooling.scanners import (
    ScannerRequest,
    ScannerStatus,
    SemgrepScanner,
    sanitize_untrusted,
)


def _fake_semgrep(tmp_path: Path, *, mode: str = "normal") -> Path:
    executable = tmp_path / f"semgrep-{mode}"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

if "--version" in sys.argv:
    print("1.173.0")
    raise SystemExit(0)
if os.environ.get("AWS_SECRET_ACCESS_KEY"):
    print("credential leaked", file=sys.stderr)
    raise SystemExit(9)
if {mode!r} == "invalid-json":
    print("not-json")
    raise SystemExit(0)
if {mode!r} == "invalid-schema":
    print("[]")
    raise SystemExit(0)
if {mode!r} == "large-output":
    print("x" * 100_000)
    raise SystemExit(0)

targets = [arg for arg in sys.argv[1:] if arg.endswith(".py")]
results = []
for target in targets:
    source = pathlib.Path(target).read_text(encoding="utf-8")
    if "eval(" in source:
        line_number = next(
            index for index, line in enumerate(source.splitlines(), start=1) if "eval(" in line
        )
        results.append({{
            "check_id": "deepaudit.python.dangerous-eval",
            "path": target,
            "start": {{"line": line_number, "col": 12}},
            "end": {{"line": line_number, "col": 23}},
            "extra": {{
                "message": "Avoid eval on untrusted input.",
                "severity": "ERROR",
                "metadata": {{
                    "title": "Dynamic Code Evaluation",
                    "category": "code_injection",
                    "cwe": ["CWE-95: Code Injection"],
                    "confidence": "HIGH"
                }}
            }}
        }})

if {mode!r} == "coverage-gap":
    scanned = []
elif {mode!r} == "coverage-substitute":
    scanned = [str(pathlib.Path(targets[0]).with_name("other.py"))]
else:
    scanned = targets
print(json.dumps({{
    "version": "1.173.0",
    "results": results,
    "errors": [],
    "paths": {{"scanned": scanned}},
    "time": {{"max_memory_bytes": 123456}}
}}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _rule(tmp_path: Path) -> Path:
    rule = tmp_path / "rules.yml"
    rule.write_text("rules: []\n", encoding="utf-8")
    return rule


@pytest.mark.asyncio
async def test_semgrep_scanner_normalizes_and_reports_complete_coverage(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("def run(value):\n    return eval(value)\n", encoding="utf-8")
    (workspace / "safe.py").write_text("safe = True\n", encoding="utf-8")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-leak")
    scanner = SemgrepScanner(_fake_semgrep(tmp_path), rules=[_rule(tmp_path)])

    first = await scanner.scan(
        ScannerRequest(
            workspace_root=workspace.resolve(),
            relative_files=("app.py", "safe.py"),
        )
    )
    second = await scanner.scan(
        ScannerRequest(
            workspace_root=workspace.resolve(),
            relative_files=("app.py", "safe.py"),
        )
    )

    assert first.status is ScannerStatus.COMPLETE
    assert first.requested_files == 2
    assert first.scanned_files == 2
    assert first.scanned_paths == ("app.py", "safe.py")
    assert len(first.candidates) == 1
    finding = first.candidates[0]
    assert finding.fingerprint == second.candidates[0].fingerprint
    assert finding.id == second.candidates[0].id
    assert finding.evidence_level is EvidenceLevel.E0
    assert finding.finding_state is FindingState.CANDIDATE
    assert finding.location is not None
    assert finding.location.file_path == "app.py"
    assert finding.evidence[0].snippet == "    return eval(value)"


@pytest.mark.asyncio
async def test_semgrep_scanner_marks_symlink_as_partial(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("eval(input())\n", encoding="utf-8")
    (workspace / "linked.py").symlink_to(outside)
    scanner = SemgrepScanner(_fake_semgrep(tmp_path), rules=[_rule(tmp_path)])

    result = await scanner.scan(
        ScannerRequest(
            workspace_root=workspace.resolve(),
            relative_files=("linked.py",),
        )
    )

    assert result.status is ScannerStatus.PARTIAL
    assert result.scanned_files == 0
    assert result.skipped_paths == ("linked.py",)
    assert result.issues[0].code == "symlink"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_code"),
    (
        ("invalid-json", "invalid_json"),
        ("invalid-schema", "invalid_schema"),
        ("large-output", "output_limit"),
    ),
)
async def test_semgrep_scanner_reports_process_contract_failures(
    tmp_path: Path, mode: str, expected_code: str
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("safe = True\n", encoding="utf-8")
    scanner = SemgrepScanner(
        _fake_semgrep(tmp_path, mode=mode),
        rules=[_rule(tmp_path)],
        output_limit_bytes=1024,
    )

    result = await scanner.scan(
        ScannerRequest(workspace_root=workspace.resolve(), relative_files=("app.py",))
    )

    assert result.status is ScannerStatus.ERROR
    assert result.scanned_files == 0
    assert result.issues[-1].code == expected_code


@pytest.mark.asyncio
async def test_semgrep_scanner_never_treats_coverage_gap_as_clean(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("safe = True\n", encoding="utf-8")
    scanner = SemgrepScanner(_fake_semgrep(tmp_path, mode="coverage-gap"), rules=[_rule(tmp_path)])

    result = await scanner.scan(
        ScannerRequest(workspace_root=workspace.resolve(), relative_files=("app.py",))
    )

    assert result.status is ScannerStatus.PARTIAL
    assert not result.candidates
    assert any(issue.code == "coverage_mismatch" for issue in result.issues)


@pytest.mark.asyncio
async def test_semgrep_scanner_compares_exact_path_sets_not_only_counts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("safe = True\n", encoding="utf-8")
    (workspace / "other.py").write_text("also_safe = True\n", encoding="utf-8")
    scanner = SemgrepScanner(
        _fake_semgrep(tmp_path, mode="coverage-substitute"), rules=[_rule(tmp_path)]
    )

    result = await scanner.scan(
        ScannerRequest(workspace_root=workspace.resolve(), relative_files=("app.py",))
    )

    assert result.status is ScannerStatus.PARTIAL
    assert result.scanned_files == 1
    assert result.scanned_paths == ("other.py",)
    assert any(issue.code == "coverage_mismatch" for issue in result.issues)


def test_rule_symlink_and_terminal_controls_are_rejected(tmp_path: Path) -> None:
    real_rule = _rule(tmp_path)
    linked_rule = tmp_path / "linked-rule.yml"
    linked_rule.symlink_to(real_rule)
    with pytest.raises(ValueError, match="must not be a symlink"):
        SemgrepScanner(_fake_semgrep(tmp_path), rules=[linked_rule])

    hostile = "safe\x1b]8;;https://evil.example\x07click\x1b]8;;\x07\x1b[31mred\x1b[0m\x00"
    cleaned = sanitize_untrusted(hostile)
    assert "\x1b" not in cleaned
    assert "https://evil.example" not in cleaned
    assert cleaned.endswith("\\x00")
    assert os.linesep not in cleaned
