"""End-to-end contracts for the CLI-1 application and presenters."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


def _fake_semgrep(tmp_path: Path, *, version: str = "1.173.0") -> Path:
    executable = tmp_path / "semgrep"
    executable.write_text(
        f"""#!/usr/bin/env python3
import json
import os
import pathlib
import sys

if "--version" in sys.argv:
    print({version!r})
    raise SystemExit(0)
if os.environ.get("DEEPAUDIT_TEST_SECRET"):
    print("credential leaked", file=sys.stderr)
    raise SystemExit(9)

targets = [arg for arg in sys.argv[1:] if arg.endswith(".py")]
results = []
for target in targets:
    source = pathlib.Path(target).read_text(encoding="utf-8")
    if "eval(" not in source:
        continue
    line = next(i for i, text in enumerate(source.splitlines(), 1) if "eval(" in text)
    results.append({{
        "check_id": "deepaudit.python.dangerous-eval",
        "path": target,
        "start": {{"line": line, "col": 12}},
        "end": {{"line": line, "col": 23}},
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
print(json.dumps({{
    "results": results,
    "errors": [],
    "paths": {{"scanned": targets}},
    "time": {{"max_memory_bytes": 123456}}
}}))
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "target"
    workspace.mkdir()
    (workspace / "app.py").write_text(
        "def evaluate(value):\n    return eval(value)\n", encoding="utf-8"
    )
    return workspace


def _run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    child_env = dict(os.environ)
    child_env.update(env or {})
    return subprocess.run(  # noqa: S603 - test invokes the current interpreter
        [sys.executable, "-m", "app.cli", *args],
        cwd=Path(__file__).resolve().parents[1],
        env=child_env,
        capture_output=True,
        text=True,
        check=False,
    )


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_doctor_reports_fixed_semgrep_and_rules(tmp_path: Path) -> None:
    semgrep = _fake_semgrep(tmp_path)

    completed = _run_cli("doctor", "--semgrep", str(semgrep), "--format", "json")

    assert completed.returncode == 0
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert (
        next(check for check in payload["checks"] if check["name"] == "semgrep")["message"]
        == "Semgrep 1.173.0"
    )


def test_audit_json_is_complete_deterministic_and_read_only(tmp_path: Path) -> None:
    semgrep = _fake_semgrep(tmp_path)
    workspace = _workspace(tmp_path)
    before = _tree_hash(workspace)

    first = _run_cli(
        "audit",
        str(workspace),
        "--semgrep",
        str(semgrep),
        "--format",
        "json",
        env={"DEEPAUDIT_TEST_SECRET": "must-not-leak"},
    )
    second = _run_cli(
        "audit",
        str(workspace),
        "--semgrep",
        str(semgrep),
        "--format",
        "json",
    )

    assert first.returncode == 1
    assert first.stderr == ""
    assert second.returncode == 1
    payload = json.loads(first.stdout)
    repeated = json.loads(second.stdout)
    assert payload["audit"]["status"] == "completed"
    assert payload["coverage"]["status"] == "complete"
    assert payload["coverage"]["requested_files"] == 1
    assert payload["coverage"]["scanned_files"] == 1
    assert payload["metadata"]["model_calls"] == 0
    assert payload["budget"]["model_calls_used"] == 0
    assert payload["findings"][0]["display_id"] == "FND-001"
    assert payload["findings"][0]["evidence_level"] == "E0"
    assert payload["findings"][0]["finding_state"] == "candidate"
    assert payload["findings"][0]["verification_status"] == "not_run"
    assert payload["findings"][0]["fingerprint"] == repeated["findings"][0]["fingerprint"]
    assert str(tmp_path) not in first.stdout
    assert _tree_hash(workspace) == before


def test_scanner_unavailable_is_partial_or_strict_environment_failure(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    missing = tmp_path / "missing-semgrep"

    partial = _run_cli(
        "audit",
        str(workspace),
        "--semgrep",
        str(missing),
        "--format",
        "json",
    )
    strict = _run_cli(
        "audit",
        str(workspace),
        "--semgrep",
        str(missing),
        "--format",
        "json",
        "--strict",
    )

    assert partial.returncode == 4
    assert partial.stderr == ""
    payload = json.loads(partial.stdout)
    assert payload["audit"]["status"] == "partial"
    assert payload["coverage"]["status"] == "error"
    assert strict.returncode == 3
    assert strict.stdout == ""
    assert "missing-semgrep" in strict.stderr


def test_output_is_atomic_and_must_be_outside_workspace(tmp_path: Path) -> None:
    semgrep = _fake_semgrep(tmp_path)
    workspace = _workspace(tmp_path)
    outside = tmp_path / "report.json"
    inside = workspace / "report.json"

    written = _run_cli(
        "audit",
        str(workspace),
        "--semgrep",
        str(semgrep),
        "--format",
        "json",
        "--out",
        str(outside),
    )
    rejected = _run_cli(
        "audit",
        str(workspace),
        "--semgrep",
        str(semgrep),
        "--out",
        str(inside),
    )

    assert written.returncode == 1
    assert json.loads(outside.read_text(encoding="utf-8"))["audit"]["status"] == "completed"
    assert rejected.returncode == 2
    assert not inside.exists()
    assert "outside" in rejected.stderr


def test_wrong_semgrep_version_fails_doctor(tmp_path: Path) -> None:
    semgrep = _fake_semgrep(tmp_path, version="9.9.9")

    completed = _run_cli("doctor", "--semgrep", str(semgrep), "--format", "json")

    assert completed.returncode == 3
    payload = json.loads(completed.stdout)
    assert payload["status"] == "error"
    assert "expected Semgrep 1.173.0" in completed.stdout


def test_manifest_size_skip_is_partial_not_clean(tmp_path: Path) -> None:
    semgrep = _fake_semgrep(tmp_path)
    workspace = _workspace(tmp_path)

    completed = _run_cli(
        "audit",
        str(workspace),
        "--semgrep",
        str(semgrep),
        "--format",
        "json",
        "--max-target-bytes",
        "8",
    )

    assert completed.returncode == 4
    payload = json.loads(completed.stdout)
    assert payload["audit"]["status"] == "partial"
    assert payload["coverage"]["manifest"]["incomplete"] is True
    assert payload["coverage"]["manifest"]["issues"][0]["code"] == "target_too_large"
