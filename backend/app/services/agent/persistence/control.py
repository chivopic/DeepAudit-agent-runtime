"""Cross-process run lease and ordered event log.

The file backend is the default that works without Redis. Selecting ``redis``
and failing to connect raises. It does not fall back to a process-local dict.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


class LeaseBusy(RuntimeError):
    """Another live owner holds this audit."""


class FileControlPlane:
    """Lease file plus an append-only JSONL event log, one directory per audit."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def acquire(self, audit_id: str, owner: str, *, ttl_seconds: int = 300) -> None:
        path = self._lease_path(audit_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(UTC)
        if path.is_file():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                current = {}
            expires = _parse_time(current.get("expires_at"))
            holder = current.get("owner")
            if holder and holder != owner and expires and expires > now:
                raise LeaseBusy(f"audit {audit_id} is owned by {holder}")
        payload = {
            "owner": owner,
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)

    def release(self, audit_id: str, owner: str) -> None:
        path = self._lease_path(audit_id)
        if not path.is_file():
            return
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if current.get("owner") == owner:
            path.unlink(missing_ok=True)

    def append_event(self, audit_id: str, event: dict[str, Any]) -> int:
        path = self._event_path(audit_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        sequence = 1
        if path.is_file():
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        sequence += 1
        body = dict(event)
        body["sequence"] = sequence
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(body) + "\n")
        return sequence

    def read_events(self, audit_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        path = self._event_path(audit_id)
        if not path.is_file():
            return []
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                item = json.loads(line)
                if int(item.get("sequence") or 0) > after_sequence:
                    out.append(item)
        return out

    def _lease_path(self, audit_id: str) -> Path:
        return self.root / _safe(audit_id) / "lease.json"

    def _event_path(self, audit_id: str) -> Path:
        return self.root / _safe(audit_id) / "events.jsonl"


def open_control_plane(
    backend: str, *, root: str | Path, redis_url: str | None = None
) -> FileControlPlane:
    """Build the control plane. ``redis`` fails closed when it cannot connect."""
    if backend in {"file", "memory"}:
        return FileControlPlane(root)
    if backend == "redis":
        if not redis_url:
            raise RuntimeError("AGENT_CONTROL_BACKEND=redis requires REDIS_URL")
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("redis control plane requested but redis is not installed") from exc
        client = redis.Redis.from_url(redis_url, socket_connect_timeout=2)
        client.ping()
        return _RedisControlPlane(client, prefix="deepaudit")
    raise RuntimeError(f"unknown control backend: {backend}")


class _RedisControlPlane(FileControlPlane):
    """Same methods, stored in Redis. Constructed only after a successful ping."""

    def __init__(self, client: Any, *, prefix: str) -> None:
        self.client = client
        self.prefix = prefix
        self.root = Path(".")

    def acquire(self, audit_id: str, owner: str, *, ttl_seconds: int = 300) -> None:
        key = f"{self.prefix}:lease:{audit_id}"
        current = self.client.get(key)
        if current:
            body = json.loads(current)
            if body.get("owner") not in {None, owner}:
                raise LeaseBusy(f"audit {audit_id} is owned by {body.get('owner')}")
        self.client.set(key, json.dumps({"owner": owner}), ex=ttl_seconds)

    def release(self, audit_id: str, owner: str) -> None:
        key = f"{self.prefix}:lease:{audit_id}"
        current = self.client.get(key)
        if not current:
            return
        body = json.loads(current)
        if body.get("owner") == owner:
            self.client.delete(key)

    def append_event(self, audit_id: str, event: dict[str, Any]) -> int:
        key = f"{self.prefix}:events:{audit_id}"
        sequence = int(self.client.incr(f"{key}:seq"))
        body = dict(event)
        body["sequence"] = sequence
        self.client.rpush(key, json.dumps(body))
        return sequence

    def read_events(self, audit_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        key = f"{self.prefix}:events:{audit_id}"
        raw_items = self.client.lrange(key, 0, -1)
        out = []
        for raw in raw_items:
            item = json.loads(raw)
            if int(item.get("sequence") or 0) > after_sequence:
                out.append(item)
        return out


def _safe(audit_id: str) -> str:
    cleaned = "".join(ch for ch in audit_id if ch.isalnum() or ch in {"-", "_"})
    return cleaned or "audit"


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
