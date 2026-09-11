"""Shared input validators."""


def is_safe_path(candidate: str) -> bool:
    """Reject attempts to climb out of a directory."""
    return ".." not in candidate
