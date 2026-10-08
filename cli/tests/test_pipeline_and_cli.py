from __future__ import annotations

import json
from pathlib import Path

from deepaudit_cli.main import main
from deepaudit_cli.pipeline import AuditOptions, audit, doctor


def test_audit_envelope_and_finding_gate(tmp_path: Path, fake_semgrep: Path) -> None:
    (tmp_path / "app.py").write_text("eval(value)\n", encoding="utf-8")

    outcome = audit(AuditOptions(workspace=tmp_path, semgrep_executable=fake_semgrep))

    assert outcome.exit_code == 1
    assert outcome.envelope["schema_version"] == "1.0"
    assert outcome.envelope["audit"]["status"] == "completed"
    assert outcome.envelope["coverage"]["scanned_files"] == 1
    assert outcome.envelope["metadata"]["model_calls"] == 0
    assert outcome.envelope["metadata"]["verification"] == "not_run"


def test_manifest_gap_has_partial_exit_priority(tmp_path: Path, fake_semgrep: Path) -> None:
    (tmp_path / "app.py").write_text("eval(value)\n", encoding="utf-8")
    (tmp_path / "large.py").write_text("x" * 100, encoding="utf-8")

    outcome = audit(
        AuditOptions(
            workspace=tmp_path,
            semgrep_executable=fake_semgrep,
            max_target_bytes=50,
        )
    )

    assert outcome.exit_code == 4
    assert outcome.envelope["audit"]["status"] == "partial"
    assert outcome.envelope["findings"]


def test_doctor_reports_zero_runtime_dependencies(fake_semgrep: Path) -> None:
    envelope, exit_code = doctor(fake_semgrep)
    checks = {check["name"]: check for check in envelope["checks"]}
    assert exit_code == 0
    assert checks["runtime_dependencies"]["status"] == "ok"
    assert checks["semgrep"]["metadata"]["expected_version"] == "1.173.0"


def test_cli_json_stdout_is_one_terminal_document(
    tmp_path: Path, fake_semgrep: Path, capsys
) -> None:
    (tmp_path / "app.py").write_text("eval(value)\n", encoding="utf-8")

    exit_code = main(
        ["audit", str(tmp_path), "--semgrep", str(fake_semgrep), "--format", "json"]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 1
    assert captured.err == ""
    assert payload["findings"][0]["display_id"] == "FND-001"


def test_cli_refuses_output_inside_workspace(
    tmp_path: Path, fake_semgrep: Path, capsys
) -> None:
    (tmp_path / "app.py").write_text("eval(value)\n", encoding="utf-8")

    exit_code = main(
        [
            "audit",
            str(tmp_path),
            "--semgrep",
            str(fake_semgrep),
            "--out",
            str(tmp_path / "report.json"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "outside the audited workspace" in captured.err


def test_cli_atomically_writes_json_outside_workspace(
    tmp_path: Path, fake_semgrep: Path, capsys
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("eval(value)\n", encoding="utf-8")
    output = tmp_path / "report.json"

    exit_code = main(
        [
            "audit",
            str(workspace),
            "--semgrep",
            str(fake_semgrep),
            "--out",
            str(output),
        ]
    )

    capsys.readouterr()
    serialized = output.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    assert exit_code == 1
    assert payload["scope"]["root_display"] == "workspace"
    assert str(tmp_path) not in serialized
