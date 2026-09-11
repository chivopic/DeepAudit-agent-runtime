"""LangGraph checkpointer factory.

A checkpoint is only worth having if it outlives the process that wrote it.
``MemorySaver`` is a dict: fine for tests, and useless for recovery — which is
what the default was in production, so a crash meant re-running from zero.

Backends:
    memory    in-process dict. Tests and local development.
    postgres  survives a restart. What production wants.
    sqlite    file-backed, single-process.
    auto      postgres if a DSN is configured, else memory.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)

# Remember a DSN that could not be reached, so every subsequent runner in this
# process fails over immediately instead of paying the connect timeout again.
# Keyed by DSN: a different database gets its own chance.
_unreachable: set[str] = set()


def forget_unreachable_dsns() -> None:
    """Clear the negative cache (tests, or after fixing connectivity)."""
    _unreachable.clear()

CheckpointerBackend = Literal["memory", "sqlite", "postgres", "auto"]


def settings_or_default() -> Any:
    """Settings when the app is importable; otherwise an empty stand-in."""
    try:
        from app.core.config import settings

        return settings
    except Exception:  # noqa: BLE001
        return object()


class CheckpointerFactory:
    """Create checkpointers without coupling nodes to a concrete backend."""

    def __init__(
        self,
        backend: CheckpointerBackend = "auto",
        *,
        sqlite_path: Optional[str] = None,
        dsn: Optional[str] = None,
    ) -> None:
        self.backend = backend
        self.sqlite_path = sqlite_path or ":memory:"
        self.dsn = dsn

    def create(self) -> Any:
        """Synchronous creation. Postgres needs ``acreate`` — it opens a pool."""
        if self.backend == "memory":
            return self._memory()
        if self.backend == "sqlite":
            return self._sqlite()
        if self.backend == "postgres":
            raise RuntimeError(
                "the postgres checkpointer is async; use acreate()"
            )
        # auto without an event loop cannot open a pool, so fall back.
        try:
            return self._sqlite()
        except Exception as exc:  # noqa: BLE001
            logger.debug("sqlite checkpointer unavailable (%s); using memory", exc)
            return self._memory()

    async def acreate(self) -> Any:
        """Create the checkpointer, opening a connection pool when needed.

        ``postgres`` is explicit and fails loudly: asking for durability and
        silently getting a dict is how a crash turns into lost work.

        ``auto`` is best-effort — it tries Postgres and falls back to memory —
        but it says so at WARNING. Silent degradation is its own bug class.
        """
        backend = self.backend
        if backend == "postgres":
            return await self._postgres()
        if backend == "sqlite":
            return self._sqlite()
        dsn = self._resolve_dsn() if backend == "auto" else None
        if dsn and dsn in _unreachable:
            logger.debug("checkpoint DSN known-unreachable; using memory")
            return self._memory()
        if backend == "auto" and dsn:
            try:
                return await self._postgres()
            except Exception as exc:  # noqa: BLE001
                _unreachable.add(dsn)
                logger.warning(
                    "checkpoint backend 'auto': Postgres unavailable (%s). "
                    "Falling back to in-process memory — checkpoints will NOT "
                    "survive a restart. Set AGENT_CHECKPOINT_BACKEND=postgres "
                    "to make this a hard failure instead.",
                    exc,
                )
        return self._memory()

    def _resolve_dsn(self) -> Optional[str]:
        """Postgres DSN for the checkpointer.

        The application speaks SQLAlchemy (``postgresql+asyncpg://``); psycopg
        does not understand that dialect prefix, so it is stripped rather than
        passed through — handing the driver a URL it cannot parse is the
        obvious way to get a confusing failure at startup.
        """
        raw = self.dsn
        if not raw:
            from app.core.config import settings

            raw = getattr(settings, "AGENT_CHECKPOINT_DSN", None) or getattr(
                settings, "DATABASE_URL", None
            )
        if not raw:
            return None
        for prefix, replacement in (
            ("postgresql+asyncpg://", "postgresql://"),
            ("postgresql+psycopg://", "postgresql://"),
            ("postgres+asyncpg://", "postgresql://"),
        ):
            if raw.startswith(prefix):
                raw = replacement + raw[len(prefix) :]
                break
        if not raw.startswith(("postgresql://", "postgres://")):
            return None
        return raw

    async def _postgres(self) -> Any:
        dsn = self._resolve_dsn()
        if not dsn:
            raise RuntimeError(
                "postgres checkpointer requested but no usable DSN "
                "(set AGENT_CHECKPOINT_DSN or DATABASE_URL)"
            )
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        # autocommit is required: the saver issues its own transactions.
        # Short timeouts on purpose: an unreachable database should surface as
        # an error in seconds, not block a request for half a minute.
        connect_timeout = int(
            getattr(settings_or_default(), "AGENT_CHECKPOINT_CONNECT_TIMEOUT", 5) or 5
        )
        pool = AsyncConnectionPool(
            conninfo=dsn,
            max_size=int(getattr(self, "pool_size", 0) or 10),
            open=False,
            timeout=connect_timeout,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "connect_timeout": connect_timeout,
            },
        )
        await pool.open(wait=True, timeout=connect_timeout)
        saver = AsyncPostgresSaver(pool)
        # Idempotent; creates the checkpoint tables on first use.
        await saver.setup()
        # The pool keeps the loop alive until it is closed, so hand callers a
        # way to release it. Without this a short-lived process (a test, a
        # worker) hangs at exit instead of finishing.
        saver.aclose_pool = pool.close  # type: ignore[attr-defined]
        return saver

    @staticmethod
    def _memory() -> Any:
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

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
    dsn: Optional[str] = None,
) -> Any:
    return CheckpointerFactory(
        backend=backend, sqlite_path=sqlite_path, dsn=dsn
    ).create()


async def acreate_checkpointer(
    backend: CheckpointerBackend = "auto",
    *,
    sqlite_path: Optional[str] = None,
    dsn: Optional[str] = None,
) -> Any:
    """Async factory. Required for the postgres backend, which opens a pool."""
    return await CheckpointerFactory(
        backend=backend, sqlite_path=sqlite_path, dsn=dsn
    ).acreate()
