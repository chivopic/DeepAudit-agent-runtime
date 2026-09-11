"""Ground truth for the comparison benchmark.

Line numbers point at the vulnerable statement in
``tests/evals/benchmark/corpus/``. Keep them in step when editing a fixture.

Safe files carry no labels on purpose: they are how false positives get
measured. Each one is the direct counterpart of a vulnerable file and is
written to *look* risky — it still talks about SQL, subprocesses, passwords
and webhooks — so it tests discrimination rather than keyword avoidance.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Label:
    path: str
    line: int
    cwe: str
    kind: str
    # Some classes are reported a line or two off (decorator, wrapper call).
    tolerance: int = 3


@dataclass(frozen=True)
class Corpus:
    vulnerable: list[Label] = field(default_factory=list)
    safe_paths: list[str] = field(default_factory=list)


LABELS: list[Label] = [
    # --- SQL injection -----------------------------------------------------
    Label("vulnerable/db_users.py", 7, "CWE-89", "sql-injection"),
    Label("vulnerable/db_users.py", 13, "CWE-89", "sql-injection"),
    # --- OS command injection ---------------------------------------------
    Label("vulnerable/net_tools.py", 7, "CWE-78", "command-injection"),
    Label("vulnerable/net_tools.py", 11, "CWE-78", "command-injection"),
    # --- Path traversal ----------------------------------------------------
    Label("vulnerable/downloads.py", 8, "CWE-22", "path-traversal"),
    # --- Unsafe deserialization / eval ------------------------------------
    Label("vulnerable/session_store.py", 6, "CWE-502", "insecure-deserialization"),
    Label("vulnerable/session_store.py", 10, "CWE-95", "code-injection"),
    # --- SSRF --------------------------------------------------------------
    Label("vulnerable/webhook.py", 6, "CWE-918", "ssrf"),
    # --- Weak crypto + hardcoded secret -----------------------------------
    Label("vulnerable/passwords.py", 4, "CWE-798", "hardcoded-secret"),
    Label("vulnerable/passwords.py", 8, "CWE-327", "weak-hash"),
    # --- XSS ---------------------------------------------------------------
    Label("vulnerable/render.js", 3, "CWE-79", "xss"),
    Label("vulnerable/render.js", 7, "CWE-79", "xss"),
    # --- Command injection (node) -----------------------------------------
    Label("vulnerable/build.js", 5, "CWE-78", "command-injection"),
    # --- Cross-file: the sanitiser is incomplete --------------------------
    # Only wrong once you read clean() in sanitize_util.py: stripping single
    # quotes leaves backslash and comment payloads intact. A per-file analyser
    # sees a "sanitised" value and a helper that looks protective.
    Label("vulnerable/reports.py", 9, "CWE-89", "sql-injection"),
]

SAFE_PATHS: list[str] = [
    "safe/db_users_safe.py",
    "safe/net_tools_safe.py",
    "safe/downloads_safe.py",
    "safe/session_store_safe.py",
    "safe/webhook_safe.py",
    "safe/passwords_safe.py",
    "safe/render_safe.js",
    "safe/build_safe.js",
    # Cross-file counterpart: the helper hands the driver a bind parameter,
    # so the same shape is genuinely safe.
    "safe/escape_util_safe.py",
    "safe/reports_safe.py",
]

CORPUS = Corpus(vulnerable=LABELS, safe_paths=SAFE_PATHS)
