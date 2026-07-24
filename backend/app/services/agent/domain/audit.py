"""Audit request and report domain models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from .common import ModelUsage, RunBudget
from .enums import AuditStatus, Severity
from .finding import Finding
from .plan import AuditPlan
from .repository import RepositoryRef


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class AuditRequest(BaseModel):
    """Validated input to start an agent audit run."""

    id: str = Field(default_factory=lambda: _new_id("aud"))
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    repository: RepositoryRef
    title: Optional[str] = None
    description: Optional[str] = None
    languages: list[str] = Field(default_factory=list)
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    focus_rules: list[str] = Field(default_factory=list)
    severity_threshold: Severity = Severity.INFO
    enable_verification: bool = False
    enable_rag: bool = True
    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    budget: RunBudget = Field(default_factory=RunBudget)
    config: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)

    @field_validator("include_paths", "exclude_paths", mode="before")
    @classmethod
    def normalize_path_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if not isinstance(v, list):
            raise ValueError("path lists must be lists of strings")
        out: list[str] = []
        for p in v:
            s = str(p).strip().replace("\\", "/")
            if not s:
                continue
            if s.startswith("/") or ".." in s.split("/") or (len(s) > 1 and s[1] == ":"):
                raise ValueError(f"unsafe or absolute path in filter: {s!r}")
            out.append(s)
        return out

    @model_validator(mode="after")
    def phase1_verification_default(self) -> AuditRequest:
        # Document invariant: Phase 1 acceptance keeps verification off unless
        # explicitly enabled; callers still may set True for M7 experiments.
        return self


class AuditReportSection(BaseModel):
    """One section of a generated audit report."""

    id: str = Field(default_factory=lambda: _new_id("sec"))
    title: str = Field(..., min_length=1)
    body_markdown: str = Field(default="")
    finding_ids: list[str] = Field(default_factory=list)
    order: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditReport(BaseModel):
    """Final audit report aggregate."""

    id: str = Field(default_factory=lambda: _new_id("rep"))
    audit_id: str
    title: str = Field(default="Security Audit Report")
    status: AuditStatus = AuditStatus.COMPLETED
    summary: str = Field(default="")
    sections: list[AuditReportSection] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    plan: Optional[AuditPlan] = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    severity_counts: dict[str, int] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    completed_at: Optional[datetime] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def recount_severities(self) -> AuditReport:
        counts: dict[str, int] = {}
        for f in self.findings:
            key = f.severity.value if isinstance(f.severity, Severity) else str(f.severity)
            counts[key] = counts.get(key, 0) + 1
        return self.model_copy(update={"severity_counts": counts})
