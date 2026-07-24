"""Shared domain value objects."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from .enums import ArtifactKind, NodeErrorCode


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class ArtifactRef(BaseModel):
    """Reference to a large payload stored outside graph/checkpoint state."""

    id: str = Field(default_factory=lambda: _new_id("art"))
    kind: ArtifactKind = ArtifactKind.OTHER
    uri: str = Field(..., min_length=1, description="Storage URI or absolute path")
    media_type: Optional[str] = None
    byte_size: Optional[int] = Field(default=None, ge=0)
    content_hash: Optional[str] = Field(
        default=None, description="sha256 hex of content when known"
    )
    audit_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("uri")
    @classmethod
    def uri_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("uri must not be blank")
        return v


class SourceLocation(BaseModel):
    """Code location without embedding the full source."""

    file_path: str = Field(..., min_length=1)
    start_line: Optional[int] = Field(default=None, ge=1)
    end_line: Optional[int] = Field(default=None, ge=1)
    column_start: Optional[int] = Field(default=None, ge=0)
    column_end: Optional[int] = Field(default=None, ge=0)
    function_name: Optional[str] = None
    class_name: Optional[str] = None
    code_hash: Optional[str] = Field(
        default=None, description="Hash of the referenced span"
    )

    @field_validator("file_path")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        v = v.strip().replace("\\", "/")
        if not v:
            raise ValueError("file_path must not be blank")
        # Absolute host paths and traversal are rejected at domain boundary.
        if v.startswith("/") or ".." in v.split("/") or (len(v) > 1 and v[1] == ":"):
            raise ValueError(f"unsafe or absolute file_path: {v!r}")
        return v

    @model_validator(mode="after")
    def line_range_valid(self) -> SourceLocation:
        if (
            self.start_line is not None
            and self.end_line is not None
            and self.end_line < self.start_line
        ):
            raise ValueError("end_line must be >= start_line")
        return self


class ModelUsage(BaseModel):
    """Token / cost accounting for a model call or aggregated run."""

    provider: Optional[str] = None
    model: Optional[str] = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    estimated_cost_usd: Optional[float] = Field(default=None, ge=0)
    latency_ms: Optional[int] = Field(default=None, ge=0)
    call_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def fill_total(self) -> ModelUsage:
        if self.total_tokens == 0 and (self.input_tokens or self.output_tokens):
            self.total_tokens = self.input_tokens + self.output_tokens
        return self

    def add(self, other: ModelUsage) -> ModelUsage:
        return ModelUsage(
            provider=self.provider or other.provider,
            model=self.model or other.model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            estimated_cost_usd=(
                (self.estimated_cost_usd or 0) + (other.estimated_cost_usd or 0)
                if self.estimated_cost_usd is not None or other.estimated_cost_usd is not None
                else None
            ),
            latency_ms=(
                (self.latency_ms or 0) + (other.latency_ms or 0)
                if self.latency_ms is not None or other.latency_ms is not None
                else None
            ),
            call_count=self.call_count + other.call_count,
        )


class NodeError(BaseModel):
    """Normalized node/tool failure for graph state."""

    id: str = Field(default_factory=lambda: _new_id("err"))
    code: NodeErrorCode = NodeErrorCode.UNKNOWN
    node: Optional[str] = None
    message: str = Field(..., min_length=1)
    retriable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)


class RunBudget(BaseModel):
    """Budgets for a single audit run. Values are hard caps unless noted."""

    max_tokens: int = Field(default=100_000, ge=0)
    max_model_calls: int = Field(default=200, ge=0)
    max_tool_calls: int = Field(default=500, ge=0)
    max_files: int = Field(default=0, ge=0, description="0 = unlimited")
    max_duration_seconds: int = Field(default=1800, ge=1)
    max_sandbox_seconds: int = Field(default=600, ge=0)
    max_parallel_analyzers: int = Field(default=5, ge=1)
    tokens_used: int = Field(default=0, ge=0)
    model_calls_used: int = Field(default=0, ge=0)
    tool_calls_used: int = Field(default=0, ge=0)
    sandbox_seconds_used: float = Field(default=0.0, ge=0)
    files_analyzed: int = Field(default=0, ge=0)

    def remaining_tokens(self) -> int:
        return max(0, self.max_tokens - self.tokens_used)

    def tokens_exhausted(self) -> bool:
        return self.tokens_used >= self.max_tokens

    def model_calls_exhausted(self) -> bool:
        return self.model_calls_used >= self.max_model_calls

    def tool_calls_exhausted(self) -> bool:
        return self.tool_calls_used >= self.max_tool_calls

    def files_exhausted(self) -> bool:
        return self.max_files > 0 and self.files_analyzed >= self.max_files

    def is_exhausted(self) -> bool:
        return (
            self.tokens_exhausted()
            or self.model_calls_exhausted()
            or self.tool_calls_exhausted()
            or self.files_exhausted()
        )

    def consume_tokens(self, n: int) -> RunBudget:
        if n < 0:
            raise ValueError("token consume must be non-negative")
        return self.model_copy(update={"tokens_used": self.tokens_used + n})

    def consume_model_call(self, tokens: int = 0) -> RunBudget:
        upd = {
            "model_calls_used": self.model_calls_used + 1,
            "tokens_used": self.tokens_used + max(0, tokens),
        }
        return self.model_copy(update=upd)

    def consume_tool_call(self) -> RunBudget:
        return self.model_copy(update={"tool_calls_used": self.tool_calls_used + 1})

    def consume_file(self) -> RunBudget:
        return self.model_copy(update={"files_analyzed": self.files_analyzed + 1})
