"""Repository and file inventory domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import ArtifactRef


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class RepositoryRef(BaseModel):
    """How to locate a repository (git URL, local path, or zip artifact)."""

    id: str = Field(default_factory=lambda: _new_id("repo"))
    source_type: str = Field(
        default="git",
        description="git | local | zip | upload",
    )
    url: Optional[str] = None
    local_path: Optional[str] = None
    branch: Optional[str] = None
    commit: Optional[str] = None
    default_branch: Optional[str] = None
    zip_artifact: Optional[ArtifactRef] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("source_type")
    @classmethod
    def normalize_source_type(cls, v: str) -> str:
        v = (v or "git").strip().lower()
        allowed = {"git", "local", "zip", "upload"}
        if v not in allowed:
            raise ValueError(f"source_type must be one of {sorted(allowed)}")
        return v

    @model_validator(mode="after")
    def require_locator(self) -> RepositoryRef:
        if self.source_type == "git" and not self.url:
            raise ValueError("git repository requires url")
        if self.source_type == "local" and not self.local_path:
            raise ValueError("local repository requires local_path")
        if self.source_type in {"zip", "upload"} and not (
            self.zip_artifact or self.local_path or self.url
        ):
            raise ValueError("zip/upload repository requires zip_artifact, local_path, or url")
        return self


class RepositorySnapshot(BaseModel):
    """Pinned checkout of a repository at a commit (or local snapshot id)."""

    id: str = Field(default_factory=lambda: _new_id("snap"))
    repository: RepositoryRef
    commit_sha: Optional[str] = None
    snapshot_path: Optional[str] = Field(
        default=None,
        description="Workspace-relative path to the checked-out tree",
    )
    created_at: datetime = Field(default_factory=_utc_now)
    file_count: int = Field(default=0, ge=0)
    total_bytes: int = Field(default=0, ge=0)
    languages: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FileArtifact(BaseModel):
    """A single file known to the audit (path + optional content ref)."""

    path: str = Field(..., min_length=1)
    language: Optional[str] = None
    byte_size: Optional[int] = Field(default=None, ge=0)
    line_count: Optional[int] = Field(default=None, ge=0)
    content_hash: Optional[str] = None
    content_ref: Optional[ArtifactRef] = None
    is_binary: bool = False
    is_generated: bool = False
    is_test: bool = False
    priority: float = Field(default=0.0, ge=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        v = v.strip().replace("\\", "/")
        if not v:
            raise ValueError("path must not be blank")
        if v.startswith("/") or ".." in v.split("/") or (len(v) > 1 and v[1] == ":"):
            raise ValueError(f"unsafe or absolute path: {v!r}")
        return v


class RepositoryManifest(BaseModel):
    """Inventory of files selected for audit after ingest/filter."""

    id: str = Field(default_factory=lambda: _new_id("man"))
    snapshot_id: str
    files: list[FileArtifact] = Field(default_factory=list)
    excluded_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    stats: dict[str, Any] = Field(default_factory=dict)

    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    def by_language(self) -> dict[str, list[FileArtifact]]:
        out: dict[str, list[FileArtifact]] = {}
        for f in self.files:
            key = f.language or "unknown"
            out.setdefault(key, []).append(f)
        return out

    def high_priority(self, min_priority: float = 1.0) -> list[FileArtifact]:
        return [f for f in self.files if f.priority >= min_priority]
