"""Artifact store for large payloads (snippets, logs, reports)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable
from uuid import uuid4

from app.services.agent.domain import ArtifactKind, ArtifactRef


@runtime_checkable
class ArtifactStore(Protocol):
    async def put(
        self,
        content: bytes | str,
        *,
        kind: ArtifactKind = ArtifactKind.OTHER,
        media_type: Optional[str] = None,
        audit_id: Optional[str] = None,
        suffix: str = ".bin",
    ) -> ArtifactRef: ...

    async def get_bytes(self, ref: ArtifactRef) -> bytes: ...


class InMemoryArtifactStore:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def put(
        self,
        content: bytes | str,
        *,
        kind: ArtifactKind = ArtifactKind.OTHER,
        media_type: Optional[str] = None,
        audit_id: Optional[str] = None,
        suffix: str = ".bin",
    ) -> ArtifactRef:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        art_id = f"art_{uuid4().hex[:12]}"
        uri = f"memory://{art_id}{suffix}"
        self._data[uri] = raw
        return ArtifactRef(
            id=art_id,
            kind=kind,
            uri=uri,
            media_type=media_type,
            byte_size=len(raw),
            content_hash=hashlib.sha256(raw).hexdigest(),
            audit_id=audit_id,
        )

    async def get_bytes(self, ref: ArtifactRef) -> bytes:
        if ref.uri not in self._data:
            raise FileNotFoundError(ref.uri)
        return self._data[ref.uri]


class FilesystemArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _safe_audit_segment(audit_id: Optional[str]) -> str:
        raw = (audit_id or "_global").strip()
        # Allow only simple id chars — reject path segments
        safe = "".join(c for c in raw if c.isalnum() or c in {"_", "-", "."})
        return safe or "_global"

    def _assert_under_root(self, path: Path) -> Path:
        resolved = path.resolve()
        if hasattr(resolved, "is_relative_to"):
            if not resolved.is_relative_to(self.root):
                raise PermissionError(f"artifact path escapes root: {resolved}")
        elif self.root not in resolved.parents and resolved != self.root:
            raise PermissionError(f"artifact path escapes root: {resolved}")
        return resolved

    async def put(
        self,
        content: bytes | str,
        *,
        kind: ArtifactKind = ArtifactKind.OTHER,
        media_type: Optional[str] = None,
        audit_id: Optional[str] = None,
        suffix: str = ".bin",
    ) -> ArtifactRef:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        art_id = f"art_{uuid4().hex[:12]}"
        if not suffix.startswith("."):
            suffix = "." + suffix
        # sanitize suffix (no path separators)
        suffix = "." + "".join(c for c in suffix[1:] if c.isalnum())[:16]
        sub = self.root / self._safe_audit_segment(audit_id)
        sub.mkdir(parents=True, exist_ok=True)
        path = self._assert_under_root(sub / f"{art_id}{suffix}")
        path.write_bytes(raw)
        return ArtifactRef(
            id=art_id,
            kind=kind,
            uri=str(path),
            media_type=media_type,
            byte_size=len(raw),
            content_hash=hashlib.sha256(raw).hexdigest(),
            audit_id=audit_id,
        )

    async def get_bytes(self, ref: ArtifactRef) -> bytes:
        path = self._assert_under_root(Path(ref.uri))
        if not path.is_file():
            raise FileNotFoundError(ref.uri)
        return path.read_bytes()
