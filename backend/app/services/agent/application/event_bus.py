"""In-process event fan-out for graph runs (SSE-compatible, M4).

Does not replace EventManager; bridges AuditRunner events for tests and
future dual-path wiring without touching production ReAct streams.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator, Optional

from .api_mapping import graph_event_to_sse


class GraphEventBus:
    """Per-task async queues of SSE-shaped events."""

    def __init__(self) -> None:
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._seq: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def publish_raw(self, task_id: str, event: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._seq[task_id] += 1
            seq = self._seq[task_id]
        sse = graph_event_to_sse(event, task_id=task_id, sequence=seq)
        self._history[task_id].append(sse)
        for q in list(self._queues.get(task_id, [])):
            try:
                q.put_nowait(sse)
            except asyncio.QueueFull:
                pass
        return sse

    async def publish_many(
        self, task_id: str, events: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        out = []
        for e in events:
            out.append(await self.publish_raw(task_id, e))
        return out

    def history(self, task_id: str) -> list[dict[str, Any]]:
        return list(self._history.get(task_id, []))

    async def subscribe(self, task_id: str) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._queues[task_id].append(q)
        # replay history first
        for item in self.history(task_id):
            yield item
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            if q in self._queues[task_id]:
                self._queues[task_id].remove(q)

    async def close(self, task_id: str) -> None:
        for q in list(self._queues.get(task_id, [])):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass


# Process-local default bus (tests / dual-path)
_default_bus: Optional[GraphEventBus] = None


def get_graph_event_bus() -> GraphEventBus:
    global _default_bus
    if _default_bus is None:
        _default_bus = GraphEventBus()
    return _default_bus
