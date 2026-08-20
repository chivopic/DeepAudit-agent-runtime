"""Finding and evidence domain models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import ArtifactRef, SourceLocation
from .enums import (
    EvidenceLevel,
    FindingState,
    FindingStatus,
    Severity,
    VerificationStatus,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


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
    location: SourceLocation | None = None
    snippet: str | None = Field(default=None, max_length=20_000)
    artifact: ArtifactRef | None = None
    level: EvidenceLevel = EvidenceLevel.E0
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
    category: str | None = None
    cwe_id: str | None = None
    owasp: str | None = None
    location: SourceLocation | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    counter_evidence: list[Evidence] = Field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.E0
    finding_state: FindingState = FindingState.CANDIDATE
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    analyzer: str | None = None
    rule_id: str | None = None
    source_task_id: str | None = None
    fingerprint: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title")
    @classmethod
    def title_strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v


class ReasoningArtifact(BaseModel):
    """Explanation that references Evidence but cannot itself become Evidence."""

    id: str = Field(default_factory=lambda: _new_id("reason"))
    finding_id: str = Field(..., min_length=1)
    evidence_summary: str = Field(..., min_length=1, max_length=20_000)
    preconditions: list[str] = Field(default_factory=list)
    impact: str | None = Field(default=None, max_length=20_000)
    limitations: list[str] = Field(default_factory=list)
    recommendation: str | None = Field(default=None, max_length=20_000)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    counter_evidence_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    context_hash: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Finding(BaseModel):
    """Canonical finding after dedupe/aggregate; API-facing domain object."""

    id: str = Field(default_factory=lambda: _new_id("fnd"))
    audit_id: str | None = None
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    severity: Severity = Severity.MEDIUM
    status: FindingStatus = FindingStatus.NEW
    verification_status: VerificationStatus = VerificationStatus.NOT_RUN
    category: str | None = None
    cwe_id: str | None = None
    owasp: str | None = None
    location: SourceLocation | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    counter_evidence: list[Evidence] = Field(default_factory=list)
    evidence_level: EvidenceLevel = EvidenceLevel.E0
    finding_state: FindingState = FindingState.CANDIDATE
    reasoning_artifacts: list[ReasoningArtifact] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_score: float = Field(default=0.0, ge=0.0, le=100.0)
    analyzer: str | None = None
    rule_id: str | None = None
    fingerprint: str | None = None
    duplicate_of: str | None = None
    recommendation: str | None = None
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
        if self.finding_state is FindingState.VERIFIED:
            if self.evidence_level is not EvidenceLevel.E3:
                raise ValueError("verified finding_state requires evidence_level=E3")
            if self.verification_status is not VerificationStatus.CONFIRMED:
                raise ValueError("verified finding_state requires verification_status=confirmed")
        if self.evidence_level is EvidenceLevel.E3 and (
            self.finding_state is not FindingState.VERIFIED
            or self.verification_status is not VerificationStatus.CONFIRMED
        ):
            raise ValueError("evidence_level=E3 requires verified state and confirmed verification")
        if self.finding_state is FindingState.CORROBORATED and self.evidence_level not in {
            EvidenceLevel.E2,
            EvidenceLevel.E3,
        }:
            raise ValueError("corroborated finding_state requires evidence_level E2 or E3")
        return self

    def with_status(self, status: FindingStatus) -> Finding:
        return self.model_copy(update={"status": status, "updated_at": _utc_now()})

    def with_verification(self, vs: VerificationStatus) -> Finding:
        return self.model_copy(update={"verification_status": vs, "updated_at": _utc_now()})


class VerifiedFinding(Finding):
    """Finding enriched with verification outcome (M7+)."""

    verification_notes: str | None = None
    verification_artifact: ArtifactRef | None = None
    verified_at: datetime | None = None
    repro_steps: str | None = None
    false_positive_reason: str | None = None
