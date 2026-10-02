"""File-backed LangGraph checkpointer.

Each ``put`` flushes the MemorySaver tables to one file. A second process
opens the same path and continues the thread. ``postgres`` is not silently
replaced with this file: callers that ask for postgres and cannot build it
must raise before they get here.
"""

from __future__ import annotations

import os
import pickle
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver


class FileCheckpointSaver(MemorySaver):
    """MemorySaver that reloads its tables from ``path``."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Any,
        metadata: Any,
        new_versions: Any,
    ) -> RunnableConfig:
        saved = super().put(config, checkpoint, metadata, new_versions)
        self._dump()
        return saved

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        super().put_writes(config, writes, task_id, task_path)
        self._dump()

    def _dump(self) -> None:
        payload = {
            "storage": {
                thread_id: {ns: dict(checkpoints) for ns, checkpoints in namespaces.items()}
                for thread_id, namespaces in self.storage.items()
            },
            "writes": dict(self.writes),
            "blobs": dict(self.blobs),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)

    def _load(self) -> None:
        if not self.path.is_file():
            return
        with self.path.open("rb") as handle:
            payload = pickle.load(handle)  # noqa: S301 — our own checkpoint file
        storage: defaultdict[Any, Any] = defaultdict(lambda: defaultdict(dict))
        for thread_id, namespaces in (payload.get("storage") or {}).items():
            for ns, checkpoints in namespaces.items():
                storage[thread_id][ns].update(checkpoints)
        writes: defaultdict[Any, Any] = defaultdict(dict)
        writes.update(payload.get("writes") or {})
        self.storage = storage
        self.writes = writes
        self.blobs = dict(payload.get("blobs") or {})
