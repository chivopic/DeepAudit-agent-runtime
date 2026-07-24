"""Audit plan and task specification models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from .enums import Severity


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class AuditTaskSpec(BaseModel):
    """A unit of analysis work within a plan (usually one file or path group)."""

    id: str = Field(default_factory=lambda: _new_id("task"))
    target_path: str = Field(..., min_length=1)
    language: Optional[str] = None
    analyzer: str = Field(
        default="static",
        description="static | llm | hybrid | taint | custom",
    )
    priority: float = Field(default=0.0, ge=0.0)
    focus_rules: list[str] = Field(default_factory=list)
    max_tokens: Optional[int] = Field(default=None, ge=0)
    depends_on: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_path")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        v = v.strip().replace("\\", "/")
        if not v:
            raise ValueError("target_path must not be blank")
        if v.startswith("/") or ".." in v.split("/") or (len(v) > 1 and v[1] == ":"):
            raise ValueError(f"unsafe or absolute target_path: {v!r}")
        return v

    @field_validator("analyzer")
    @classmethod
    def normalize_analyzer(cls, v: str) -> str:
        v = (v or "static").strip().lower()
        allowed = {"static", "llm", "hybrid", "taint", "custom"}
        if v not in allowed:
            raise ValueError(f"analyzer must be one of {sorted(allowed)}")
        return v


class AuditPlan(BaseModel):
    """Ordered/parallelizable plan produced by the planner node."""

    id: str = Field(default_factory=lambda: _new_id("plan"))
    audit_id: str
    tasks: list[AuditTaskSpec] = Field(default_factory=list)
    strategy: str = Field(
        default="file_parallel",
        description="file_parallel | sequential | risk_first",
    )
    max_parallel: int = Field(default=5, ge=1)
    focus_severities: list[Severity] = Field(default_factory=list)
    skip_paths: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    rationale: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("strategy")
    @classmethod
    def normalize_strategy(cls, v: str) -> str:
        v = (v or "file_parallel").strip().lower()
        allowed = {"file_parallel", "sequential", "risk_first"}
        if v not in allowed:
            raise ValueError(f"strategy must be one of {sorted(allowed)}")
        return v

    def task_count(self) -> int:
        return len(self.tasks)

    def sorted_by_priority(self) -> list[AuditTaskSpec]:
        return sorted(self.tasks, key=lambda t: t.priority, reverse=True)
