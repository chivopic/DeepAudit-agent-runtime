"""Eval fixture cases (deterministic, no network)."""

from __future__ import annotations

from tests.evals.evaluators import EvalCase


def python_vulnerable_sqli() -> EvalCase:
    return EvalCase(
        id="py-vuln-sqli",
        language="python",
        category="vulnerable",
        files={
            "app/db.py": (
                "def get_user(conn, user_id):\n"
                "    q = \"SELECT * FROM users WHERE id = \" + user_id\n"
                "    return conn.execute(q)\n"
            ),
            "app/main.py": "from app.db import get_user\n",
        },
        expected_min_findings=1,
        expected_cwe_any=["CWE-89", "89"],
        expected_paths=["app/db.py"],
        notes="classic string-concat SQL",
    )


def python_safe_param() -> EvalCase:
    return EvalCase(
        id="py-safe-param",
        language="python",
        category="safe",
        files={
            "app/db.py": (
                "def get_user(conn, user_id):\n"
                "    # parameterized — no string-concat SQL, no shell\n"
                "    return conn.execute('SELECT id, name FROM users WHERE id = %s', (user_id,))\n"
            ),
            "app/util.py": "def add(a, b):\n    return a + b\n",
        },
        expected_min_findings=0,
        expected_paths=[],
        notes="parameterized query — no required findings",
    )


def js_vulnerable_xss() -> EvalCase:
    return EvalCase(
        id="js-vuln-xss",
        language="javascript",
        category="vulnerable",
        files={
            "src/view.js": (
                "function render(user) {\n"
                "  document.getElementById('x').innerHTML = user.bio;\n"
                "}\n"
            ),
        },
        expected_min_findings=1,
        expected_paths=["src/view.js"],
        notes="innerHTML sink",
    )


def mixed_command_injection() -> EvalCase:
    return EvalCase(
        id="mixed-cmd",
        language="python",
        category="vulnerable",
        files={
            "tools/run.py": (
                "import os\n"
                "def run(cmd):\n"
                "    os.system(cmd)\n"
            ),
        },
        expected_min_findings=1,
        expected_cwe_any=["CWE-78", "78"],
        expected_paths=["tools/run.py"],
    )


ALL_CASES: list[EvalCase] = [
    python_vulnerable_sqli(),
    python_safe_param(),
    js_vulnerable_xss(),
    mixed_command_injection(),
]


# Small CI set: one vuln + one safe
CI_CASES: list[EvalCase] = [
    python_vulnerable_sqli(),
    python_safe_param(),
]
