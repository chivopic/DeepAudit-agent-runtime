"""Unit tests for agent domain models (M1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent.domain import (
    ArtifactRef,
    ArtifactKind,
    AuditPlan,
    AuditReport,
    AuditRequest,
    AuditStatus,
    AuditTaskSpec,
    CandidateFinding,
    Evidence,
    FileArtifact,
    Finding,
    FindingStatus,
    FixProposal,
    ModelUsage,
    NodeError,
    NodeErrorCode,
    RepositoryManifest,
    RepositoryRef,
    RepositorySnapshot,
    RunBudget,
    Severity,
    SourceLocation,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerifiedFinding,
    finding_from_legacy_dict,
    finding_to_legacy_dict,
    fingerprint_components,
)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnums:
    def test_severity_values(self):
        assert Severity.CRITICAL.value == "critical"
        assert Severity("high") is Severity.HIGH

    def test_verification_default_not_run(self):
        assert VerificationStatus.NOT_RUN.value == "not_run"

    def test_audit_status_pipeline_stages(self):
        stages = {
            AuditStatus.PENDING,
            AuditStatus.VALIDATING,
            AuditStatus.INGESTING,
            AuditStatus.PLANNING,
            AuditStatus.ANALYZING,
            AuditStatus.AGGREGATING,
            AuditStatus.VERIFYING,
            AuditStatus.REPORTING,
            AuditStatus.COMPLETED,
        }
        assert len(stages) == 9
        assert AuditStatus.PARTIAL.value == "partial"


# ---------------------------------------------------------------------------
# SourceLocation / ArtifactRef
# ---------------------------------------------------------------------------


class TestSourceLocation:
    def test_valid_relative_path(self):
        loc = SourceLocation(file_path="src/app.py", start_line=10, end_line=20)
        assert loc.file_path == "src/app.py"
        assert loc.start_line == 10

    def test_backslash_normalized(self):
        loc = SourceLocation(file_path=r"src\pkg\mod.py")
        assert loc.file_path == "src/pkg/mod.py"

    def test_rejects_absolute_posix(self):
        with pytest.raises(ValidationError):
            SourceLocation(file_path="/etc/passwd")

    def test_rejects_windows_drive(self):
        with pytest.raises(ValidationError):
            SourceLocation(file_path="C:/Users/secret.py")

    def test_rejects_traversal(self):
        with pytest.raises(ValidationError):
            SourceLocation(file_path="src/../../etc/passwd")

    def test_rejects_inverted_line_range(self):
        with pytest.raises(ValidationError):
            SourceLocation(file_path="a.py", start_line=20, end_line=10)

    def test_blank_path_rejected(self):
        with pytest.raises(ValidationError):
            SourceLocation(file_path="   ")


class TestArtifactRef:
    def test_minimal(self):
        ref = ArtifactRef(uri="file:///tmp/snippet.txt")
        assert ref.id.startswith("art_")
        assert ref.kind is ArtifactKind.OTHER

    def test_blank_uri_rejected(self):
        with pytest.raises(ValidationError):
            ArtifactRef(uri="  ")


# ---------------------------------------------------------------------------
# RunBudget / ModelUsage
# ---------------------------------------------------------------------------


class TestRunBudget:
    def test_defaults(self):
        b = RunBudget()
        assert b.max_tokens == 100_000
        assert not b.is_exhausted()

    def test_consume_tokens_immutable(self):
        b = RunBudget(max_tokens=100)
        b2 = b.consume_tokens(40)
        assert b.tokens_used == 0
        assert b2.tokens_used == 40
        assert b2.remaining_tokens() == 60

    def test_consume_negative_rejected(self):
        b = RunBudget()
        with pytest.raises(ValueError):
            b.consume_tokens(-1)

    def test_exhausted_when_tokens_hit(self):
        b = RunBudget(max_tokens=10, tokens_used=10)
        assert b.tokens_exhausted()
        assert b.is_exhausted()

    def test_files_unlimited_when_zero(self):
        b = RunBudget(max_files=0, files_analyzed=999)
        assert not b.files_exhausted()

    def test_files_capped(self):
        b = RunBudget(max_files=2, files_analyzed=2)
        assert b.files_exhausted()
        assert b.is_exhausted()

    def test_consume_model_call(self):
        b = RunBudget().consume_model_call(tokens=50)
        assert b.model_calls_used == 1
        assert b.tokens_used == 50


class TestModelUsage:
    def test_auto_total(self):
        u = ModelUsage(input_tokens=10, output_tokens=5)
        assert u.total_tokens == 15

    def test_add(self):
        a = ModelUsage(input_tokens=1, output_tokens=2, call_count=1, estimated_cost_usd=0.01)
        b = ModelUsage(input_tokens=3, output_tokens=4, call_count=2, estimated_cost_usd=0.02)
        c = a.add(b)
        assert c.input_tokens == 4
        assert c.output_tokens == 6
        assert c.call_count == 3
        assert c.estimated_cost_usd == pytest.approx(0.03)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


class TestRepository:
    def test_git_requires_url(self):
        with pytest.raises(ValidationError):
            RepositoryRef(source_type="git")

    def test_git_ok(self):
        r = RepositoryRef(source_type="git", url="https://github.com/acme/repo.git")
        assert r.url.endswith("repo.git")

    def test_local_requires_path(self):
        with pytest.raises(ValidationError):
            RepositoryRef(source_type="local")

    def test_file_artifact_rejects_absolute(self):
        with pytest.raises(ValidationError):
            FileArtifact(path="/abs/file.py")

    def test_manifest_helpers(self):
        files = [
            FileArtifact(path="a.py", language="python", priority=2.0),
            FileArtifact(path="b.ts", language="typescript", priority=0.5),
        ]
        m = RepositoryManifest(snapshot_id="snap_1", files=files)
        assert m.paths() == ["a.py", "b.ts"]
        assert set(m.by_language().keys()) == {"python", "typescript"}
        assert len(m.high_priority(1.0)) == 1

    def test_snapshot(self):
        snap = RepositorySnapshot(
            repository=RepositoryRef(source_type="local", local_path="/workspace/repo"),
            commit_sha="abc123",
            file_count=3,
        )
        assert snap.commit_sha == "abc123"


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


class TestPlan:
    def test_task_path_safety(self):
        with pytest.raises(ValidationError):
            AuditTaskSpec(target_path="../secret.py")

    def test_invalid_analyzer(self):
        with pytest.raises(ValidationError):
            AuditTaskSpec(target_path="a.py", analyzer="quantum")

    def test_plan_sort(self):
        plan = AuditPlan(
            audit_id="aud_1",
            tasks=[
                AuditTaskSpec(target_path="low.py", priority=0.1),
                AuditTaskSpec(target_path="high.py", priority=9.0),
            ],
        )
        ordered = plan.sorted_by_priority()
        assert ordered[0].target_path == "high.py"
        assert plan.task_count() == 2


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class TestFindings:
    def test_candidate_minimal(self):
        c = CandidateFinding(title="SQLi", description="user input in query")
        assert c.severity is Severity.MEDIUM
        assert c.id.startswith("cand_")

    def test_finding_phase1_verification_default(self):
        f = Finding(title="XSS", description="reflected")
        assert f.verification_status is VerificationStatus.NOT_RUN
        assert f.status is FindingStatus.NEW

    def test_blank_title_rejected(self):
        with pytest.raises(ValidationError):
            Finding(title="  ", description="x")

    def test_confidence_bounds(self):
        with pytest.raises(ValidationError):
            Finding(title="t", description="d", confidence=1.5)

    def test_with_status_updates(self):
        f = Finding(title="t", description="d")
        f2 = f.with_status(FindingStatus.VERIFIED)
        assert f.status is FindingStatus.NEW
        assert f2.status is FindingStatus.VERIFIED

    def test_evidence_kind(self):
        with pytest.raises(ValidationError):
            Evidence(kind="video", summary="nope")

    def test_verified_finding_extends(self):
        vf = VerifiedFinding(
            title="t",
            description="d",
            verification_status=VerificationStatus.CONFIRMED,
            verification_notes="reproduced",
        )
        assert vf.verification_notes == "reproduced"


# ---------------------------------------------------------------------------
# Verification / Fix
# ---------------------------------------------------------------------------


class TestVerification:
    def test_request_defaults_no_exec(self):
        r = VerificationRequest(finding_id="fnd_1")
        assert r.allow_execution is False
        assert r.strategy == "static_recheck"

    def test_invalid_strategy(self):
        with pytest.raises(ValidationError):
            VerificationRequest(finding_id="f", strategy="nuke")

    def test_result(self):
        res = VerificationResult(
            request_id="vreq_1",
            finding_id="fnd_1",
            status=VerificationStatus.SKIPPED,
            summary="phase1 skip",
        )
        assert res.status is VerificationStatus.SKIPPED

    def test_fix_proposal(self):
        fp = FixProposal(
            finding_id="fnd_1",
            title="parameterize query",
            description="use prepared statements",
            steps=["bind params", "retest"],
        )
        assert len(fp.steps) == 2


# ---------------------------------------------------------------------------
# AuditRequest / Report
# ---------------------------------------------------------------------------


class TestAuditRequest:
    def test_valid_request(self):
        req = AuditRequest(
            repository=RepositoryRef(
                source_type="git", url="https://example.com/r.git"
            ),
            languages=["python"],
            include_paths=["src/"],
            exclude_paths=["vendor/"],
        )
        assert req.budget.max_tokens == 100_000
        assert req.enable_verification is False
        assert req.severity_threshold is Severity.INFO

    def test_unsafe_include_paths_rejected(self):
        with pytest.raises(ValidationError):
            AuditRequest(
                repository=RepositoryRef(source_type="local", local_path="."),
                include_paths=["../outside"],
            )

    def test_report_recount(self):
        findings = [
            Finding(title="a", description="a", severity=Severity.HIGH),
            Finding(title="b", description="b", severity=Severity.HIGH),
            Finding(title="c", description="c", severity=Severity.LOW),
        ]
        report = AuditReport(audit_id="aud_1", findings=findings).recount_severities()
        assert report.severity_counts["high"] == 2
        assert report.severity_counts["low"] == 1


# ---------------------------------------------------------------------------
# NodeError
# ---------------------------------------------------------------------------


class TestNodeError:
    def test_create(self):
        err = NodeError(
            code=NodeErrorCode.BUDGET,
            message="tokens exhausted",
            node="analyze_file",
            retriable=False,
        )
        assert err.id.startswith("err_")
        assert err.code is NodeErrorCode.BUDGET


# ---------------------------------------------------------------------------
# Mappers
# ---------------------------------------------------------------------------


class TestMappers:
    def test_fingerprint_stable(self):
        a = fingerprint_components(
            file_path="Src/App.py",
            start_line=10,
            title="  SQL  Injection ",
            rule_id="CWE-89",
        )
        b = fingerprint_components(
            file_path="src/app.py",
            start_line=10,
            title="SQL Injection",
            rule_id="cwe-89",
        )
        assert a == b
        assert len(a) == 32

    def test_from_legacy_dict(self):
        legacy = {
            "id": "legacy-1",
            "task_id": "task-9",
            "title": "Path Traversal",
            "description": "unsanitized path join",
            "severity": "High",
            "file_path": "api/files.py",
            "line_start": 42,
            "line_end": 48,
            "code_snippet": "open(user_path)",
            "confidence": 80,
            "rule_id": "PT-01",
            "cwe_id": "CWE-22",
            "verification_status": "not_verified",
        }
        f = finding_from_legacy_dict(legacy)
        assert f.id == "legacy-1"
        assert f.audit_id == "task-9"
        assert f.severity is Severity.HIGH
        assert f.location is not None
        assert f.location.file_path == "api/files.py"
        assert f.location.start_line == 42
        assert f.confidence == pytest.approx(0.8)
        assert f.verification_status is VerificationStatus.NOT_RUN
        assert f.evidence and f.evidence[0].snippet == "open(user_path)"
        assert f.fingerprint

    def test_roundtrip_legacy(self):
        f = Finding(
            title="Open Redirect",
            description="redirect to user URL",
            severity=Severity.MEDIUM,
            location=SourceLocation(file_path="web/views.py", start_line=5),
            evidence=[
                Evidence(kind="code", summary="redirect", snippet="redirect(url)")
            ],
            recommendation="allowlist hosts",
            tags=["web"],
        )
        d = finding_to_legacy_dict(f)
        assert d["file_path"] == "web/views.py"
        assert d["line_start"] == 5
        assert d["code_snippet"] == "redirect(url)"
        assert d["severity"] == "medium"
        f2 = finding_from_legacy_dict(d)
        assert f2.title == f.title
        assert f2.severity is Severity.MEDIUM
        assert f2.location and f2.location.file_path == "web/views.py"

    def test_from_legacy_unknown_severity_defaults(self):
        f = finding_from_legacy_dict(
            {"title": "x", "description": "y", "severity": "ultra"}
        )
        assert f.severity is Severity.MEDIUM

    def test_from_legacy_vulnerability_type_and_ai_confidence(self):
        f = finding_from_legacy_dict(
            {
                "title": "SQLi",
                "description": "query",
                "vulnerability_type": "sql_injection",
                "ai_confidence": 85,
                "is_verified": True,
            }
        )
        assert f.category == "sql_injection"
        assert f.confidence == pytest.approx(0.85)
        assert f.verification_status is VerificationStatus.CONFIRMED
        d = finding_to_legacy_dict(f)
        assert d["vulnerability_type"] == "sql_injection"
        assert d["ai_confidence"] == pytest.approx(0.85)
        assert d["is_verified"] is True
