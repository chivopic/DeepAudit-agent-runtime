"""Cross-analyzer de-duplication of findings.

The LLM and the pattern scanner report the same flaw in different words; one
flaw must yield one corroborated finding, not two.
"""

from __future__ import annotations

import pytest

from app.services.agent.domain import (
    Evidence,
    Finding,
    Severity,
    SourceLocation,
)
from app.services.agent.graph.nodes import (
    _dedupe_key,
    _merge_duplicate,
    _normalize_cwe,
    deduplicate_findings,
)


def _finding(
    *,
    title: str,
    analyzer: str,
    confidence: float,
    severity: Severity = Severity.MEDIUM,
    cwe: str | None = "CWE-78",
    path: str = "app.py",
    line: int = 10,
    description: str = "desc",
) -> Finding:
    loc = SourceLocation(file_path=path, start_line=line, end_line=line)
    return Finding(
        title=title,
        description=description,
        severity=severity,
        cwe_id=cwe,
        location=loc,
        evidence=[Evidence(kind="model", summary=analyzer, location=loc, confidence=confidence)],
        confidence=confidence,
        analyzer=analyzer,
    )


async def _run(findings: list[Finding]) -> list[Finding]:
    out = await deduplicate_findings({"normalized_findings": findings})
    return out["normalized_findings"]


# ---------------------------------------------------------------------------
# CWE normalisation — the key that makes semantic matching possible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("CWE-89", "CWE-89"),
        ("cwe 78", "CWE-78"),
        (" 502 ", "CWE-502"),
        (89, "CWE-89"),
        (None, None),
        ("unknown", None),
        ("CWE-", None),
        ("", None),
    ],
)
def test_normalize_cwe(raw, expected):
    assert _normalize_cwe(raw) == expected


def test_dedupe_key_requires_a_class():
    """Without a CWE we must not merge — a wrong merge loses a real finding."""
    assert _dedupe_key(_finding(title="t", analyzer="llm", confidence=0.5, cwe=None)) is None


def test_dedupe_key_is_case_and_separator_insensitive():
    a = _finding(title="a", analyzer="llm", confidence=0.5, path="App.py")
    b = _finding(title="b", analyzer="heuristic", confidence=0.6, path="app.py")
    assert _dedupe_key(a) == _dedupe_key(b)


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_flaw_from_two_analyzers_collapses():
    llm = _finding(
        title="OS Command Injection in ping()",
        analyzer="llm",
        confidence=0.55,
        severity=Severity.CRITICAL,
    )
    heur = _finding(
        title="OS Command Injection",
        analyzer="heuristic",
        confidence=0.6,
        severity=Severity.HIGH,
    )

    out = await _run([heur, llm])

    assert len(out) == 1
    merged = out[0]
    # model-authored narrative wins: it read the surrounding code
    assert merged.title == "OS Command Injection in ping()"
    # corroboration across analyzers is recorded, not lost
    assert merged.analyzer == "heuristic+llm"
    assert set(merged.metadata["merged_from"]) == {"heuristic", "llm"}
    assert merged.metadata["duplicate_count"] == 2
    # the more severe and more confident assessment survives
    assert merged.severity is Severity.CRITICAL
    assert merged.confidence == 0.6
    # neither analyzer's evidence is dropped
    assert len(merged.evidence) == 2


@pytest.mark.asyncio
async def test_different_lines_are_not_merged():
    a = _finding(title="X", analyzer="llm", confidence=0.5, line=10)
    b = _finding(title="X", analyzer="heuristic", confidence=0.6, line=42)
    assert len(await _run([a, b])) == 2


@pytest.mark.asyncio
async def test_different_cwe_on_same_line_is_not_merged():
    """Two genuinely different flaws can share a line."""
    a = _finding(title="cmd", analyzer="llm", confidence=0.5, cwe="CWE-78")
    b = _finding(title="sqli", analyzer="llm", confidence=0.5, cwe="CWE-89")
    assert len(await _run([a, b])) == 2


@pytest.mark.asyncio
async def test_findings_without_cwe_are_kept_apart():
    a = _finding(title="one", analyzer="llm", confidence=0.5, cwe=None)
    b = _finding(title="two", analyzer="heuristic", confidence=0.6, cwe=None)
    assert len(await _run([a, b])) == 2


@pytest.mark.asyncio
async def test_survivor_keeps_position_of_first_report():
    """A late duplicate collapses into the earlier slot, order preserved."""
    first = _finding(title="first", analyzer="heuristic", confidence=0.9, line=1, cwe="CWE-1")
    dup = _finding(title="first dup", analyzer="heuristic", confidence=0.1, line=1, cwe="CWE-1")
    other = _finding(title="other", analyzer="heuristic", confidence=0.5, line=2, cwe="CWE-2")

    out = await _run([first, other, dup])

    assert [f.title for f in out] == ["first", "other"]
    assert out[0].metadata["duplicate_count"] == 2


@pytest.mark.asyncio
async def test_model_title_wins_even_at_lower_confidence():
    """Confidence ranks the assessment; the model still writes the headline."""
    heur = _finding(title="Possible SQL string", analyzer="heuristic", confidence=0.9)
    llm = _finding(title="SQL Injection via string-formatted query", analyzer="llm", confidence=0.1)

    out = await _run([heur, llm])

    assert len(out) == 1
    assert out[0].title == "SQL Injection via string-formatted query"
    assert out[0].confidence == 0.9


@pytest.mark.asyncio
async def test_empty_input():
    assert await _run([]) == []


def test_merge_prefers_richer_description_when_model_silent():
    keep = _finding(title="t", analyzer="heuristic", confidence=0.6, description="pattern hit")
    drop = _finding(title="t", analyzer="heuristic", confidence=0.5, description="other")
    merged = _merge_duplicate(keep, drop)
    assert merged.description == "pattern hit"
    assert merged.metadata["duplicate_count"] == 2
