"""Load source files from a server-owned project directory.

The caller must already have resolved the directory through the authorized
project checkout. This module refuses paths that escape that directory and
refuses to treat a client-supplied absolute path as the root.
"""

from __future__ import annotations

from pathlib import Path

_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
}
_SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".c",
    ".h",
    ".cpp",
    ".cs",
    ".sol",
    ".vue",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".xml",
    ".html",
    ".jsp",
}


class ProjectSourceError(ValueError):
    """The snapshot root is not an authorized directory."""


def load_authorized_snapshot(
    root: Path,
    *,
    max_files: int = 200,
    max_bytes: int = 2_000_000,
    target_files: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> dict[str, str]:
    """Read text files under ``root``. Keys are relative POSIX paths."""
    if max_files < 1:
        raise ProjectSourceError("max_files must be positive")
    try:
        root_r = root.resolve()
    except OSError as exc:
        raise ProjectSourceError("project root cannot be resolved") from exc
    if not root_r.is_dir():
        raise ProjectSourceError("project root is not a directory")

    excludes = [p for p in (exclude_patterns or []) if p and "*" not in p]
    wanted = None
    if target_files:
        wanted = set()
        for raw in target_files:
            rel = _relative(raw)
            if rel is None:
                raise ProjectSourceError(f"target file escapes the project: {raw}")
            wanted.add(rel)

    files: dict[str, str] = {}
    total = 0
    candidates = sorted(_walk(root_r))
    for path in candidates:
        rel = path.relative_to(root_r).as_posix()
        if wanted is not None and rel not in wanted:
            continue
        if any(part in excludes for part in Path(rel).parts):
            continue
        if path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        total += len(data)
        if total > max_bytes or len(files) >= max_files:
            break
        files[rel] = data.decode("utf-8", errors="replace")
    return files


def _relative(raw: str) -> str | None:
    text = raw.replace("\\", "/").strip()
    if (
        not text
        or text.startswith("/")
        or ".." in text.split("/")
        or (len(text) > 1 and text[1] == ":")
    ):
        return None
    return text.lstrip("./")


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        found.append(path)
    return found
