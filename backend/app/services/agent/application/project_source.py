"""Read an authorized project into a bounded snapshot with explicit coverage."""

from __future__ import annotations

import fnmatch
import os
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

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
    ".swift",
    ".kt",
    ".scala",
    ".sql",
    ".sh",
}


class ProjectSourceError(ValueError):
    """The project or a requested path cannot be read within the authorized scope."""


class SourceIssue(BaseModel):
    path: str
    reason: str


class AuthorizedSnapshot(BaseModel):
    """Source is stored as an artifact; only coverage goes into graph state."""

    files: dict[str, str] = Field(default_factory=dict)
    discovered_files: int = 0
    loaded_bytes: int = 0
    max_files: int
    max_bytes: int
    issues: list[SourceIssue] = Field(default_factory=list)

    def coverage(self) -> dict[str, Any]:
        return {
            "discovered_files": self.discovered_files,
            "loaded_files": len(self.files),
            "loaded_bytes": self.loaded_bytes,
            "max_files": self.max_files,
            "max_bytes": self.max_bytes,
            "omitted_units": [issue.model_dump() for issue in self.issues],
        }


def _excluded(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    ancestors = ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]
    for raw in patterns:
        pattern = raw.replace("\\", "/").removeprefix("./").strip("/")
        if not pattern:
            continue
        candidates = ancestors if "/" in pattern else parts
        variants = [pattern]
        # A leading **/ also matches entries in the project root.
        while variants[-1].startswith("**/"):
            variants.append(variants[-1][3:])
        if any(fnmatch.fnmatchcase(part, pat) for part in candidates for pat in variants):
            return True
    return False


def load_authorized_snapshot(
    root: Path,
    *,
    max_files: int = 200,
    max_bytes: int = 2_000_000,
    target_files: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> AuthorizedSnapshot:
    """Inventory the requested scope, recording every unread or omitted file.

    Neither file nor directory symlinks are followed. ``target_files=[]`` is an
    empty selection; ``None`` selects all supported sources.
    """
    if max_files < 1 or max_bytes < 1:
        raise ProjectSourceError("max_files and max_bytes must be positive")
    try:
        root_r = root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ProjectSourceError("project root cannot be resolved") from exc
    if not root_r.is_dir():
        raise ProjectSourceError("project root is not a directory")
    patterns = list(exclude_patterns or [])
    wanted = None if target_files is None else {_relative(raw) for raw in target_files}
    snapshot = AuthorizedSnapshot(max_files=max_files, max_bytes=max_bytes)

    def in_scope(rel: str) -> bool:
        return not any(part in _SKIP_DIRS for part in Path(rel).parts) and not _excluded(
            rel, patterns
        )

    def walk_error(exc: OSError) -> None:
        try:
            rel = Path(exc.filename or root_r).relative_to(root_r).as_posix()
        except ValueError:
            rel = "."
        snapshot.issues.append(SourceIssue(path=rel, reason="unreadable_directory"))

    if wanted is not None:
        candidates = sorted(rel for rel in wanted if in_scope(rel))
    else:
        candidates = []
        for directory, dirs, names in os.walk(root_r, followlinks=False, onerror=walk_error):
            base = Path(directory)
            kept = []
            for name in sorted(dirs):
                path = base / name
                rel = path.relative_to(root_r).as_posix()
                if not in_scope(rel):
                    continue
                if path.is_symlink():
                    snapshot.issues.append(SourceIssue(path=rel, reason="symlink_directory"))
                    continue
                kept.append(name)
            dirs[:] = kept
            for name in names:
                rel = (base / name).relative_to(root_r).as_posix()
                if in_scope(rel) and Path(rel).suffix.lower() in _SOURCE_SUFFIXES:
                    candidates.append(rel)
        candidates.sort()

    snapshot.discovered_files = len(candidates)
    for rel in candidates:
        path = root_r / rel
        reason: str | None = None
        try:
            current = root_r
            if any((current := current / part).is_symlink() for part in Path(rel).parts):
                reason = "symlink"
            elif not path.resolve(strict=True).is_relative_to(root_r):
                reason = "unsafe_path"
            elif not stat.S_ISREG(path.stat().st_mode):
                reason = "not_regular_file"
            elif path.suffix.lower() not in _SOURCE_SUFFIXES:
                reason = "unsupported_file"
            elif len(snapshot.files) >= max_files:
                reason = "file_budget"
            elif path.stat().st_size > max_bytes - snapshot.loaded_bytes:
                reason = "byte_budget"
            else:
                # Never open a final symlink, even if replaced after inspection.
                fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(fd, "rb") as handle:
                    data = handle.read(max_bytes - snapshot.loaded_bytes + 1)
                if len(data) > max_bytes - snapshot.loaded_bytes:
                    reason = "byte_budget"
                elif b"\x00" in data:
                    reason = "binary_file"
                else:
                    try:
                        content = data.decode("utf-8")
                    except UnicodeDecodeError:
                        reason = "invalid_encoding"
                    else:
                        snapshot.files[rel] = content
                        snapshot.loaded_bytes += len(data)
        except FileNotFoundError:
            reason = "missing_file"
        except (OSError, RuntimeError, ValueError):
            reason = "unreadable"
        if reason:
            snapshot.issues.append(SourceIssue(path=rel, reason=reason))
    return snapshot


def _relative(raw: str) -> str:
    text = raw.replace("\\", "/").strip()
    if (
        not text
        or text.startswith("/")
        or ".." in text.split("/")
        or (len(text) > 1 and text[1] == ":")
        or "\x00" in text
    ):
        raise ProjectSourceError(f"target file escapes the project: {raw}")
    rel = Path(text).as_posix()
    if rel == ".":
        raise ProjectSourceError("target file must name a file")
    return rel
