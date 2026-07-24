"""Finding and evidence domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import ArtifactRef, SourceLocation
from .enums import FindingStatus, Severity, VerificationStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Evidence(BaseModel):
    """Supporting evidence for a finding (snippet, log, tool output)."""

    id: str = Field(default_factory=lambda: _new_id("ev"))
    kind: str = Field(
        default="code",
        description="code | log | tool | model | config | other",
    )
    summary: str = Field(..., min_length=1)
    location: Optional[SourceLocation] = None
    snippet: Optional[str] = Field(default=None, max_length=20_000)
    artifact: Optional[ArtifactRef] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("kind")
    @classmethod
    def normalize_kind(cls, v: str) -> str:
        v = (v or "code").strip().lower()
        allowed = {"code", "log", "tool", "model", "config", "other"}
        if v not in allowed:
            raise ValueError(f"kind must be one of {sorted(allowed)}")
        return v


class CandidateFinding(BaseModel):
    """Pre-aggregation analyzer output; may be noisy or incomplete."""

    id: str = Field(default_factory=lambda: _new_id("cand"))
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    severity: Severity = Severity.MEDIUM
    category: Optional[str] = None
    cwe_id: Optional[str] = None
    owasp: Optional[str] = None
    location: Optional[SourceLocation] = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    analyzer: Optional[str] = None
    rule_id: Optional[str] = None
    source_task_id: Optional[str] = None
    fingerprint: Optional[str] = None
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def title_strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v


class Finding(BaseModel):
    """Canonical finding after dedupe/aggregate; API-facing domain object."""

    id: str = Field(default_factory=lambda: _new_id("fnd"))
    audit_id: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    severity: Severity = Severity.MEDIUM
    status: FindingStatus = FindingStatus.NEW
    verification_status: VerificationStatus = VerificationStatus.NOT_RUN
    category: Optional[str] = None
    cwe_id: Optional[str] = None
    owasp: Optional[str] = None
    location: Optional[SourceLocation] = None
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_score: float = Field(default=0.0, ge=0.0, le=100.0)
    analyzer: Optional[str] = None
    rule_id: Optional[str] = None
    fingerprint: Optional[str] = None
    duplicate_of: Optional[str] = None
    recommendation: Optional[str] = None
    references: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def title_strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v

    @model_validator(mode="after")
    def sync_updated(self) -> Finding:
        # Keep updated_at >= created_at for reconstructed objects.
        if self.updated_at < self.created_at:
            self.updated_at = self.created_at
        return self

    def with_status(self, status: FindingStatus) -> Finding:
        return self.model_copy(
            update={"status": status, "updated_at": _utc_now()}
        )

    def with_verification(self, vs: VerificationStatus) -> Finding:
        return self.model_copy(
            update={"verification_status": vs, "updated_at": _utc_now()}
        )


class VerifiedFinding(Finding):
    """Finding enriched with verification outcome (M7+)."""

    verification_notes: Optional[str] = None
    verification_artifact: Optional[ArtifactRef] = None
    verified_at: Optional[datetime] = None
    repro_steps: Optional[str] = None
    false_positive_reason: Optional[str] = None
