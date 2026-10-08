"""Regression tests for lightweight, side-effect-free agent imports."""

from __future__ import annotations

import subprocess
import sys


def test_harness_import_does_not_initialise_legacy_rag() -> None:
    probe = """
import sys
from app.services.agent.harness import AgentRuntime

assert AgentRuntime.__name__ == "AgentRuntime"
assert "app.services.agent.knowledge.rag_knowledge" not in sys.modules
assert "app.services.agent.agents.orchestrator" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_legacy_package_export_remains_available() -> None:
    from app.services.agent import AgentState
    from app.services.agent.core import AgentState as DirectAgentState

    assert AgentState is DirectAgentState
