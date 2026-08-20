from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[1]


def test_project_declares_no_runtime_dependencies() -> None:
    config = tomllib.loads((CLI_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["project"]["dependencies"] == []


def test_package_imports_without_backend_or_frameworks() -> None:
    source = CLI_ROOT / "src"
    code = f"""
import sys
sys.path.insert(0, {str(source)!r})
import deepaudit_cli.main
forbidden = {{'app', 'pydantic', 'langgraph', 'langchain', 'fastapi', 'sqlalchemy'}}
loaded = forbidden.intersection(sys.modules)
if loaded:
    raise SystemExit(','.join(sorted(loaded)))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
