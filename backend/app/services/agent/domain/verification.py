"""Verification request/result and fix proposal models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .common import ArtifactRef
from .enums import ExecutionStatus, VerificationStatus


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class VerificationRequest(BaseModel):
    """Ask the verification subgraph to confirm or reject a finding."""

    id: str = Field(default_factory=lambda: _new_id("vreq"))
    finding_id: str = Field(..., min_length=1)
    audit_id: Optional[str] = None
    strategy: str = Field(
        default="static_recheck",
        description="static_recheck | sandbox | heuristic | human",
    )
    timeout_seconds: int = Field(default=120, ge=1, le=3600)
    allow_execution: bool = Field(
        default=False,
        description="Phase 1 must remain False (no untrusted exec)",
    )
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("strategy")
    @classmethod
    def normalize_strategy(cls, v: str) -> str:
        v = (v or "static_recheck").strip().lower()
        allowed = {"static_recheck", "sandbox", "heuristic", "human"}
        if v not in allowed:
            raise ValueError(f"strategy must be one of {sorted(allowed)}")
        return v


class VerificationResult(BaseModel):
    """Outcome of a verification attempt."""

    id: str = Field(default_factory=lambda: _new_id("vres"))
    request_id: str
    finding_id: str
    status: VerificationStatus = VerificationStatus.INCONCLUSIVE
    # Default SKIPPED so "not run / static only" cannot look like successful sandbox exec.
    execution_status: ExecutionStatus = ExecutionStatus.SKIPPED
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    summary: str = Field(default="", max_length=5000)
    details: dict[str, Any] = Field(default_factory=dict)
    artifact: Optional[ArtifactRef] = None
    latency_ms: Optional[int] = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)


class FixProposal(BaseModel):
    """Suggested remediation (patch text or high-level steps)."""

    id: str = Field(default_factory=lambda: _new_id("fix"))
    finding_id: str
    title: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1)
    patch_unified: Optional[str] = Field(
        default=None,
        max_length=200_000,
        description="Optional unified diff; never auto-applied in Phase 1",
    )
    steps: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    artifact: Optional[ArtifactRef] = None
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
