"""Small, serialization-first domain contract for local audits."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


SEVERITY_RANK = {
    Severity.INFO: 1,
    Severity.LOW: 2,
    Severity.MEDIUM: 3,
    Severity.HIGH: 4,
    Severity.CRITICAL: 5,
}


class ScanStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass(frozen=True)
class Issue:
    code: str
    message: str
    path: str | None = None
    retriable: bool = False


@dataclass(frozen=True)
class Location:
    file_path: str
    start_line: int
    end_line: int
    column_start: int = 0
    column_end: int = 0
    code_hash: str = ""
    function_name: str | None = None
    class_name: str | None = None


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str
    summary: str
    location: Location
    snippet: str
    level: str = "E0"
    confidence: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict)
    artifact: dict[str, Any] | None = None


@dataclass(frozen=True)
class Finding:
    id: str
    display_id: str
    title: str
    description: str
    severity: Severity
    category: str
    location: Location
    evidence: tuple[Evidence, ...]
    confidence: float
    analyzer: str
    rule_id: str
    fingerprint: str
    cwe_id: str | None = None
    owasp: str | None = None
    evidence_level: str = "E0"
    finding_state: str = "candidate"
    verification_status: str = "not_run"


@dataclass(frozen=True)
class Manifest:
    files: tuple[str, ...]
    discovered_files: int
    issues: tuple[Issue, ...] = ()
    limited: bool = False
    incomplete: bool = False
    languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScanResult:
    status: ScanStatus
    version: str | None
    rule_set_hash: str
    requested_files: int
    scanned_files: int
    findings: tuple[Finding, ...] = ()
    scanned_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    issues: tuple[Issue, ...] = ()
    duration_seconds: float = 0.0
    max_memory_bytes: int | None = None


def primitive(value: Any) -> Any:
    """Convert domain objects to deterministic JSON-compatible primitives."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return primitive(asdict(value))
    if isinstance(value, dict):
        return {str(key): primitive(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [primitive(item) for item in value]
    return value
