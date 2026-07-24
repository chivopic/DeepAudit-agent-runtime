"""Domain enumerations for the audit agent runtime."""

from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class VerificationStatus(str, Enum):
    """Whether a finding has been sandbox/tool verified."""

    NOT_RUN = "not_run"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    SKIPPED = "skipped"
    ERROR = "error"


class FindingStatus(str, Enum):
    NEW = "new"
    ANALYZING = "analyzing"
    VERIFIED = "verified"
    FALSE_POSITIVE = "false_positive"
    NEEDS_REVIEW = "needs_review"
    FIXED = "fixed"
    WONT_FIX = "wont_fix"
    DUPLICATE = "duplicate"


class AuditStatus(str, Enum):
    PENDING = "pending"
    VALIDATING = "validating"
    INGESTING = "ingesting"
    PLANNING = "planning"
    ANALYZING = "analyzing"
    AGGREGATING = "aggregating"
    VERIFYING = "verifying"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    # Budget hit before full plan finished; report still emitted with partial findings
    PARTIAL = "partial"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    POLICY_DENIED = "policy_denied"
    # Phase-1 null sandbox / deliberate non-execution (never treat as confirmed)
    SKIPPED = "skipped"


class ArtifactKind(str, Enum):
    SOURCE_SNIPPET = "source_snippet"
    FULL_FILE = "full_file"
    LOG = "log"
    PATCH = "patch"
    REPORT = "report"
    MODEL_RAW = "model_raw"
    TOOL_OUTPUT = "tool_output"
    VERIFICATION = "verification"
    OTHER = "other"


class NodeErrorCode(str, Enum):
    VALIDATION = "validation"
    INGEST = "ingest"
    PLAN = "plan"
    ANALYZE = "analyze"
    AGGREGATE = "aggregate"
    DEDUPE = "dedupe"
    PRIORITIZE = "prioritize"
    VERIFY = "verify"
    REPORT = "report"
    BUDGET = "budget"
    LLM = "llm"
    TOOL = "tool"
    SANDBOX = "sandbox"
    PERSISTENCE = "persistence"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
