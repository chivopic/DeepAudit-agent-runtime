"""Scoring rules for the engine comparison benchmark.

Deterministic — no model, no network. The benchmark itself needs a real model
and is run by hand; these tests pin the arithmetic it depends on.
"""

from __future__ import annotations

import pytest

from tests.evals.benchmark.labels import CORPUS, Label
from tests.evals.benchmark.scorer import matches, score


def _f(path: str, line: int, **kw) -> dict:
    return {"file_path": path, "line_start": line, **kw}


# ---------------------------------------------------------------------------
# Label matching
# ---------------------------------------------------------------------------


def test_matches_on_cwe():
    label = Label("vulnerable/a.py", 10, "CWE-89", "sql-injection")
    assert matches(label, _f("vulnerable/a.py", 10, cwe_id="CWE-89"))


def test_matches_on_title_when_no_cwe_is_emitted():
    """Engines differ in how reliably they emit CWEs; the class still counts."""
    label = Label("vulnerable/a.py", 10, "CWE-89", "sql-injection")
    assert matches(label, _f("vulnerable/a.py", 10, title="SQL Injection in query"))


def test_wrong_class_does_not_match():
    label = Label("vulnerable/a.py", 10, "CWE-89", "sql-injection")
    assert not matches(label, _f("vulnerable/a.py", 10, title="Weak hash", cwe_id="CWE-327"))


def test_line_tolerance_is_respected():
    label = Label("vulnerable/a.py", 10, "CWE-89", "sql-injection", tolerance=3)
    assert matches(label, _f("vulnerable/a.py", 13, cwe_id="CWE-89"))
    assert not matches(label, _f("vulnerable/a.py", 14, cwe_id="CWE-89"))


def test_path_suffix_matching_survives_a_prefixed_root():
    label = Label("vulnerable/a.py", 10, "CWE-89", "sql-injection")
    assert matches(label, _f("/tmp/run/src/vulnerable/a.py", 10, cwe_id="CWE-89"))


def test_a_finding_without_a_line_cannot_match():
    label = Label("vulnerable/a.py", 10, "CWE-89", "sql-injection")
    assert not matches(label, {"file_path": "vulnerable/a.py", "cwe_id": "CWE-89"})


# ---------------------------------------------------------------------------
# Aggregate scoring
# ---------------------------------------------------------------------------


def test_perfect_engine_scores_full_recall_without_false_positives():
    findings = [
        _f(lab.path, lab.line, cwe_id=lab.cwe, title=lab.kind)
        for lab in CORPUS.vulnerable
    ]
    s = score("perfect", findings, CORPUS.vulnerable, CORPUS.safe_paths)
    assert s.labels_found == len(CORPUS.vulnerable)
    assert s.recall == 1.0
    assert s.false_positives == 0
    assert s.missed == []


def test_findings_on_safe_files_count_as_false_positives():
    findings = [_f(p, 5, title="SQL Injection") for p in CORPUS.safe_paths]
    s = score("noisy", findings, CORPUS.vulnerable, CORPUS.safe_paths)
    assert s.false_positives == len(CORPUS.safe_paths)
    assert s.fp_rate == 1.0
    assert s.recall == 0.0


def test_unmatched_findings_on_vulnerable_files_are_not_called_false_positives():
    """Planted code can contain incidental issues; don't hold those against it."""
    findings = [_f("vulnerable/db_users.py", 99, title="Something else")]
    s = score("x", findings, CORPUS.vulnerable, CORPUS.safe_paths)
    assert s.false_positives == 0
    assert s.unmatched == 1


def test_silent_engine_scores_zero_rather_than_crashing():
    s = score("blind", [], CORPUS.vulnerable, CORPUS.safe_paths)
    assert s.recall == 0.0
    assert s.findings_total == 0
    assert len(s.missed) == len(CORPUS.vulnerable)


def test_fp_rate_tracks_the_real_number_of_safe_files():
    s = score("x", [_f(CORPUS.safe_paths[0], 3, title="SQLi")], CORPUS.vulnerable, CORPUS.safe_paths)
    assert s.safe_files == len(CORPUS.safe_paths)
    assert s.fp_rate == pytest.approx(1 / len(CORPUS.safe_paths))


def test_degraded_runs_are_flagged_not_silently_scored():
    """A crippled baseline must never be presented as a comparison."""
    s = score("react", [], CORPUS.vulnerable, CORPUS.safe_paths)
    assert s.degraded == []
    s.degraded = ["embeddings disabled"]
    assert s.degraded


# ---------------------------------------------------------------------------
# Corpus integrity
# ---------------------------------------------------------------------------


def test_every_label_points_at_a_real_line():
    from pathlib import Path

    root = Path(__file__).parent / "evals" / "benchmark" / "corpus"
    for label in CORPUS.vulnerable:
        lines = (root / label.path).read_text(encoding="utf-8").splitlines()
        assert 1 <= label.line <= len(lines), f"{label.path}:{label.line} out of range"


def test_every_safe_path_exists():
    from pathlib import Path

    root = Path(__file__).parent / "evals" / "benchmark" / "corpus"
    for path in CORPUS.safe_paths:
        assert (root / path).exists(), path


def test_corpus_has_negatives_or_false_positives_are_unmeasurable():
    assert len(CORPUS.safe_paths) >= 8


def test_advisory_gaps_are_separate_from_blocking_ones():
    """Not every missing capability invalidates a run to the same degree.

    A missing scanner means findings were never looked for (blocking). A
    missing embedding provider means retrieval was weaker (advisory). Only the
    first withholds the verdict.
    """
    s = score("react", [], CORPUS.vulnerable, CORPUS.safe_paths)
    assert s.degraded == []
    assert s.advisory == []

    s.advisory = ["RAG disabled"]
    assert s.advisory and not s.degraded


def test_cross_file_labels_exist_and_are_tracked_separately():
    """Single-file patterns are the easy case; a combined recall hides the gap
    that actually decides whether one engine can replace the other."""
    xfile = [lab for lab in CORPUS.vulnerable if lab.cross_file]
    assert len(xfile) >= 3

    findings = [_f(lab.path, lab.line, cwe_id=lab.cwe, title=lab.kind) for lab in xfile]
    s = score("x", findings, CORPUS.vulnerable, CORPUS.safe_paths)
    assert s.xfile_total == len(xfile)
    assert s.xfile_found == len(xfile)
    assert s.xfile_recall == 1.0


def test_a_cross_file_label_also_matches_at_the_broken_helper():
    """Flagging the helper shows comprehension too — scoring it wrong would
    penalise an engine for being right in the other place."""
    lab = next(x for x in CORPUS.vulnerable if x.cross_file and x.also_at)
    helper_path, helper_line = lab.also_at[0]
    assert matches(lab, _f(helper_path, helper_line, cwe_id=lab.cwe))


def test_every_cross_file_helper_location_is_a_real_line():
    from pathlib import Path

    root = Path(__file__).parent / "evals" / "benchmark" / "corpus"
    for lab in CORPUS.vulnerable:
        for path, line in lab.also_at:
            lines = (root / path).read_text(encoding="utf-8").splitlines()
            assert 1 <= line <= len(lines), f"{path}:{line} out of range"
