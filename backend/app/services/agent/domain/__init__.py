"""
Validated domain models for the DeepAudit agent runtime.

These Pydantic models are the single source of truth for:
- graph state payloads
- structured LLM outputs
- tool I/O (later milestones)
- API mapping (later milestones)

ORM models remain under ``app.models``; map at the application boundary.
"""

from .enums import (
    AuditStatus,
    FindingStatus,
    Severity,
    VerificationStatus,
    ExecutionStatus,
    ArtifactKind,
    NodeErrorCode,
)
from .common import ArtifactRef, SourceLocation, ModelUsage, NodeError, RunBudget
from .repository import (
    RepositoryRef,
    RepositorySnapshot,
    FileArtifact,
    RepositoryManifest,
)
from .plan import AuditTaskSpec, AuditPlan
from .finding import (
    Evidence,
    CandidateFinding,
    Finding,
    VerifiedFinding,
)
from .verification import (
    VerificationRequest,
    VerificationResult,
    FixProposal,
)
from .audit import AuditRequest, AuditReport, AuditReportSection
from .mappers import (
    finding_from_legacy_dict,
    finding_to_legacy_dict,
    fingerprint_components,
)

__all__ = [
    # enums
    "AuditStatus",
    "FindingStatus",
    "Severity",
    "VerificationStatus",
    "ExecutionStatus",
    "ArtifactKind",
    "NodeErrorCode",
    # common
    "ArtifactRef",
    "SourceLocation",
    "ModelUsage",
    "NodeError",
    "RunBudget",
    # repository
    "RepositoryRef",
    "RepositorySnapshot",
    "FileArtifact",
    "RepositoryManifest",
    # plan
    "AuditTaskSpec",
    "AuditPlan",
    # findings
    "Evidence",
    "CandidateFinding",
    "Finding",
    "VerifiedFinding",
    # verification
    "VerificationRequest",
    "VerificationResult",
    "FixProposal",
    # audit
    "AuditRequest",
    "AuditReport",
    "AuditReportSection",
    # mappers
    "finding_from_legacy_dict",
    "finding_to_legacy_dict",
    "fingerprint_components",
]
