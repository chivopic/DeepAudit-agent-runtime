"""Shared input validators."""
import os


def resolve_within(base: str, candidate: str) -> str:
    """Resolve candidate under base, or refuse. Symlinks resolved first."""
    root = os.path.realpath(base)
    target = os.path.realpath(os.path.join(root, candidate))
    if target != root and not target.startswith(root + os.sep):
        raise PermissionError("path escapes the base directory")
    return target
