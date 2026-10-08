"""Versioned local-CLI result contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import RunBudget
from .enums import AuditStatus
from .finding import Finding


class CliAuditSummary(BaseModel):
    id: str
    status: AuditStatus
    mode: Literal["non_interactive"] = "non_interactive"


class CliScope(BaseModel):
    root_display: str
    files_selected: int = Field(default=0, ge=0)
    files_discovered: int = Field(default=0, ge=0)
    languages: list[str] = Field(default_factory=list)


class CliFindingView(Finding):
    """Canonical Finding plus a run-local human display alias."""

    display_id: str


class CliAuditEnvelope(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    audit: CliAuditSummary
    scope: CliScope
    coverage: dict[str, Any] = Field(default_factory=dict)
    budget: RunBudget
    findings: list[CliFindingView] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DoctorCheck(BaseModel):
    name: str
    status: Literal["ok", "error"]
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class DoctorEnvelope(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    status: Literal["ok", "error"]
    checks: list[DoctorCheck] = Field(default_factory=list)
