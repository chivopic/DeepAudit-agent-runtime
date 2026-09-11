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


def matches(label: Label, finding: dict[str, Any]) -> bool:
    """Does ``finding`` plausibly report ``label``?"""
    fpath = _norm_path(finding.get("file_path") or finding.get("path"))
    lpath = _norm_path(label.path)
    if not fpath or not (fpath.endswith(lpath) or lpath.endswith(fpath)):
        return False

    line = _line_of(finding)
    if line is None or abs(line - label.line) > label.tolerance:
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
    missed: list[str] = field(default_factory=list)
    fp_examples: list[str] = field(default_factory=list)
    # Capabilities this engine was missing at run time. Non-empty means the
    # numbers describe a crippled engine and must not be read as a verdict.
    degraded: list[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def recall(self) -> float:
        return self.labels_found / self.labels_total if self.labels_total else 0.0

    @property
    def fp_rate(self) -> float:
        """Safe files carrying at least one false finding, as a share."""
        return self.false_positives / self.safe_files if self.safe_files else 0.0

    def row(self) -> str:
        return (
            f"{self.engine:<12} "
            f"recall {self.labels_found:>2}/{self.labels_total:<2} "
            f"({self.recall * 100:5.1f}%)  "
            f"findings {self.findings_total:>3}  "
            f"FP(safe) {self.false_positives:>3}  "
            f"unmatched {self.unmatched:>3}  "
            f"tokens {self.tokens:>7}  "
            f"{self.seconds:6.1f}s"
        )


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
            matched_findings.add(hit)

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
