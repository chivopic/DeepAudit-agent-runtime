"""Every package must be reachable from production code, not just from tests.

This repository has shipped the same failure four times: a package is built,
tested, and marked Done in IMPLEMENTATION_STATUS — and nothing outside its own
test ever imports it.

    FakeLLM / ModelRouter   the graph path could not reach a real model
    severity_threshold      the API accepted the parameter and discarded it
    M7 verification         every finding stayed NOT_RUN whatever was asked
    M11 harness             still unreferenced today

Unit tests cannot catch this: they import the thing themselves, so the thing is
always reachable from where they stand. This test looks from production.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"
AGENT = APP / "services" / "agent"

# Packages that are deliberately not wired yet. Adding to this set has to be a
# conscious edit with a reason — that is the whole point of the test.
KNOWN_UNWIRED: dict[str, str] = {
    "harness": (
        "M11 Agent Harness: AgentRuntime, ModelRouter, PermissionPolicy and "
        "BudgetManager are referenced only by tests. graph_audits reaches the "
        "real model through LLMServiceGateway instead, so ModelRouter — the "
        "harness's intended wiring point — is still dead. Either connect it or "
        "drop it; leaving it is what this test exists to make visible."
    ),
}


def _packages() -> list[str]:
    return sorted(
        p.name
        for p in AGENT.iterdir()
        if p.is_dir() and not p.name.startswith("__") and (p / "__init__.py").exists()
    )


def _production_importers(package: str) -> list[str]:
    """Files under app/ outside ``package`` that import it."""
    patterns = [
        re.compile(rf"\bagent\.{re.escape(package)}\b"),
        re.compile(rf"from\s+\.{{1,3}}{re.escape(package)}\b"),
        re.compile(rf"from\s+\.{{1,3}}\s*import\s+[^\n]*\b{re.escape(package)}\b"),
    ]
    own = AGENT / package
    hits: list[str] = []
    for path in APP.rglob("*.py"):
        if own in path.parents or path == own:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if any(p.search(text) for p in patterns):
            hits.append(str(path.relative_to(APP)))
    return hits


@pytest.mark.parametrize("package", _packages())
def test_package_is_reachable_from_production(package):
    importers = _production_importers(package)
    if package in KNOWN_UNWIRED:
        assert not importers, (
            f"{package} is listed as unwired but production now imports it "
            f"({importers[:3]}). Remove it from KNOWN_UNWIRED."
        )
        pytest.skip(f"known unwired: {KNOWN_UNWIRED[package]}")
    assert importers, (
        f"app/services/agent/{package} is imported by no production code — "
        f"only by tests, if at all. Built and tested but never connected is "
        f"this repository's most repeated defect. Wire it, delete it, or add it "
        f"to KNOWN_UNWIRED with a reason."
    )


def test_the_unwired_list_is_not_a_dumping_ground():
    """A growing list of exceptions would defeat the check."""
    assert len(KNOWN_UNWIRED) <= 2, (
        "More than two packages are knowingly unwired. That is a backlog, not "
        "an exception list."
    )


def test_every_unwired_entry_explains_itself():
    for package, reason in KNOWN_UNWIRED.items():
        assert len(reason) > 40, f"{package} needs a real reason, not a label"


def test_the_check_actually_detects_an_import():
    """Guard the guard: a regex that matches nothing would pass everything."""
    assert _production_importers("domain"), "domain is definitely imported"
