from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

from deepaudit_cli.domain import ScanStatus, Severity
from deepaudit_cli.scanner import run_capped, sanitize, scanner_environment
from deepaudit_cli.semgrep import SemgrepScanner, normalize_results


def test_normalization_preserves_evidence_contract(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("eval(user_input)\n", encoding="utf-8")
    payload = {
        "results": [
            {
                "check_id": "deepaudit.python.dangerous-eval",
                "path": str(source),
                "start": {"line": 1, "col": 1},
                "end": {"line": 1, "col": 17},
                "extra": {
                    "message": "dangerous evaluation",
                    "severity": "ERROR",
                    "metadata": {
                        "title": "Dynamic Code Evaluation",
                        "category": "code_injection",
                        "cwe": ["CWE-95: Dynamic Evaluation"],
                        "confidence": "HIGH",
                    },
                },
            }
        ]
    }

    findings = normalize_results(
        payload,
        cwd=tmp_path,
        workspace=tmp_path,
        scanner_version="1.173.0",
        rule_set_hash="rules",
    )

    finding = findings[0]
    assert finding.display_id == "FND-001"
    assert finding.severity is Severity.HIGH
    assert finding.evidence_level == "E0"
    assert finding.finding_state == "candidate"
    assert finding.verification_status == "not_run"
    assert finding.location.file_path == "app.py"
    assert finding.location.code_hash == hashlib.sha256(b"eval(user_input)").hexdigest()


def test_normalization_rejects_result_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("eval(x)\n", encoding="utf-8")
    payload = {
        "results": [
            {
                "check_id": "rule",
                "path": str(outside),
                "start": {"line": 1},
                "extra": {},
            }
        ]
    }
    with pytest.raises(ValueError, match="outside workspace"):
        normalize_results(
            payload,
            cwd=tmp_path,
            workspace=tmp_path,
            scanner_version="1.173.0",
            rule_set_hash="rules",
        )


def test_fake_scanner_end_to_end(tmp_path: Path, fake_semgrep: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("eval(value)\n", encoding="utf-8")
    scanner = SemgrepScanner(fake_semgrep)

    result = scanner.scan(tmp_path, ("app.py",))

    assert result.status is ScanStatus.COMPLETE
    assert result.version == "1.173.0"
    assert result.scanned_paths == ("app.py",)
    assert len(result.findings) == 1


def test_scanner_rejects_symlinked_path_component(
    tmp_path: Path, fake_semgrep: Path
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "app.py").write_text("eval(value)\n", encoding="utf-8")
    (tmp_path / "linked").symlink_to(real, target_is_directory=True)
    scanner = SemgrepScanner(fake_semgrep)

    with pytest.raises(ValueError, match="unsafe scan target"):
        scanner.scan(tmp_path, ("linked/app.py",))


def test_scanner_environment_does_not_forward_caller_secrets(
    tmp_path: Path, fake_semgrep: Path
) -> None:
    environment = scanner_environment(tmp_path, fake_semgrep)
    assert "OPENAI_API_KEY" not in environment
    assert "AWS_SECRET_ACCESS_KEY" not in environment
    assert set(environment) <= {
        "HOME",
        "TMPDIR",
        "PATH",
        "LANG",
        "LC_ALL",
        "NO_COLOR",
        "PYTHONIOENCODING",
        "SEMGREP_ENABLE_VERSION_CHECK",
        "SEMGREP_SEND_METRICS",
        "SSL_CERT_FILE",
    }


def test_terminal_controls_are_sanitized() -> None:
    assert sanitize("safe\x1b]0;owned\x07\x1b[31mred\x1b[0m\x00") == "safered\\x00"


def test_process_output_is_bounded(tmp_path: Path) -> None:
    result = run_capped(
        [sys.executable, "-c", "print('x' * 100000)"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=2,
        output_limit_bytes=1024,
    )
    assert result.output_limited
    assert len(result.stdout) + len(result.stderr) <= 1024


def test_process_timeout_is_bounded(tmp_path: Path) -> None:
    result = run_capped(
        [sys.executable, "-c", "import time; time.sleep(5)"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
        timeout_seconds=0.05,
        output_limit_bytes=1024,
    )
    assert result.timed_out
