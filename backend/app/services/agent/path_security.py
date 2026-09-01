"""Shared workspace path validation for agent/runtime file access.

All paths supplied by users, models, or repository-derived metadata must stay
inside an explicitly trusted workspace root.  String-prefix checks are not
sufficient because sibling paths (``/tmp/repo-secret``) and symlinks can bypass
them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union

PathLike = Union[str, Path]


class WorkspacePathError(ValueError):
    """Raised when a candidate path escapes or is invalid for a workspace."""


def normalize_relative_workspace_path(candidate: PathLike, *, allow_dot: bool = True) -> str:
    """Normalize and validate an untrusted relative workspace path.

    Reject absolute/host paths, Windows drive paths, home expansion, and any
    parent traversal segment.  The returned path always uses POSIX separators.
    """

    raw = str(candidate).strip().replace("\\", "/")
    if not raw:
        raise WorkspacePathError("workspace path must not be blank")
    if raw.startswith(("/", "~", "//")):
        raise WorkspacePathError(f"absolute or host path is not allowed: {raw!r}")
    if len(raw) > 1 and raw[1] == ":":
        raise WorkspacePathError(f"drive path is not allowed: {raw!r}")

    parts = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            raise WorkspacePathError(f"parent traversal is not allowed: {raw!r}")
        parts.append(part)

    if not parts:
        if allow_dot:
            return "."
        raise WorkspacePathError("workspace path must identify a child path")
    return "/".join(parts)


def resolve_under_workspace(
    workspace_root: PathLike,
    candidate: PathLike,
    *,
    must_exist: bool = False,
) -> Path:
    """Resolve ``candidate`` and require the result to remain under ``workspace_root``.

    ``Path.resolve(strict=False)`` resolves existing symlinks, which prevents a
    symlink inside the workspace from redirecting a model-controlled read to a
    sibling or host path.
    """

    relative = normalize_relative_workspace_path(candidate)
    root = Path(workspace_root).expanduser().resolve(strict=False)
    target = (root / relative).resolve(strict=False)

    try:
        target.relative_to(root)
    except ValueError as exc:
        raise WorkspacePathError(
            f"path escapes workspace root: {candidate!r}"
        ) from exc

    if must_exist and not target.exists():
        raise WorkspacePathError(f"workspace path does not exist: {candidate!r}")
    return target
