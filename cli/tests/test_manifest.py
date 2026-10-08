from __future__ import annotations

from pathlib import Path

from deepaudit_cli.manifest import build_manifest


def test_manifest_is_sorted_and_skips_dependency_trees(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("print('b')\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "readme.md").write_text("ignored\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("eval(x)\n", encoding="utf-8")

    manifest = build_manifest(tmp_path)

    assert manifest.files == ("a.py", "b.py")
    assert manifest.discovered_files == 2
    assert manifest.languages == ("python",)
    assert not manifest.incomplete


def test_manifest_reports_symlink_and_file_limit(tmp_path: Path) -> None:
    target = tmp_path / "a.py"
    target.write_text("print('a')\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("print('b')\n", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to(target)

    manifest = build_manifest(tmp_path, max_files=1)

    assert manifest.files == ("a.py",)
    assert manifest.incomplete
    assert {issue.code for issue in manifest.issues} == {"file_limit", "symlink"}


def test_manifest_filters_and_rejects_oversized_files(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "drop.py").write_text("x = 2\n", encoding="utf-8")
    (tmp_path / "large.py").write_text("x" * 50, encoding="utf-8")

    manifest = build_manifest(
        tmp_path,
        include=("*.py",),
        exclude=("drop.py",),
        max_target_bytes=20,
    )

    assert manifest.files == ("keep.py",)
    assert any(issue.code == "target_too_large" for issue in manifest.issues)
