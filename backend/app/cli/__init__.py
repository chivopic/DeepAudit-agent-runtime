"""DeepAudit command-line entrypoint."""

from __future__ import annotations

from typing import Any


def main(*args: Any, **kwargs: Any) -> int:
    """Import lazily so ``import app.cli`` does not load the audit runtime."""
    from .main import main as _main

    return _main(*args, **kwargs)


__all__ = ["main"]
