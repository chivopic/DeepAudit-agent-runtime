"""Deterministic, symlink-averse local workspace discovery."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from deepaudit_cli.domain import Issue, Manifest

SUPPORTED = {".py": "python", ".pyi": "python"}
SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
}


def resolve_workspace(value: Path) -> Path:
    root = value.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"workspace is not a directory: {value}")
    return root


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(4096)


def build_manifest(
    root: Path,
    *,
    include: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
    max_files: int = 500,
    max_target_bytes: int = 1024 * 1024,
) -> Manifest:
    if max_files <= 0 or max_target_bytes <= 0:
        raise ValueError("manifest limits must be positive")
    root = resolve_workspace(root)
    selected: list[str] = []
    issues: list[Issue] = []
    discovered = 0
    languages: set[str] = set()
    limit_reported = False

    def walk(directory: Path) -> None:
        nonlocal discovered, limit_reported
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            relative = directory.relative_to(root).as_posix() or "."
            issues.append(Issue("unreadable_directory", str(exc), relative))
            return
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                if entry.is_symlink():
                    issues.append(Issue("symlink", "symbolic links are not scanned", relative))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP_DIRS and not _matches(relative, exclude):
                        walk(path)
                    continue
                if not entry.is_file(follow_symlinks=False) or path.suffix.lower() not in SUPPORTED:
                    continue
                discovered += 1
                if include and not _matches(relative, include):
                    continue
                if _matches(relative, exclude):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
                if size > max_target_bytes:
                    issues.append(
                        Issue("target_too_large", f"target exceeds {max_target_bytes} bytes", relative)
                    )
                    continue
                if _is_binary(path):
                    issues.append(Issue("binary_file", "binary file is not scanned", relative))
                    continue
                if len(selected) >= max_files:
                    if not limit_reported:
                        issues.append(
                            Issue("file_limit", f"selected files exceed limit {max_files}")
                        )
                        limit_reported = True
                    continue
                selected.append(relative)
                languages.add(SUPPORTED[path.suffix.lower()])
            except OSError as exc:
                issues.append(Issue("unreadable_file", str(exc), relative))

    walk(root)
    limited = any(issue.code == "file_limit" for issue in issues)
    incomplete = bool(issues)
    return Manifest(
        files=tuple(selected),
        discovered_files=discovered,
        issues=tuple(issues),
        limited=limited,
        incomplete=incomplete,
        languages=tuple(sorted(languages)),
    )
