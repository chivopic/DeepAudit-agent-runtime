"""Findings must be defects, not observations.

A security report full of non-issues is worse than a short one: it teaches
people to skim. These pin the two rules that keep it short — drop the model's
descriptions of defences, and honour the severity threshold the API accepts.
"""

from __future__ import annotations

import pytest

from app.services.agent.domain import (
    AuditRequest,
    CandidateFinding,
    Evidence,
    RepositoryRef,
    Severity,
    SourceLocation,
)
from app.services.agent.graph.nodes import (
    _is_defence_note,
    _parse_llm_findings,
    aggregate_findings,
)

# ---------------------------------------------------------------------------
# Defence notes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "SSRF mitigated by strict host allowlist and scheme check",
        "Path traversal mitigated via resolve_within helper",
        "Command injection is mitigated through argv execution",
        "Input is properly validated, not a vulnerability",
        "No vulnerability: the query is parameterised",
        "已缓解：使用了主机白名单",
    ],
)
def test_descriptions_of_defences_are_dropped(title):
    assert _is_defence_note({"title": title}) is True


@pytest.mark.parametrize(
    "title",
    [
        "SQL Injection via string-formatted query",
        "OS Command Injection in ping()",
        "SSRF - bypassable host_allowed policy",
        "Insufficient mitigation of path traversal",
        "Input is properly validated but the check is incomplete",
        "Allowlist is applied, however it can be circumvented",
        "使用了白名单但仍然可绕过",
    ],
)
def test_real_defects_survive(title):
    """Conservative on purpose: dropping a real finding costs far more."""
    assert _is_defence_note({"title": title}) is False


def test_the_check_reads_the_description_too():
    item = {"title": "Session handling", "description": "This is mitigated by json.loads"}
    assert _is_defence_note(item) is True


def test_an_empty_finding_is_not_treated_as_a_defence_note():
    assert _is_defence_note({}) is False


def test_parsing_filters_defence_notes_out():
    raw = (
        '[{"title": "SQL Injection", "line": 3},'
        ' {"title": "SSRF mitigated by allowlist", "line": 9}]'
    )
    parsed = _parse_llm_findings(raw)
    assert [f["title"] for f in parsed] == ["SQL Injection"]


# ---------------------------------------------------------------------------
# Severity threshold — accepted by the API, previously ignored
# ---------------------------------------------------------------------------


def _candidate(sev: Severity, title: str = "x") -> CandidateFinding:
    loc = SourceLocation(file_path="a.py", start_line=1, end_line=1)
    return CandidateFinding(
        title=title,
        description="d",
        severity=sev,
        location=loc,
        evidence=[Evidence(kind="model", summary="s", location=loc, confidence=0.5)],
    )


async def _aggregate(threshold: Severity, sevs: list[Severity]):
    req = AuditRequest(
        repository=RepositoryRef(source_type="local", local_path="fixture://t"),
        severity_threshold=threshold,
    )
    out = await aggregate_findings(
        {"request": req, "candidate_findings": [_candidate(s) for s in sevs]}
    )
    return out["normalized_findings"]


@pytest.mark.asyncio
async def test_threshold_drops_findings_below_it():
    kept = await _aggregate(
        Severity.HIGH, [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
    )
    assert [f.severity for f in kept] == [Severity.HIGH, Severity.CRITICAL]


@pytest.mark.asyncio
async def test_the_default_threshold_keeps_everything():
    sevs = [Severity.INFO, Severity.LOW, Severity.CRITICAL]
    kept = await _aggregate(Severity.INFO, sevs)
    assert len(kept) == len(sevs)


@pytest.mark.asyncio
async def test_threshold_drops_are_reported_not_silent():
    req = AuditRequest(
        repository=RepositoryRef(source_type="local", local_path="fixture://t"),
        severity_threshold=Severity.CRITICAL,
    )
    out = await aggregate_findings(
        {"request": req, "candidate_findings": [_candidate(Severity.LOW)]}
    )
    event = out["events"][0]
    assert event["below_threshold"] == 1
    assert event["kept"] == 0


@pytest.mark.asyncio
async def test_aggregation_without_a_request_does_not_crash():
    out = await aggregate_findings({"candidate_findings": [_candidate(Severity.LOW)]})
    assert len(out["normalized_findings"]) == 1


# ---------------------------------------------------------------------------
# The SQL heuristic fired on parameterised queries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        'cur.execute("SELECT * FROM users WHERE id = ?", (uid,))',
        'cur.execute("SELECT * FROM reports WHERE owner = ?", bind(name))',
        'SQL = "SELECT * FROM users"',
    ],
)
def test_parameterised_queries_are_not_flagged(line):
    """A bare SELECT is just SQL; it is building it from parts that injects."""
    from app.services.agent.graph.nodes import _line_builds_a_string

    assert _line_builds_a_string(line) is False


@pytest.mark.parametrize(
    "line",
    [
        'cur.execute("SELECT * FROM users WHERE id = %s" % uid)',
        'cur.execute("SELECT * FROM t WHERE n = \'" + name + "\'")',
        'cur.execute(f"SELECT * FROM t WHERE n = {name}")',
        'sql = "SELECT * FROM users WHERE x = {}".format(x)',
    ],
)
def test_built_statements_are_still_flagged(line):
    from app.services.agent.graph.nodes import _line_builds_a_string

    assert _line_builds_a_string(line) is True


def test_the_heuristic_scanner_skips_a_parameterised_query():
    from app.services.agent.domain import AuditTaskSpec
    from app.services.agent.graph.nodes import _heuristic_candidates

    task = AuditTaskSpec(kind="analyze_file", target_path="db.py")
    safe = 'cur.execute("SELECT * FROM users WHERE id = ?", (uid,))\n'
    assert _heuristic_candidates(task, safe) == []

    unsafe = 'cur.execute("SELECT * FROM users WHERE id = %s" % uid)\n'
    assert len(_heuristic_candidates(task, unsafe)) == 1
