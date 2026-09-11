"""Scoring for the engine comparison benchmark.

Methodology note, because it decides whether the numbers mean anything:

* **Recall** is measured on the vulnerable fixtures against the labels.
* **False positives** are counted **only on the safe fixtures**. A finding on a
  vulnerable file that matches no label is *not* automatically wrong — planted
  code can contain incidental issues — so those are reported separately as
  ``unmatched`` rather than silently held against the engine.
* A label counts as found when an engine reports the same file, a line within
  the label's tolerance, and a compatible class. Class agreement accepts either
  the CWE id or a keyword match on the title, since engines differ in how
  reliably they emit CWEs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from tests.evals.benchmark.labels import Label

# Title keywords that stand in for a CWE when an engine does not emit one.
_KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sql-injection": ("sql",),
    "command-injection": ("command inject", "os command", "shell inject", "rce"),
    "path-traversal": ("path travers", "directory travers", "lfi"),
    "insecure-deserialization": ("deserial", "pickle"),
    "code-injection": ("code inject", "eval", "arbitrary code"),
    "ssrf": ("ssrf", "server-side request"),
    "hardcoded-secret": ("hardcoded", "hard-coded", "secret", "credential", "api key"),
    "weak-hash": ("md5", "weak hash", "weak crypto", "insecure hash"),
    "xss": ("xss", "cross-site script"),
}


def _verification_status_of(finding: dict[str, Any]) -> str:
    """Normalise the two vocabularies the engines use.

    The graph path emits ``verification_status`` (a VerificationStatus value).
    ReAct emits ``is_verified`` / ``needs_verification`` / ``verdict`` and has
    no such field at all — so defaulting a missing key to "not_run" silently
    reports ReAct as never verifying, which is a measurement artifact rather
    than a fact about the engine. Read both, and say "unreported" when neither
    vocabulary is present.
    """
    explicit = finding.get("verification_status") or finding.get("verification")
    if explicit:
        return str(explicit).lower()

    if "is_verified" in finding or "needs_verification" in finding:
        if finding.get("is_verified"):
            return "confirmed"
        if finding.get("needs_verification"):
            return "needs_verification"
        return "unverified"

    verdict = finding.get("verdict")
    if verdict:
        return f"verdict:{str(verdict).lower()}"

    return "unreported"


def _norm_path(p: Optional[str]) -> str:
    return (p or "").replace("\\", "/").strip().lstrip("./").lower()


def _cwe_of(finding: dict[str, Any]) -> Optional[str]:
    raw = finding.get("cwe_id") or finding.get("cwe")
    if not raw:
        return None
    m = re.search(r"(\d+)", str(raw))
    return f"CWE-{m.group(1)}" if m else None


def _line_of(finding: dict[str, Any]) -> Optional[int]:
    for key in ("line_start", "start_line", "line"):
        v = finding.get(key)
        if v is not None:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def _same_file(finding_path: str, label_path: str) -> bool:
    a, b = _norm_path(finding_path), _norm_path(label_path)
    return bool(a) and (a.endswith(b) or b.endswith(a))


def matches(label: Label, finding: dict[str, Any]) -> bool:
    """Does ``finding`` plausibly report ``label``?

    Cross-file labels accept the sink *or* the helper whose guard is broken:
    flagging either shows the engine understood the flaw, and insisting on one
    would score an engine wrong for being right in the other place.
    """
    fpath = finding.get("file_path") or finding.get("path") or ""
    line = _line_of(finding)
    if line is None:
        return False

    locations = [(label.path, label.line)] + [
        (p, ln) for p, ln in label.also_at
    ]
    if not any(
        _same_file(fpath, p) and abs(line - ln) <= label.tolerance
        for p, ln in locations
    ):
        return False

    if _cwe_of(finding) == label.cwe:
        return True

    haystack = " ".join(
        str(finding.get(k) or "") for k in ("title", "description", "vulnerability_type")
    ).lower()
    return any(kw in haystack for kw in _KIND_KEYWORDS.get(label.kind, ()))


@dataclass
class EngineScore:
    engine: str
    labels_total: int = 0
    labels_found: int = 0
    findings_total: int = 0
    # Findings on files known to be safe — the only unambiguous false positives.
    false_positives: int = 0
    safe_files: int = 0
    # Findings on vulnerable files that match no label: suspicious, not damning.
    unmatched: int = 0
    tokens: int = 0
    seconds: float = 0.0
    # Cross-file labels tracked separately: single-file patterns are the easy
    # case and flatter a per-file analyser, so a combined recall hides the gap
    # that actually decides whether one engine can replace the other.
    xfile_total: int = 0
    xfile_found: int = 0
    missed: list[str] = field(default_factory=list)
    fp_examples: list[str] = field(default_factory=list)
    # Kept for --show-xfile: what the engine actually said, not just whether
    # it scored. A hit that would also fire on the safe counterpart is not
    # comprehension, and only the text shows the difference.
    raw_findings: list[dict[str, Any]] = field(default_factory=list)
    # verification_status counts. The graph path reported NOT_RUN for every
    # finding until the M7 subgraph was actually wired in; this makes that
    # visible instead of leaving it an assumption.
    verification: dict[str, int] = field(default_factory=dict)
    # Capabilities this engine was missing at run time. Non-empty means the
    # numbers describe a crippled engine and must not be read as a verdict.
    degraded: list[str] = field(default_factory=list)
    # Gaps that weaken the run without invalidating it; reported, not blocking.
    advisory: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def recall(self) -> float:
        return self.labels_found / self.labels_total if self.labels_total else 0.0

    @property
    def fp_rate(self) -> float:
        """Safe files carrying at least one false finding, as a share."""
        return self.false_positives / self.safe_files if self.safe_files else 0.0

    @property
    def xfile_recall(self) -> float:
        return self.xfile_found / self.xfile_total if self.xfile_total else 0.0

    def row(self) -> str:
        return (
            f"{self.engine:<12} "
            f"recall {self.labels_found:>2}/{self.labels_total:<2} "
            f"({self.recall * 100:5.1f}%)  "
            f"x-file {self.xfile_found}/{self.xfile_total}  "
            f"findings {self.findings_total:>3}  "
            f"FP(safe) {self.false_positives:>3}  "
            f"unmatched {self.unmatched:>3}  "
            f"tokens {self.tokens:>7}  "
            f"{self.seconds:6.1f}s"
        )

    def verification_row(self) -> str:
        """One line of verification_status counts, or a plain statement that
        nothing was verified — which is itself the finding for a path that
        never ran the subgraph."""
        if not self.verification:
            return "no findings"
        parts = ", ".join(
            f"{k}={v}" for k, v in sorted(self.verification.items())
        )
        only_not_run = set(self.verification) <= {"not_run", "none", ""}
        suffix = "  (nothing verified)" if only_not_run else ""
        return parts + suffix


def score(
    engine: str,
    findings: Iterable[dict[str, Any]],
    labels: list[Label],
    safe_paths: list[str],
    *,
    tokens: int = 0,
    seconds: float = 0.0,
) -> EngineScore:
    findings = list(findings)
    safe = {_norm_path(p) for p in safe_paths}

    result = EngineScore(
        engine=engine,
        labels_total=len(labels),
        findings_total=len(findings),
        safe_files=len(safe_paths),
        xfile_total=sum(1 for lab in labels if lab.cross_file),
        raw_findings=findings,
        tokens=tokens,
        seconds=seconds,
    )

    matched_findings: set[int] = set()
    for label in labels:
        hit = next(
            (i for i, f in enumerate(findings) if matches(label, f)),
            None,
        )
        if hit is None:
            result.missed.append(f"{label.path}:{label.line} {label.cwe}")
        else:
            result.labels_found += 1
            if label.cross_file:
                result.xfile_found += 1
            matched_findings.add(hit)

    for f in findings:
        status = _verification_status_of(f)
        result.verification[status] = result.verification.get(status, 0) + 1

    for i, f in enumerate(findings):
        fpath = _norm_path(f.get("file_path") or f.get("path"))
        on_safe_file = any(fpath.endswith(s) or s.endswith(fpath) for s in safe if fpath)
        if on_safe_file:
            result.false_positives += 1
            if len(result.fp_examples) < 6:
                result.fp_examples.append(
                    f"{f.get('file_path')}:{_line_of(f)} {str(f.get('title'))[:54]}"
                )
        elif i not in matched_findings:
            result.unmatched += 1

    return result
