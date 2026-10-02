"""LangGraph checkpointer factory (M3).

Default: in-process MemorySaver (dev/tests).
Optional: Sqlite via ``langgraph-checkpoint-sqlite`` if installed.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

CheckpointerBackend = Literal["memory", "sqlite", "file", "postgres", "auto"]


class CheckpointerFactory:
    """Create checkpointers without coupling nodes to a concrete backend."""

    def __init__(
        self,
        backend: CheckpointerBackend = "auto",
        *,
        sqlite_path: Optional[str] = None,
        path: Optional[str] = None,
    ) -> None:
        self.backend = backend
        self.sqlite_path = sqlite_path or ":memory:"
        self.path = path

    def create(self) -> Any:
        if self.backend == "memory":
            return self._memory()
        if self.backend == "file":
            return self._file()
        if self.backend == "postgres":
            raise RuntimeError(
                "postgres checkpointer is not available; set AGENT_CHECKPOINT_BACKEND=file"
            )
        if self.backend == "sqlite":
            return self._sqlite()
        # auto: prefer sqlite package if present, else memory
        try:
            return self._sqlite()
        except Exception as exc:  # noqa: BLE001
            logger.debug("sqlite checkpointer unavailable (%s); using memory", exc)
            return self._memory()

    @staticmethod
    def _memory() -> Any:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    def _file(self) -> Any:
        if not self.path:
            raise RuntimeError("file checkpointer requires a path")
        from app.services.agent.persistence.file_checkpointer import FileCheckpointSaver

        return FileCheckpointSaver(self.path)

    def _sqlite(self) -> Any:
        # Optional extra — may not be installed in this repo pin.
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver  # type: ignore
        except ImportError as e:
            raise ImportError(
                "langgraph-checkpoint-sqlite not installed; use backend='memory'"
            ) from e
        # SqliteSaver.from_conn_string is sync context manager in some versions
        if hasattr(SqliteSaver, "from_conn_string"):
            # Keep connection open for process lifetime in M3 tests via raw ctor when possible
            import sqlite3

            conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            return SqliteSaver(conn)
        raise RuntimeError("SqliteSaver API not recognized")


def create_checkpointer(
    backend: CheckpointerBackend = "memory",
    *,
    sqlite_path: Optional[str] = None,
    path: Optional[str] = None,
) -> Any:
    return CheckpointerFactory(backend=backend, sqlite_path=sqlite_path, path=path).create()
