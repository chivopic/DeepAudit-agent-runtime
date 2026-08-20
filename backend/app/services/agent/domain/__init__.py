"""
Validated domain models for the DeepAudit agent runtime.

These Pydantic models are the single source of truth for:
- graph state payloads
- structured LLM outputs
- tool I/O (later milestones)
- API mapping (later milestones)

ORM models remain under ``app.models``; map at the application boundary.
"""

from .audit import AuditReport, AuditReportSection, AuditRequest
from .cli import (
    CliAuditEnvelope,
    CliAuditSummary,
    CliFindingView,
    CliScope,
    DoctorCheck,
    DoctorEnvelope,
)
from .common import ArtifactRef, ModelUsage, NodeError, RunBudget, SourceLocation
from .enums import (
    ArtifactKind,
    AuditStatus,
    EvidenceLevel,
    ExecutionStatus,
    FindingState,
    FindingStatus,
    NodeErrorCode,
    Severity,
    VerificationStatus,
)
from .finding import (
    CandidateFinding,
    Evidence,
    Finding,
    ReasoningArtifact,
    VerifiedFinding,
)
from .mappers import (
    finding_from_legacy_dict,
    finding_to_legacy_dict,
    fingerprint_components,
)
from .plan import AuditPlan, AuditTaskSpec
from .repository import (
    FileArtifact,
    RepositoryManifest,
    RepositoryRef,
    RepositorySnapshot,
)
from .verification import (
    FixProposal,
    VerificationRequest,
    VerificationResult,
)

__all__ = [
    # enums
    "AuditStatus",
    "EvidenceLevel",
    "FindingState",
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
    # CLI result contracts
    "CliAuditEnvelope",
    "CliAuditSummary",
    "CliFindingView",
    "CliScope",
    "DoctorCheck",
    "DoctorEnvelope",
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
    "ReasoningArtifact",
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
