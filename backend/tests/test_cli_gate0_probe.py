"""Unit tests for the CLI Gate 0 execution and normalization boundaries."""

from __future__ import annotations

import sys
from pathlib import Path

from scripts.cli_gate0_probe import (
    build_scanner_env,
    normalize_semgrep,
    run_capped,
    workspace_digest,
)


def test_scanner_environment_does_not_inherit_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-cross-boundary")
    env = build_scanner_env(tmp_path, Path(sys.executable))
    result = run_capped(
        [
            str(Path(sys.executable).resolve()),
            "-c",
            "import os; print(os.environ.get('AWS_SECRET_ACCESS_KEY', 'NOT_SET'))",
        ],
        cwd=tmp_path,
        env=env,
        timeout_seconds=5,
        output_limit_bytes=1024,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == b"NOT_SET"
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_process_output_is_killed_at_combined_limit(tmp_path: Path) -> None:
    env = build_scanner_env(tmp_path, Path(sys.executable))
    result = run_capped(
        [str(Path(sys.executable).resolve()), "-c", "print('x' * 100_000)"],
        cwd=tmp_path,
        env=env,
        timeout_seconds=5,
        output_limit_bytes=1024,
    )
    assert result.output_limited is True
    assert len(result.stdout) + len(result.stderr) <= 1024


def test_process_is_killed_at_timeout(tmp_path: Path) -> None:
    env = build_scanner_env(tmp_path, Path(sys.executable))
    result = run_capped(
        [str(Path(sys.executable).resolve()), "-c", "import time; time.sleep(5)"],
        cwd=tmp_path,
        env=env,
        timeout_seconds=0.1,
        output_limit_bytes=1024,
    )
    assert result.timed_out is True


def test_semgrep_json_normalizes_to_stable_e0_finding(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "app.py"
    source.write_text("def run(value):\n    return eval(value)\n", encoding="utf-8")
    raw = {
        "results": [
            {
                "check_id": "deepaudit.python.dangerous-eval",
                "path": str(source),
                "start": {"line": 2, "col": 12},
                "end": {"line": 2, "col": 23},
                "extra": {
                    "message": "Avoid eval on untrusted input.",
                    "severity": "ERROR",
                    "metadata": {
                        "category": "security",
                        "cwe": ["CWE-95: Code Injection"],
                    },
                },
            }
        ]
    }
    first = normalize_semgrep(
        raw,
        cwd=tmp_path,
        workspace=workspace,
        scanner_version="1.173.0",
        rule_hash="abc123",
    )[0]
    second = normalize_semgrep(
        raw,
        cwd=tmp_path,
        workspace=workspace,
        scanner_version="1.173.0",
        rule_hash="abc123",
    )[0]

    assert first.fingerprint == second.fingerprint
    assert first.id == second.id
    assert first.location is not None
    assert first.location.file_path == "app.py"
    assert first.metadata["evidence_level"] == "E0"
    assert first.metadata["finding_state"] == "candidate"
    assert first.verification_status.value == "not_run"
    assert first.evidence[0].snippet == "    return eval(value)"


def test_workspace_digest_changes_only_when_workspace_changes(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "app.py"
    source.write_text("safe = True\n", encoding="utf-8")
    before = workspace_digest(tmp_path)
    monkeypatch.setenv("DEEPAUDIT_UNRELATED_TEST_VALUE", "ignored")
    assert workspace_digest(tmp_path) == before
    source.write_text("safe = False\n", encoding="utf-8")
    assert workspace_digest(tmp_path) != before
