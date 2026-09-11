"""Cross-file context for analysis.

A guard defined in another file cannot be judged from the call site alone: the
call looks defended and the model has to guess. These cover resolving the local
imports, the budget that keeps it affordable, and the jail that keeps a crafted
import string from becoming a path traversal.
"""

from __future__ import annotations

import pytest

from app.services.agent.graph.nodes import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_MODULES,
    _load_import_context,
    _local_imports,
)

# ---------------------------------------------------------------------------
# Resolving local imports
# ---------------------------------------------------------------------------


def test_python_relative_import_resolves_inside_the_package():
    assert _local_imports("pkg/app.py", "from .validators import is_safe_path") == [
        "pkg/validators.py"
    ]


def test_python_parent_relative_import_walks_up():
    assert _local_imports("pkg/sub/app.py", "from ..policy import host_allowed") == [
        "pkg/policy.py"
    ]


def test_javascript_relative_import_and_require():
    assert "src/util.js" in _local_imports("src/render.js", "import {x} from './util'")
    assert "lib/b.js" in _local_imports("src/a.js", "const b = require('../lib/b')")


def test_a_file_does_not_import_itself():
    assert _local_imports("pkg/app.py", "from .app import x") == []


def test_duplicate_imports_are_collapsed():
    content = "from .util import a\nfrom .util import b\n"
    assert _local_imports("pkg/app.py", content) == ["pkg/util.py"]


def test_unknown_language_yields_nothing():
    assert _local_imports("README.md", "from .x import y") == []


# ---------------------------------------------------------------------------
# Loading, budget, and the jail
# ---------------------------------------------------------------------------


def test_context_comes_from_fixture_files_when_there_is_no_disk():
    ctx = _load_import_context(
        "pkg/app.py",
        "from .validators import is_safe_path",
        fixture_files={"pkg/validators.py": "def is_safe_path(p): return True"},
        root=None,
    )
    assert ctx == [("pkg/validators.py", "def is_safe_path(p): return True")]


def test_imports_that_do_not_exist_are_skipped_not_faked():
    """Third-party imports resolve to nothing and must not consume budget."""
    ctx = _load_import_context(
        "pkg/app.py",
        "import os\nimport requests\nfrom .validators import x",
        fixture_files={"pkg/validators.py": "guard"},
        root=None,
    )
    assert ctx == [("pkg/validators.py", "guard")]


def test_module_budget_is_enforced():
    content = "".join(f"from .m{i} import x\n" for i in range(10))
    fixtures = {f"pkg/m{i}.py": f"body{i}" for i in range(10)}
    ctx = _load_import_context(
        "pkg/app.py", content, fixture_files=fixtures, root=None
    )
    assert len(ctx) == MAX_CONTEXT_MODULES


def test_each_module_is_truncated():
    ctx = _load_import_context(
        "pkg/app.py",
        "from .big import x",
        fixture_files={"pkg/big.py": "x" * (MAX_CONTEXT_CHARS * 3)},
        root=None,
    )
    assert len(ctx[0][1]) == MAX_CONTEXT_CHARS


def test_context_reads_from_disk_through_the_workspace(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "validators.py").write_text("def is_safe_path(p): return '..' not in p")

    ctx = _load_import_context(
        "pkg/app.py",
        "from .validators import is_safe_path",
        fixture_files={},
        root=tmp_path,
    )
    assert ctx and "is_safe_path" in ctx[0][1]


@pytest.mark.parametrize(
    "statement",
    [
        "from ........ import secrets",
        "from ..... import x",
    ],
)
def test_a_crafted_python_import_cannot_escape_the_workspace(tmp_path, statement):
    """Import strings are attacker-influenced content, not trusted paths."""
    outside = tmp_path.parent / "outside-secret.py"
    outside.write_text("SECRET = 'do not read me'")

    ctx = _load_import_context(
        "pkg/app.py", statement, fixture_files={}, root=tmp_path
    )
    assert all("do not read me" not in body for _, body in ctx)


def test_a_crafted_js_require_cannot_escape_the_workspace(tmp_path):
    outside = tmp_path.parent / "outside.js"
    outside.write_text("// do not read me")

    ctx = _load_import_context(
        "src/a.js",
        "require('../../../../outside')",
        fixture_files={},
        root=tmp_path,
    )
    assert all("do not read me" not in body for _, body in ctx)


def test_no_imports_means_no_context_and_no_cost():
    assert _load_import_context("a.py", "x = 1", fixture_files={}, root=None) == []
