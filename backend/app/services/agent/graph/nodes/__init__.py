"""Phase 1 audit graph node implementations (skeleton with real domain I/O)."""

import hashlib
import json
import logging
import re
from contextvars import ContextVar
from pathlib import Path, PurePosixPath
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.services.agent.domain import (
    AuditPlan,
    AuditReport,
    AuditReportSection,
    AuditStatus,
    AuditTaskSpec,
    CandidateFinding,
    Evidence,
    FileArtifact,
    Finding,
    FindingStatus,
    ModelUsage,
    NodeError,
    NodeErrorCode,
    RepositoryManifest,
    RepositoryRef,
    RepositorySnapshot,
    RunBudget,
    Severity,
    SourceLocation,
    VerificationStatus,
)

from ..llm import LLMMessage
from ..runtime import GraphRuntime, get_runtime
from ..state import AuditState

logger = logging.getLogger(__name__)

# Extensions considered "source" for skeleton ingest
_SOURCE_EXTS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".c",
    ".cpp",
    ".h",
    ".cs",
    ".swift",
    ".kt",
    ".scala",
    ".vue",
    ".sol",
}

_SKIP_DIR_NAMES = {
    ".git",
    "node_modules",
    "vendor",
    "dist",
    "build",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
    ".idea",
    ".vscode",
}


def _event(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    payload = {"kind": kind, "message": message}
    payload.update(extra)
    return payload


def _sync_budget_manager(runtime: GraphRuntime, budget: RunBudget) -> None:
    """Keep harness BudgetManager in sync with graph RunBudget when present."""
    bm = runtime.budget_manager or runtime.extra.get("budget_manager")
    if bm is not None and hasattr(bm, "budget"):
        bm.budget = budget


# What analyze_file actually sends to the model. Longer files are a coverage gap
# until the context manager chunks them (later migration phase).
_MODEL_PREVIEW_CHARS = 4000


def _coverage_state(state: AuditState) -> dict[str, Any]:
    """Copy the running coverage record so a node can append without aliasing."""
    meta = state.get("meta") or {}
    raw = meta.get("analysis_coverage") if isinstance(meta, dict) else None
    cov: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    cov["succeeded"] = int(cov.get("succeeded") or 0)
    cov["tool_errors"] = int(cov.get("tool_errors") or 0)
    cov["planner_error"] = cov.get("planner_error")
    for key in ("failed_units", "degraded_units", "skipped_units", "truncated_units"):
        cov[key] = list(cov.get(key) or [])
    for key in ("source_windows", "rejected_findings"):
        if key in cov:
            cov[key] = list(cov.get(key) or [])
    return cov


def _unit(task_id: str, path: str | None, reason: str) -> dict[str, Any]:
    return {"task_id": task_id, "path": path, "reason": reason}


def _units_for_ids(
    plan: AuditPlan | None, task_ids: list[str], reason: str
) -> list[dict[str, Any]]:
    by_id = {task.id: task for task in plan.tasks} if plan is not None else {}
    units: list[dict[str, Any]] = []
    for task_id in task_ids:
        task = by_id.get(task_id)
        units.append(_unit(task_id, task.target_path if task is not None else None, reason))
    return units


def _structured_findings(text: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Parse a model payload.

    Returns ``([], None)`` when the payload is valid but carries no finding list
    (the offline ack ``{"ok": true}``). Returns ``(None, reason)`` when the
    payload was supposed to be JSON and is not usable.
    """
    raw = (text or "").strip()
    if not raw:
        return [], None
    # Real gateways commonly return fenced JSON. Accept the envelope without
    # repairing malformed JSON into an apparently successful empty analysis.
    if not (raw.startswith("{") or raw.startswith("[")):
        fenced = re.search(r"```(?:json)?\s*([\[{][\s\S]*?)\s*```", raw, re.IGNORECASE)
        if fenced is None:
            return None, "model output is not JSON"
        raw = fenced.group(1).strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if isinstance(parsed, dict):
        for key in ("findings", "results", "issues", "vulnerabilities"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
        if isinstance(parsed, dict):
            return [], None
    if not isinstance(parsed, list):
        return None, "structured findings are not a list"
    rows: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            return None, "structured finding is not an object"
        if not _is_defence_note(item):
            rows.append(item)
    return rows, None


def _candidates_from_model_rows(
    task: AuditTaskSpec, rows: list[dict[str, Any]]
) -> list[CandidateFinding]:
    found: list[CandidateFinding] = []
    for item in rows:
        try:
            line = int(item.get("line") or 1)
        except (TypeError, ValueError):
            line = 1
        loc = SourceLocation(
            file_path=task.target_path,
            start_line=line,
            end_line=line,
        )
        sev_raw = str(item.get("severity") or "medium").lower()
        try:
            sev = Severity(sev_raw)
        except ValueError:
            sev = Severity.MEDIUM
        found.append(
            CandidateFinding(
                title=str(item.get("title") or "LLM finding"),
                description=str(item.get("description") or item.get("title") or ""),
                severity=sev,
                cwe_id=_normalize_cwe(item.get("cwe") or item.get("cwe_id")),
                location=loc,
                evidence=[
                    Evidence(
                        kind="model",
                        summary="llm candidate",
                        location=loc,
                        confidence=0.55,
                    )
                ],
                confidence=0.55,
                analyzer="llm",
                source_task_id=task.id,
            )
        )
    return found


def _node_span(runtime: GraphRuntime, node_name: str, **attrs: Any):
    """Return a context manager for node spans (no-op if no tracer)."""
    from contextlib import nullcontext

    tracer = runtime.get_tracer() if hasattr(runtime, "get_tracer") else None
    if tracer is None:
        return nullcontext()
    try:
        from app.services.agent.observability import SPAN_GRAPH_NODE

        return tracer.span(
            f"{SPAN_GRAPH_NODE}.{node_name}",
            **{"node.name": node_name, **attrs},
        )
    except Exception:  # noqa: BLE001
        return nullcontext()


def _normalize_cwe(value: Any) -> str | None:
    """Normalize a model-supplied CWE to canonical ``CWE-<digits>`` form.

    Models write "CWE-89", "cwe 89" or bare "89"; anything without a number is
    dropped rather than guessed, since a wrong class would merge unrelated
    findings during dedupe.
    """
    if value is None:
        return None
    import re

    m = re.search(r"(\d+)", str(value))
    return f"CWE-{m.group(1)}" if m else None


# Phrases in which a model is describing a defence, not a defect. Kept
# phrase-level rather than word-level: bare "safe" or "mitigated" appear in
# plenty of genuine findings.
_DEFENCE_PHRASES = (
    "mitigated by",
    "mitigated via",
    "mitigated through",
    "is mitigated",
    "already mitigated",
    "properly validated",
    "properly escaped",
    "properly sanitized",
    "properly sanitised",
    "correctly validated",
    "correctly escaped",
    "not a vulnerability",
    "not exploitable",
    "no vulnerability",
    "no security issue",
    "appears safe",
    "is safe because",
    "已缓解",
    "不是漏洞",
    "不可利用",
    "不存在漏洞",
)

# If any of these also appear, the model is saying the defence does not hold —
# which is a real finding and must survive.
_STILL_VULNERABLE_PHRASES = (
    "not mitigated",
    "insufficient",
    "insufficiently",
    "incomplete",
    "partial",
    "partially",
    "bypass",
    "can be circumvented",
    "still vulnerable",
    "however",
    "but it",
    "but the",
    "可绕过",
    "不充分",
    "仍然",
)


def _is_defence_note(item: dict[str, Any]) -> bool:
    """Is this the model describing a defence rather than reporting a defect?

    Models asked for "findings" will happily narrate what the code does right
    — "SSRF mitigated by strict host allowlist" — and every one of those is a
    false positive in a security report. Worse than noise: a report full of
    non-issues teaches people to skim it.

    Conservative on purpose. Anything hinting the defence is incomplete is
    kept, because dropping a real finding costs far more than keeping a
    tidy-looking one.
    """
    text = " ".join(str(item.get(k) or "") for k in ("title", "description")).lower()
    if not text.strip():
        return False
    if any(p in text for p in _STILL_VULNERABLE_PHRASES):
        return False
    return any(p in text for p in _DEFENCE_PHRASES)


def _parse_llm_findings(content: str | None) -> list[dict[str, Any]]:
    """Extract a findings list from a model reply.

    Real models fence their JSON or prefix it with prose, so delegate to the
    shared tolerant parser (markdown stripping + json-repair) instead of
    requiring a bare leading "[". Accepts either a top-level array or an
    object wrapping one under a conventional key.
    """
    text = (content or "").strip()
    if not text:
        return []

    from app.services.agent.json_parser import AgentJsonParser

    parsed = AgentJsonParser.parse_any(text, default=None)
    if isinstance(parsed, dict):
        for key in ("findings", "results", "issues", "vulnerabilities"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        return []
    items = [item for item in parsed if isinstance(item, dict)]

    kept = [item for item in items if not _is_defence_note(item)]
    if len(kept) != len(items):
        logger.debug(
            "dropped %d finding(s) that described a defence, not a defect",
            len(items) - len(kept),
        )
    return kept


def _lang_for(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".jsx": "javascript",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".cs": "csharp",
        ".sol": "solidity",
    }.get(ext)


def _resolve_workspace(state: AuditState, runtime: GraphRuntime) -> Path | None:
    if runtime.workspace_root:
        return Path(runtime.workspace_root)
    req = state.get("request")
    if not req:
        return None
    repo: RepositoryRef = req.repository
    if repo.local_path:
        # Synthetic locators ("fixture://", "project://") name a source, not a
        # directory — they are resolved during ingest, never opened as a path.
        if "://" in repo.local_path:
            return None
        return Path(repo.local_path)
    if runtime.extra.get("fixture_files"):
        # Synthetic workspace: files provided as {rel_path: content}
        return None
    return None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def validate_request(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Validate AuditRequest and normalize audit_id / status."""
    runtime = get_runtime(config)
    req = state.get("request")
    errors: list[NodeError] = []
    if req is None:
        errors.append(
            NodeError(
                code=NodeErrorCode.VALIDATION,
                node="validate_request",
                message="missing request in state",
            )
        )
        return {
            "status": AuditStatus.FAILED,
            "errors": errors,
            "events": [_event("node.failed", "validate_request: missing request")],
        }

    audit_id = state.get("audit_id") or req.id
    # Budget sanity
    budget = req.budget
    if budget.max_tokens <= 0 and budget.max_model_calls <= 0:
        errors.append(
            NodeError(
                code=NodeErrorCode.VALIDATION,
                node="validate_request",
                message="budget max_tokens and max_model_calls both non-positive",
            )
        )

    if errors:
        return {
            "audit_id": audit_id,
            "status": AuditStatus.FAILED,
            "errors": errors,
            "events": [_event("node.failed", "validate_request failed")],
        }

    return {
        "audit_id": audit_id,
        "thread_id": state.get("thread_id") or audit_id,
        "status": AuditStatus.VALIDATING,
        "budget": budget.model_copy(deep=True),
        "events": [
            _event(
                "node.completed",
                "validate_request ok",
                audit_id=audit_id,
                offline=runtime.offline,
            )
        ],
        "meta": {"validated": True},
    }


async def ingest_repository(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Snapshot repository metadata (local path or fixture map)."""
    runtime = get_runtime(config)
    req = state["request"]
    root = _resolve_workspace(state, runtime)
    fixture_files: dict[str, str] = dict(runtime.extra.get("fixture_files") or {})

    # Materialising a real repository (clone / unzip) can take minutes, so the
    # API hands us a resolver instead of doing it during the HTTP request.
    # Ingest is the right phase for it, and later nodes read the root back off
    # the runtime.
    if root is None and "fixture_files" not in runtime.extra:
        resolver = runtime.extra.get("workspace_resolver")
        if resolver is not None:
            resolved = await resolver()
            if resolved:
                runtime.workspace_root = Path(resolved)
                root = Path(resolved)

    file_count = 0
    total_bytes = 0
    languages: set[str] = set()
    snapshot_path: str | None = None

    if "fixture_files" in runtime.extra:
        for p, content in fixture_files.items():
            file_count += 1
            total_bytes += len(content.encode("utf-8"))
            lang = _lang_for(p)
            if lang:
                languages.add(lang)
        snapshot_path = "fixture://" + (state.get("audit_id") or "aud")
    elif root and root.exists() and root.is_dir():
        root = root.resolve(strict=True)
        snapshot_path = str(root)
        for path in root.rglob("*"):
            try:
                rel = path.relative_to(root)
            except ValueError:
                continue
            if any(part in _SKIP_DIR_NAMES for part in rel.parts):
                continue
            if path.suffix.lower() not in _SOURCE_EXTS:
                continue
            current = root
            if any((current := current / part).is_symlink() for part in rel.parts):
                continue
            if not path.is_file():
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
                size = resolved.stat().st_size
            except (OSError, ValueError):
                continue
            file_count += 1
            total_bytes += size
            lang = _lang_for(path.name)
            if lang:
                languages.add(lang)
    else:
        # Still produce a snapshot for remote git without checkout (M2 skeleton)
        snapshot_path = req.repository.url or req.repository.local_path or "unknown"

    snap = RepositorySnapshot(
        repository=req.repository,
        commit_sha=req.repository.commit,
        snapshot_path=snapshot_path,
        file_count=file_count,
        total_bytes=total_bytes,
        languages=sorted(languages),
        metadata={"ingest": "skeleton", "fixture": bool(fixture_files)},
    )
    return {
        "status": AuditStatus.INGESTING,
        "repository": snap,
        "events": [
            _event(
                "node.completed",
                "ingest_repository",
                file_count=file_count,
                languages=sorted(languages),
            )
        ],
        "meta": {"ingested": True},
    }


async def build_manifest(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Build file inventory from workspace or fixtures."""
    runtime = get_runtime(config)
    req = state["request"]
    snap = state.get("repository")
    if snap is None:
        return {
            "status": AuditStatus.FAILED,
            "errors": [
                NodeError(
                    code=NodeErrorCode.INGEST,
                    node="build_manifest",
                    message="repository snapshot missing",
                )
            ],
            "events": [_event("node.failed", "build_manifest: no snapshot")],
        }

    include = set(req.include_paths or [])
    exclude = set(req.exclude_paths or [])
    requested_languages = {language.lower() for language in req.languages}
    files: list[FileArtifact] = []
    excluded: list[str] = []
    manifest_issues: list[dict[str, str]] = []
    fixture_files: dict[str, str] = dict(runtime.extra.get("fixture_files") or {})
    max_target_bytes = max(1, int(runtime.extra.get("scanner_max_target_bytes", 1 * 1024 * 1024)))

    def _allowed(rel: str) -> bool:
        if any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in exclude):
            return False
        if not include:
            return True
        return any(rel == i or rel.startswith(i.rstrip("/") + "/") for i in include)

    def _source_allowed(rel: str) -> bool:
        language = _lang_for(rel)
        supported = (
            bool(req.config.get("source_snapshot")) or Path(rel).suffix.lower() in _SOURCE_EXTS
        )
        return supported and (not requested_languages or language in requested_languages)

    if "fixture_files" in runtime.extra:
        for rel, content in sorted(fixture_files.items()):
            rel_n = rel.replace("\\", "/")
            if not _allowed(rel_n) or not _source_allowed(rel_n):
                excluded.append(rel_n)
                continue
            raw = content.encode("utf-8")
            if len(raw) > max_target_bytes:
                excluded.append(rel_n)
                manifest_issues.append(
                    {
                        "code": "target_too_large",
                        "path": rel_n,
                        "message": f"source file exceeds {max_target_bytes} bytes",
                    }
                )
                continue
            if b"\0" in raw[:8192]:
                excluded.append(rel_n)
                manifest_issues.append(
                    {
                        "code": "binary_file",
                        "path": rel_n,
                        "message": "binary source file was not scanned",
                    }
                )
                continue
            files.append(
                FileArtifact(
                    path=rel_n,
                    language=_lang_for(rel_n),
                    byte_size=len(raw),
                    line_count=content.count("\n") + 1,
                    content_hash=hashlib.sha256(raw).hexdigest()[:16],
                    priority=1.0 if "auth" in rel_n.lower() or "sql" in rel_n.lower() else 0.5,
                )
            )
    else:
        root = _resolve_workspace(state, runtime)
        if root and root.exists() and root.is_dir():
            root = root.resolve(strict=True)
            for path in sorted(root.rglob("*")):
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                if any(part in _SKIP_DIR_NAMES for part in Path(rel).parts):
                    continue
                if not _allowed(rel) or not _source_allowed(rel):
                    excluded.append(rel)
                    continue
                current = root
                if any((current := current / part).is_symlink() for part in Path(rel).parts):
                    excluded.append(rel)
                    manifest_issues.append(
                        {
                            "code": "symlink",
                            "path": rel,
                            "message": "symbolic-link source file was not scanned",
                        }
                    )
                    continue
                if not path.is_file():
                    continue
                try:
                    resolved = path.resolve(strict=True)
                    resolved.relative_to(root)
                except (OSError, ValueError):
                    excluded.append(rel)
                    manifest_issues.append(
                        {
                            "code": "unsafe_path",
                            "path": rel,
                            "message": "source file could not be resolved inside workspace",
                        }
                    )
                    continue
                try:
                    size = resolved.stat().st_size
                except OSError:
                    excluded.append(rel)
                    manifest_issues.append(
                        {
                            "code": "unreadable",
                            "path": rel,
                            "message": "source file metadata could not be read",
                        }
                    )
                    continue
                if size > max_target_bytes:
                    excluded.append(rel)
                    manifest_issues.append(
                        {
                            "code": "target_too_large",
                            "path": rel,
                            "message": f"source file exceeds {max_target_bytes} bytes",
                        }
                    )
                    continue
                try:
                    data = resolved.read_bytes()
                except OSError:
                    excluded.append(rel)
                    manifest_issues.append(
                        {
                            "code": "unreadable",
                            "path": rel,
                            "message": "source file could not be read",
                        }
                    )
                    continue
                if b"\0" in data[:8192]:
                    excluded.append(rel)
                    manifest_issues.append(
                        {
                            "code": "binary_file",
                            "path": rel,
                            "message": "binary source file was not scanned",
                        }
                    )
                    continue
                files.append(
                    FileArtifact(
                        path=rel,
                        language=_lang_for(rel),
                        byte_size=len(data),
                        line_count=data.count(b"\n") + 1,
                        content_hash=hashlib.sha256(data).hexdigest()[:16],
                        priority=0.5,
                    )
                )

    # Cap by budget.max_files if set
    budget: RunBudget = state.get("budget") or req.budget
    discovered = len(files)
    limited = budget.max_files > 0 and discovered > budget.max_files
    omitted_paths = [file.path for file in files[budget.max_files :]] if limited else []
    if limited:
        excluded.extend(omitted_paths)
        files = files[: budget.max_files]

    manifest = RepositoryManifest(
        snapshot_id=snap.id,
        files=files,
        excluded_paths=excluded,
        stats={
            "selected": len(files),
            "discovered": discovered,
            "excluded": len(excluded),
            "limited": limited,
            "omitted_paths": omitted_paths,
            "incomplete": bool(manifest_issues),
            "issues": manifest_issues,
        },
    )
    return {
        "manifest": manifest,
        "events": [
            _event(
                "node.completed",
                "build_manifest",
                selected=len(files),
                excluded=len(excluded),
            )
        ],
        "meta": {
            "manifest_built": True,
            "manifest_limited": limited,
            "manifest_incomplete": bool(manifest_issues),
            "manifest_issues": manifest_issues,
        },
    }


async def static_scan(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Run the injected scanner against the exact manifest and expose coverage."""
    runtime = get_runtime(config)
    scanner = runtime.scanner
    if scanner is None:
        return {
            "events": [_event("node.completed", "static_scan not configured")],
            "meta": {"scanner_configured": False},
        }

    manifest = state.get("manifest")
    root = _resolve_workspace(state, runtime)
    if manifest is None or root is None:
        message = "scanner requires a manifest and local workspace"
        return {
            "errors": [
                NodeError(
                    code=NodeErrorCode.TOOL,
                    node="static_scan",
                    message=message,
                )
            ],
            "events": [_event("node.failed", message)],
            "meta": {
                "scanner_configured": True,
                "scanner_status": "error",
                "scanner_coverage": {
                    "status": "error",
                    "requested_files": len(manifest.files) if manifest else 0,
                    "scanned_files": 0,
                },
            },
        }

    try:
        from app.services.agent.tooling.scanners import ScannerRequest

        result = await scanner.scan(
            ScannerRequest(
                workspace_root=Path(root).resolve(strict=True),
                relative_files=tuple(manifest.paths()),
                max_results=int(runtime.extra.get("scanner_max_results", 2_000)),
                max_target_bytes=int(
                    runtime.extra.get("scanner_max_target_bytes", 1 * 1024 * 1024)
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 — normalized into explicit scanner failure
        message = f"scanner failed: {type(exc).__name__}: {exc}"
        return {
            "errors": [
                NodeError(
                    code=NodeErrorCode.TOOL,
                    node="static_scan",
                    message=message,
                )
            ],
            "events": [_event("node.failed", message)],
            "meta": {
                "scanner_configured": True,
                "scanner_status": "error",
                "scanner_coverage": {
                    "status": "error",
                    "requested_files": len(manifest.files),
                    "scanned_files": 0,
                },
            },
        }

    status = result.status.value
    issue_dicts = [issue.to_dict() for issue in result.issues]
    event_kind = "node.completed" if status == "complete" else "node.partial"
    if status == "error":
        event_kind = "node.failed"
    errors = []
    if status == "error":
        errors.append(
            NodeError(
                code=NodeErrorCode.TOOL,
                node="static_scan",
                message="static scanner returned an error result",
                details={"issues": issue_dicts},
            )
        )
    budget = state.get("budget")
    budget_update = budget
    if (
        budget is not None
        and not runtime.enable_model_calls
        and not runtime.enable_heuristic_analysis
    ):
        budget_update = budget.model_copy(
            update={"files_analyzed": budget.files_analyzed + result.scanned_files}
        )
        _sync_budget_manager(runtime, budget_update)
    return {
        "candidate_findings": list(result.candidates),
        "budget": budget_update,
        "errors": errors,
        "events": [
            _event(
                event_kind,
                f"static_scan {status}",
                scanner=result.scanner,
                requested_files=result.requested_files,
                scanned_files=result.scanned_files,
                candidates=len(result.candidates),
            )
        ],
        "meta": {
            "scanner_configured": True,
            "scanner_status": status,
            "scanner_coverage": result.coverage_dict(),
            "scanner_issues": issue_dicts,
            "scanner_metadata": dict(result.metadata),
        },
    }


async def plan_audit(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Create an AuditPlan — FakeLLM optional structured assist, then deterministic plan."""
    runtime = get_runtime(config)
    req = state["request"]
    manifest = state.get("manifest")
    scanner_only = not runtime.enable_model_calls and not runtime.enable_heuristic_analysis
    if manifest is None or not manifest.files or scanner_only:
        # Empty plan is valid (no files)
        plan = AuditPlan(
            audit_id=state["audit_id"],
            tasks=[],
            strategy="file_parallel",
            rationale=(
                "static scanner is the complete analysis plan"
                if scanner_only and manifest and manifest.files
                else "no files in manifest"
            ),
        )
        return {
            "status": AuditStatus.PLANNING,
            "plan": plan,
            "pending_task_ids": [],
            "events": [_event("node.completed", "plan_audit empty")],
        }

    usage = state.get("usage") or ModelUsage()
    budget: RunBudget = state.get("budget") or req.budget.model_copy(deep=True)
    rationale = "priority by path heuristics"
    plan_errors: list[NodeError] = []
    coverage = _coverage_state(state)
    with _node_span(runtime, "plan_audit", **{"audit.id": state.get("audit_id")}):
        # Optional LLM call (FakeLLM in tests) — counts against budget.
        # A planner failure still falls through to the deterministic task list.
        if runtime.enable_model_calls:
            try:
                resp = await runtime.llm.complete(
                    [
                        LLMMessage(role="system", content="You plan security audits. Reply JSON."),
                        LLMMessage(
                            role="user",
                            content=json.dumps(
                                {
                                    "files": [f.path for f in manifest.files[:50]],
                                    "languages": req.languages,
                                }
                            ),
                        ),
                    ]
                )
                usage = usage.add(resp.usage).add(ModelUsage(attempt_count=1, success_count=1))
                tokens = resp.usage.total_tokens or 0
                budget = budget.consume_model_call(tokens=tokens)
                if resp.content:
                    rationale = f"llm:{resp.content[:200]}"
            except Exception as exc:  # noqa: BLE001 — planner must not fail hard
                logger.warning("plan_audit llm failed: %s", exc)
                usage = usage.add(
                    ModelUsage(
                        attempt_count=1,
                        failure_count=1,
                        unknown_token_calls=1,
                    )
                )
                budget = budget.consume_model_call(tokens=0)
                plan_errors.append(
                    NodeError(
                        code=NodeErrorCode.PLAN,
                        node="plan_audit",
                        message=f"planner model failed: {type(exc).__name__}",
                        retriable=True,
                        details={"error_type": type(exc).__name__},
                    )
                )
                coverage["planner_error"] = f"{type(exc).__name__}: {exc}"[:300]

        tasks: list[AuditTaskSpec] = []
        for f in manifest.files:
            tasks.append(
                AuditTaskSpec(
                    target_path=f.path,
                    language=f.language,
                    analyzer=(
                        "llm"
                        if runtime.enable_model_calls and runtime.offline
                        else "hybrid" if runtime.enable_model_calls else "static"
                    ),
                    priority=f.priority,
                    max_tokens=min(4000, budget.remaining_tokens() or 4000),
                    metadata={"role": "analysis"},
                )
            )
        tasks.sort(key=lambda t: t.priority, reverse=True)
        plan = AuditPlan(
            audit_id=state["audit_id"],
            tasks=tasks,
            # Multiple files in a graph step are still awaited serially.
            strategy="sequential",
            max_parallel=1,
            rationale=rationale,
        )
        # Single source of truth: graph budget → harness BudgetManager (no double-count).
        _sync_budget_manager(runtime, budget)

    return {
        "status": AuditStatus.PLANNING,
        "plan": plan,
        "pending_task_ids": [t.id for t in tasks],
        "usage": usage,
        "budget": budget,
        "errors": plan_errors,
        "events": [_event("node.completed", "plan_audit", task_count=len(tasks))],
        "meta": {"planned": True, "analysis_coverage": coverage},
    }


def _line_builds_a_string(line: str) -> bool:
    """Does this line interpolate or concatenate into the literal?

    A bare ``SELECT * FROM`` is just SQL; it is the building of it from parts
    that makes it injectable. Without this the pattern fires on every
    parameterised query, which is where most of the scanner's false positives
    came from.
    """
    stripped = line.strip()
    if '" +' in line or "' +" in line or '+ "' in line or "+ '" in line:
        return True
    if '" %' in line or "' %" in line or "%s" in line:
        return True
    if ".format(" in line:
        return True
    # f-string on the same line as the literal
    return bool(re.search(r"""f["']""", stripped)) and "{" in stripped


# Cross-file context budget. Kept tight on purpose: the point is to let the
# model judge a guard defined elsewhere, not to paste the repository into every
# prompt. Blowing the budget would cost more than the capability is worth.
MAX_CONTEXT_MODULES = 3
MAX_CONTEXT_CHARS = 1500

_PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(?P<from>\.{0,2}[\w.]+)\s+import|import\s+(?P<plain>[\w.]+))",
    re.MULTILINE,
)
_JS_IMPORT_RE = re.compile(
    r"""(?:from\s+|require\(\s*)["'](?P<mod>\.{1,2}/[^"']+)["']""",
)


def _local_imports(path: str, content: str) -> list[str]:
    """Candidate workspace-relative paths this file imports.

    Only local modules: a guard living in ``validators.py`` is worth reading,
    ``os`` and ``requests`` are not. Returns plain candidates — whether any of
    them exists is decided by the caller, which holds the file map.
    """
    here = PurePosixPath(path.replace("\\", "/"))
    pkg = here.parent
    out: list[str] = []

    if here.suffix in {".py", ".pyi"}:
        for m in _PY_IMPORT_RE.finditer(content):
            name = m.group("from") or m.group("plain") or ""
            if not name:
                continue
            if name.startswith("."):
                # Relative: one leading dot means this package, two means up.
                dots = len(name) - len(name.lstrip("."))
                base = pkg
                for _ in range(dots - 1):
                    base = base.parent
                tail = name.lstrip(".").replace(".", "/")
                out.append(str(base / tail) + ".py" if tail else "")
            else:
                # Absolute, but only interesting when it resolves in-tree.
                out.append(name.replace(".", "/") + ".py")
    elif here.suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
        for m in _JS_IMPORT_RE.finditer(content):
            rel = m.group("mod")
            target = pkg
            parts = [p for p in rel.split("/") if p not in ("",)]
            for part in parts:
                if part == ".":
                    continue
                if part == "..":
                    target = target.parent
                else:
                    target = target / part
            stem = str(target)
            if PurePosixPath(stem).suffix:
                out.append(stem)
            else:
                out.extend(f"{stem}{ext}" for ext in (here.suffix, ".js", ".ts"))

    seen: set[str] = set()
    unique: list[str] = []
    for c in out:
        if c and c != path and c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _load_import_context(
    path: str,
    content: str,
    *,
    fixture_files: dict[str, str],
    root: Path | None,
) -> list[tuple[str, str]]:
    """Source of the local modules ``path`` imports, within budget.

    Reads through the same jail as the file under analysis — a crafted import
    string must not become a path traversal.
    """
    loaded: list[tuple[str, str]] = []
    for candidate in _local_imports(path, content):
        if len(loaded) >= MAX_CONTEXT_MODULES:
            break
        text = ""
        if candidate in fixture_files:
            text = fixture_files[candidate]
        elif root is not None:
            text = _safe_read_under_root(root, candidate)
        if text:
            loaded.append((candidate, text[:MAX_CONTEXT_CHARS]))
    return loaded


def _heuristic_candidates(task: AuditTaskSpec, content: str) -> list[CandidateFinding]:
    """Deterministic pattern hits so Fake-LLM-less runs still produce findings."""
    patterns = [
        ("eval(", "Code Injection", Severity.HIGH, "CWE-95"),
        ("exec(", "Code Injection", Severity.HIGH, "CWE-95"),
        ("pickle.loads", "Insecure Deserialization", Severity.HIGH, "CWE-502"),
        ("subprocess.call", "Command Execution", Severity.MEDIUM, "CWE-78"),
        ("os.system", "OS Command Injection", Severity.HIGH, "CWE-78"),
        ("innerHTML", "DOM XSS sink", Severity.MEDIUM, "CWE-79"),
        ("dangerouslySetInnerHTML", "React XSS sink", Severity.MEDIUM, "CWE-79"),
        ("md5(", "Weak Hash", Severity.LOW, "CWE-328"),
        ("verify=False", "TLS Verification Disabled", Severity.MEDIUM, "CWE-295"),
        # Only injectable when the statement is built from parts — see
        # _line_builds_a_string.
        ("SELECT * FROM", "Possible SQL string", Severity.MEDIUM, "CWE-89"),
        ("password =", "Hardcoded secret pattern", Severity.MEDIUM, "CWE-798"),
    ]
    out: list[CandidateFinding] = []
    lines = content.splitlines()
    for i, line in enumerate(lines, start=1):
        for needle, title, sev, cwe in patterns:
            if needle in line:
                if needle == "SELECT * FROM" and not _line_builds_a_string(line):
                    continue
                loc = SourceLocation(
                    file_path=task.target_path,
                    start_line=i,
                    end_line=i,
                )
                out.append(
                    CandidateFinding(
                        title=title,
                        description=f"Pattern `{needle}` at {task.target_path}:{i}",
                        severity=sev,
                        cwe_id=cwe,
                        location=loc,
                        evidence=[
                            Evidence(
                                kind="code",
                                summary=f"matched {needle}",
                                location=loc,
                                snippet=line.strip()[:500],
                                confidence=0.6,
                            )
                        ],
                        confidence=0.6,
                        analyzer="heuristic",
                        rule_id=f"heur:{needle}",
                        source_task_id=task.id,
                    )
                )
    return out


def _safe_read_under_root(root: Path, rel: str) -> str:
    """Read file only if resolved path stays under workspace root (jail)."""
    rel_n = rel.replace("\\", "/").lstrip("/")
    if not rel_n or ".." in rel_n.split("/") or (len(rel_n) > 1 and rel_n[1] == ":"):
        return ""
    try:
        root_r = root.resolve()
        fp = (root_r / rel_n).resolve()
        if hasattr(fp, "is_relative_to"):
            if not fp.is_relative_to(root_r):
                return ""
        else:
            # py<3.9 fallback
            if root_r not in fp.parents and fp != root_r:
                return ""
        if fp.is_file():
            return fp.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    return ""


async def finalize_cancelled(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Terminal node when cooperative cancel is observed."""
    return {
        "status": AuditStatus.CANCELLED,
        "cancelled": True,
        "pending_task_ids": [],
        "events": [
            _event(
                "task.cancelled",
                "audit cancelled (cooperative)",
                audit_id=state.get("audit_id"),
            )
        ],
    }


_in_analyze_batch: ContextVar[bool] = ContextVar("deepaudit_analyze_batch", default=False)


async def _analyze_batch(
    state: AuditState,
    config: RunnableConfig | None,
    pending: list[str],
    width: int,
) -> dict:
    """Analyze up to ``width`` files serially inside one graph step."""
    budget = state.get("budget")
    remaining_calls = 10**6
    if budget is not None and budget.max_model_calls > 0:
        remaining_calls = max(0, budget.max_model_calls - budget.model_calls_used)
    slots = max(1, min(width, len(pending), remaining_calls or 1))
    batch = pending[:slots]
    rest = pending[slots:]
    token = _in_analyze_batch.set(True)
    merged_candidates: list[Any] = []
    merged_errors: list[Any] = []
    merged_events: list[dict[str, Any]] = []
    coverage: dict[str, Any] | None = None
    usage = state.get("usage")
    try:
        current = dict(state)
        for index, task_id in enumerate(batch):
            current["pending_task_ids"] = [task_id]
            if usage is not None:
                current["usage"] = usage
            delta = await analyze_file(current, config)
            budget = delta.get("budget", budget)
            usage = delta.get("usage", usage)
            current["budget"] = budget
            current["meta"] = delta.get("meta") or current.get("meta") or {}
            merged_candidates.extend(delta.get("candidate_findings") or [])
            merged_errors.extend(delta.get("errors") or [])
            merged_events.extend(delta.get("events") or [])
            meta = delta.get("meta") or {}
            if isinstance(meta.get("analysis_coverage"), dict):
                coverage = meta["analysis_coverage"]
            analyzed_this = (
                "current_task_id" in delta
                or "budget" in delta
                or bool(delta.get("candidate_findings"))
            )
            if not analyzed_this:
                rest = list(batch[index:]) + rest
                break
            if budget is not None and budget.is_exhausted():
                rest = list(batch[index + 1 :]) + rest
                break
    finally:
        _in_analyze_batch.reset(token)
    out: dict[str, Any] = {
        "status": AuditStatus.ANALYZING,
        "pending_task_ids": rest,
        "candidate_findings": merged_candidates,
        "errors": merged_errors,
        "events": merged_events,
        "budget": budget,
        "usage": usage,
    }
    if coverage is not None:
        out["meta"] = {"analysis_coverage": coverage}
    return out


async def analyze_file(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Analyze the next pending task; emit candidate findings.

    When ``GraphRuntime.tools`` is set, runs allowlisted ``heuristic_scan`` /
    ``read_snippet`` tools and charges ``tool_calls_used`` on the budget.
    """
    runtime = get_runtime(config)
    if runtime.is_cancelled() or state.get("cancelled"):
        return {
            "cancelled": True,
            "status": AuditStatus.CANCELLED,
            "pending_task_ids": [],
            "events": [_event("task.cancelled", "analyze_file saw cancel")],
        }
    pending = list(state.get("pending_task_ids") or [])
    plan = state.get("plan")
    if not pending or plan is None:
        return {
            "status": AuditStatus.ANALYZING,
            "events": [_event("node.completed", "analyze_file noop")],
        }

    budget: RunBudget = state.get("budget") or RunBudget()
    if budget.is_exhausted() or (
        runtime.budget_manager is not None and runtime.budget_manager.exhausted()
    ):
        coverage = _coverage_state(state)
        coverage["skipped_units"] = list(coverage["skipped_units"]) + _units_for_ids(
            plan, pending, "budget_exhausted"
        )
        return {
            "errors": [
                NodeError(
                    code=NodeErrorCode.BUDGET,
                    node="analyze_file",
                    message="budget exhausted before analyze",
                    retriable=False,
                    details={"skipped": len(pending)},
                )
            ],
            "events": [_event("node.failed", "analyze_file budget", skipped=len(pending))],
            "pending_task_ids": [],  # stop loop; unfinished ids are in coverage
            "meta": {"analysis_coverage": coverage},
        }

    if (
        not _in_analyze_batch.get()
        and len(pending) > 1
        and runtime.extra.get("enable_parallel_analysis")
    ):
        try:
            width = max(1, min(int(runtime.extra.get("max_parallel_analyzers") or 1), 8))
        except (TypeError, ValueError):
            width = 1
        if width > 1:
            return await _analyze_batch(state, config, pending, width)

    task_id = pending[0]
    rest = pending[1:]
    task = next((t for t in plan.tasks if t.id == task_id), None)
    if task is None:
        return {
            "pending_task_ids": rest,
            "errors": [
                NodeError(
                    code=NodeErrorCode.ANALYZE,
                    node="analyze_file",
                    message=f"task {task_id} not in plan",
                )
            ],
        }

    tool_events: list[dict[str, Any]] = []
    analyze_errors: list[NodeError] = []
    tool_failed = False
    with _node_span(
        runtime,
        "analyze_file",
        **{"task.id": task_id, "path": task.target_path},
    ):
        # Load content
        content = ""
        fixture_files: dict[str, str] = dict(runtime.extra.get("fixture_files") or {})
        if task.target_path in fixture_files:
            content = fixture_files[task.target_path]
        elif "fixture_files" not in runtime.extra:
            root = _resolve_workspace(state, runtime)
            if root:
                content = _safe_read_under_root(Path(root), task.target_path)

        # Local modules this file imports. Without them a guard defined
        # elsewhere cannot be judged: the call looks defended, and the model
        # has to guess whether the defence holds.
        root_path = (
            _resolve_workspace(state, runtime) if "fixture_files" not in runtime.extra else None
        )
        import_context = _load_import_context(
            task.target_path,
            content,
            fixture_files=fixture_files,
            root=Path(root_path) if root_path else None,
        )

        candidates = (
            _heuristic_candidates(task, content) if runtime.enable_heuristic_analysis else []
        )
        usage = state.get("usage") or ModelUsage()

        # Optional ToolRegistry: allowlisted heuristic_scan
        tools = (
            runtime.get_tools()
            if runtime.enable_heuristic_analysis and hasattr(runtime, "get_tools")
            else None
        )
        if tools is not None and not budget.tool_calls_exhausted():
            try:
                from contextlib import nullcontext

                from app.services.agent.observability import SPAN_TOOL_CALL
                from app.services.agent.tooling import ToolInput

                tracer = runtime.get_tracer() if hasattr(runtime, "get_tracer") else None
                tool_cm = (
                    tracer.span(
                        SPAN_TOOL_CALL,
                        **{"tool.name": "heuristic_scan", "path": task.target_path},
                    )
                    if tracer is not None
                    else nullcontext()
                )
                with tool_cm:
                    tout = await tools.invoke(
                        ToolInput(
                            name="heuristic_scan",
                            arguments={"path": task.target_path},
                            audit_id=state.get("audit_id"),
                        )
                    )
                budget = budget.consume_tool_call()
                tool_events.append(
                    _event(
                        "tool.call",
                        "heuristic_scan",
                        success=tout.success,
                        path=task.target_path,
                        error=tout.error,
                    )
                )
                if not tout.success:
                    tool_failed = True
                    analyze_errors.append(
                        NodeError(
                            code=NodeErrorCode.TOOL,
                            node="analyze_file",
                            message=f"tool failed on {task.target_path}",
                            retriable=True,
                            details={
                                "path": task.target_path,
                                "error": (tout.error or "")[:300],
                            },
                        )
                    )
                elif isinstance(tout.data, dict):
                    hits = tout.data.get("hits") or []
                    if isinstance(hits, list):
                        for hit in hits[:20]:
                            needle = str(hit)
                            loc = SourceLocation(
                                file_path=task.target_path, start_line=1, end_line=1
                            )
                            candidates.append(
                                CandidateFinding(
                                    title=f"Tool hit: {needle}",
                                    description=f"heuristic_scan reported {needle}",
                                    severity=Severity.MEDIUM,
                                    location=loc,
                                    evidence=[
                                        Evidence(
                                            kind="tool",
                                            summary=f"tool:{needle}",
                                            location=loc,
                                            confidence=0.5,
                                        )
                                    ],
                                    confidence=0.5,
                                    analyzer="tool:heuristic_scan",
                                    rule_id=f"tool:{needle}",
                                    source_task_id=task.id,
                                )
                            )
            except Exception as exc:  # noqa: BLE001
                logger.warning("analyze_file tool invoke failed: %s", exc)
                tool_events.append(_event("tool.error", str(exc)[:200], path=task.target_path))
                analyze_errors.append(
                    NodeError(
                        code=NodeErrorCode.TOOL,
                        node="analyze_file",
                        message=f"tool failed on {task.target_path}: {type(exc).__name__}",
                        retriable=True,
                        details={"path": task.target_path, "error_type": type(exc).__name__},
                    )
                )
                tool_failed = True

        if (
            runtime.extra.get("pattern_scan")
            and tools is not None
            and not budget.tool_calls_exhausted()
        ):
            try:
                from app.services.agent.tooling import ToolInput

                tout = await tools.invoke(
                    ToolInput(
                        name="pattern_scan",
                        arguments={"path": task.target_path},
                        audit_id=state.get("audit_id"),
                    )
                )
                budget = budget.consume_tool_call()
                tool_events.append(
                    _event(
                        "tool.call",
                        "pattern_scan",
                        success=tout.success,
                        path=task.target_path,
                        error=tout.error,
                    )
                )
                if not tout.success:
                    tool_failed = True
                    analyze_errors.append(
                        NodeError(
                            code=NodeErrorCode.TOOL,
                            node="analyze_file",
                            message=f"pattern_scan failed on {task.target_path}",
                            retriable=True,
                            details={"path": task.target_path, "error": (tout.error or "")[:300]},
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("analyze_file pattern_scan failed: %s", exc)
                tool_failed = True
                analyze_errors.append(
                    NodeError(
                        code=NodeErrorCode.TOOL,
                        node="analyze_file",
                        message=f"pattern_scan failed on {task.target_path}: {type(exc).__name__}",
                        retriable=True,
                        details={"path": task.target_path, "error_type": type(exc).__name__},
                    )
                )

        # Model enrichment. Timeouts and bad payloads stay in state; they must
        # not disappear into a log line and a completed zero-finding report.
        # context_windows stays off unless the product runtime asks for ranges.
        model_failed = False
        invalid_output = False
        model_called = False
        rejected_locations: list[dict[str, Any]] = []
        window_coverage: list[dict[str, Any]] = []
        unread_windows: list[dict[str, Any]] = []
        use_windows = bool(runtime.extra.get("context_windows"))
        if use_windows:
            from app.services.agent.graph.context_windows import iter_source_windows

            source_windows = iter_source_windows(content, _MODEL_PREVIEW_CHARS)
        else:
            source_windows = [
                {
                    "start_line": 1,
                    "end_line": 1,
                    "text": content[:_MODEL_PREVIEW_CHARS],
                    "truncated": len(content) > _MODEL_PREVIEW_CHARS,
                }
            ]
        truncated = (not use_windows) and len(content) > _MODEL_PREVIEW_CHARS
        if runtime.enable_model_calls:
            for window_index, window in enumerate(source_windows):
                if budget.is_exhausted() or (
                    runtime.budget_manager is not None and runtime.budget_manager.exhausted()
                ):
                    unread_windows.extend(source_windows[window_index:])
                    break
                model_called = True
                preview = str(window.get("text") or "")
                window_truncated = bool(window.get("truncated"))
                payload = {
                    "path": task.target_path,
                    "content": preview,
                    "imports": [{"path": p, "content": c} for p, c in import_context],
                    "truncated": window_truncated if use_windows else truncated,
                }
                if use_windows:
                    payload["start_line"] = window.get("start_line")
                    payload["end_line"] = window.get("end_line")
                try:
                    from contextlib import nullcontext

                    from app.services.agent.observability import SPAN_LLM_CALL

                    tracer = runtime.get_tracer() if hasattr(runtime, "get_tracer") else None
                    llm_cm = (
                        tracer.span(
                            SPAN_LLM_CALL,
                            **{"path": task.target_path, "node": "analyze_file"},
                        )
                        if tracer is not None
                        else nullcontext()
                    )
                    with llm_cm:
                        resp = await runtime.llm.complete(
                            [
                                LLMMessage(
                                    role="system",
                                    content=(
                                        "Audit code for exploitable security defects. "
                                        "Return a JSON array of findings "
                                        "[{title,description,severity,line,cwe}]. "
                                        "Use absolute file line numbers and a CWE id "
                                        "such as CWE-89, or null when unsure. "
                                        "Report only defects an attacker could exploit. "
                                        "Do not report defences, mitigations or good "
                                        "practice as findings. Return [] when there "
                                        "is no exploitable defect. imports[] contains "
                                        "local module source; use it to assess whether "
                                        "guards and validators actually hold. Report "
                                        "bypassable guards against the call site."
                                    ),
                                ),
                                LLMMessage(role="user", content=json.dumps(payload)),
                            ]
                        )
                    usage = usage.add(resp.usage).add(ModelUsage(attempt_count=1, success_count=1))
                    tokens = resp.usage.total_tokens or 0
                    budget = budget.consume_model_call(tokens=tokens)
                    rows, parse_error = _structured_findings(resp.content or "")
                    if parse_error:
                        invalid_output = True
                        usage = usage.add(ModelUsage(invalid_output_count=1))
                        analyze_errors.append(
                            NodeError(
                                code=NodeErrorCode.LLM,
                                node="analyze_file",
                                message=f"invalid model output for {task.target_path}",
                                retriable=True,
                                details={"path": task.target_path, "reason": parse_error},
                            )
                        )
                    elif rows:
                        if runtime.extra.get("validate_findings") and content:
                            from app.services.agent.graph.context_windows import (
                                validate_model_rows,
                            )

                            rows, rejected = validate_model_rows(
                                rows, content, path=task.target_path
                            )
                            rejected_locations.extend(rejected)
                        if rows:
                            candidates.extend(_candidates_from_model_rows(task, rows))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("analyze_file llm failed: %s", exc)
                    model_failed = True
                    usage = usage.add(
                        ModelUsage(
                            attempt_count=1,
                            failure_count=1,
                            unknown_token_calls=1,
                        )
                    )
                    budget = budget.consume_model_call(tokens=0)
                    analyze_errors.append(
                        NodeError(
                            code=NodeErrorCode.LLM,
                            node="analyze_file",
                            message=f"model failed on {task.target_path}: {type(exc).__name__}",
                            retriable=True,
                            details={
                                "path": task.target_path,
                                "task_id": task_id,
                                "error_type": type(exc).__name__,
                            },
                        )
                    )
                    unread_windows.extend(source_windows[window_index + 1 :])
                    break
                window_coverage.append(
                    {
                        "path": task.target_path,
                        "start_line": window.get("start_line"),
                        "end_line": window.get("end_line"),
                        "chars": len(preview),
                    }
                )
                if not use_windows:
                    break

        coverage = _coverage_state(state)
        if model_failed or invalid_output:
            reason = "model_error" if model_failed else "invalid_model_output"
            bucket = "degraded_units" if candidates else "failed_units"
            coverage[bucket].append(_unit(task_id, task.target_path, reason))
        elif tool_failed:
            coverage["degraded_units"].append(_unit(task_id, task.target_path, "tool_error"))
        else:
            coverage["succeeded"] = int(coverage["succeeded"]) + 1
        if tool_failed:
            coverage["tool_errors"] = int(coverage["tool_errors"]) + 1
        if truncated and model_called:
            coverage["truncated_units"].append(
                _unit(
                    task_id,
                    task.target_path,
                    f"model saw {_MODEL_PREVIEW_CHARS} of {len(content)} chars",
                )
            )
        if unread_windows:
            first = unread_windows[0]
            last = unread_windows[-1]
            coverage["truncated_units"].append(
                _unit(
                    task_id,
                    task.target_path,
                    f"unread lines {first.get('start_line')}-{last.get('end_line')}",
                )
            )
        if window_coverage:
            coverage["source_windows"] = (
                list(coverage.get("source_windows") or []) + window_coverage
            )
        if rejected_locations:
            coverage["rejected_findings"] = (
                list(coverage.get("rejected_findings") or []) + rejected_locations
            )

        budget = budget.consume_file()
        _sync_budget_manager(runtime, budget)

    return {
        "status": AuditStatus.ANALYZING,
        "pending_task_ids": rest,
        "current_task_id": task_id,
        "candidate_findings": candidates,
        "budget": budget,
        "usage": usage,
        "errors": analyze_errors,
        "events": [
            _event(
                "node.completed",
                "analyze_file",
                task_id=task_id,
                path=task.target_path,
                candidates=len(candidates),
            ),
            *tool_events,
        ],
        "meta": {"analysis_coverage": coverage},
    }


async def aggregate_findings(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Normalize candidates into Finding list; drop evidence-less items.

    Also applies ``AuditRequest.severity_threshold``, which until now was
    accepted by the API and silently ignored.
    """
    candidates = list(state.get("candidate_findings") or [])
    req = state.get("request")
    threshold = req.severity_threshold if req else Severity.INFO
    min_rank = _SEV_RANK.get(threshold, 0)

    normalized: list[Finding] = []
    dropped = 0
    below_threshold = 0
    for c in candidates:
        if not c.evidence and not c.location:
            dropped += 1
            continue
        if _SEV_RANK.get(c.severity, 0) < min_rank:
            below_threshold += 1
            continue
        normalized.append(
            Finding(
                id=f"fnd_{c.fingerprint[:12]}" if c.fingerprint else c.id,
                audit_id=state.get("audit_id"),
                title=c.title,
                description=c.description,
                severity=c.severity,
                status=FindingStatus.NEW,
                verification_status=VerificationStatus.NOT_RUN,
                category=c.category,
                cwe_id=c.cwe_id,
                owasp=c.owasp,
                location=c.location,
                evidence=list(c.evidence),
                counter_evidence=list(c.counter_evidence),
                evidence_level=c.evidence_level,
                finding_state=c.finding_state,
                confidence=c.confidence,
                analyzer=c.analyzer,
                rule_id=c.rule_id,
                fingerprint=c.fingerprint,
                metadata={"source_task_id": c.source_task_id, **(c.metadata or {})},
            )
        )
    runtime = get_runtime(config)
    if runtime.extra.get("cross_file") and len(normalized) > 1:
        grouped: dict[str, list[str]] = {}
        for item in normalized:
            key = item.rule_id or item.cwe_id or item.title
            path = item.location.file_path if item.location else ""
            if key and path and path not in grouped.setdefault(key, []):
                grouped[key].append(path)
        linked: list[Finding] = []
        for item in normalized:
            key = item.rule_id or item.cwe_id or item.title
            path = item.location.file_path if item.location else ""
            others = [other for other in grouped.get(key, []) if other != path]
            if others:
                meta = dict(item.metadata or {})
                meta["related_paths"] = others
                meta["role"] = "aggregation"
                item = item.model_copy(update={"metadata": meta})
            linked.append(item)
        normalized = linked
    return {
        "status": AuditStatus.AGGREGATING,
        "normalized_findings": normalized,
        "events": [
            _event(
                "node.completed",
                "aggregate_findings",
                kept=len(normalized),
                dropped=dropped,
                below_threshold=below_threshold,
            )
        ],
    }


def _dedupe_key(f: Finding) -> tuple[str, int, str] | None:
    """Analyzer-independent identity: same file, same line, same CWE class.

    Titles are free text — the model writes "OS Command Injection in ping()"
    where the pattern scanner writes "OS Command Injection" — so a
    title-derived fingerprint never merges the two reports of one flaw.
    Returns ``None`` when the class is unknown, in which case the caller falls
    back to the exact fingerprint rather than risk merging unrelated findings.
    """
    if not f.location or not f.location.file_path or not f.cwe_id:
        return None
    return (
        f.location.file_path.replace("\\", "/").strip().lower(),
        int(f.location.start_line or 0),
        f.cwe_id.strip().upper(),
    )


def _merge_duplicate(keep: Finding, drop: Finding) -> Finding:
    """Fold ``drop`` into ``keep``, losing no evidence.

    Corroboration across analyzers is signal, so the merged confidence is the
    higher of the two and both analyzers are recorded.
    """
    analyzers = [a for a in (keep.analyzer, drop.analyzer) if a]
    merged_evidence = list(keep.evidence) + [e for e in drop.evidence if e not in keep.evidence]
    metadata = dict(keep.metadata)
    merged_from = list(metadata.get("merged_from") or [])
    for a in analyzers:
        if a not in merged_from:
            merged_from.append(a)
    metadata["merged_from"] = merged_from
    metadata["duplicate_count"] = int(metadata.get("duplicate_count") or 1) + 1

    # Narrative fields come from the model when it contributed: it reads the
    # surrounding code, so "SQL Injection via string-formatted query" beats the
    # pattern scanner's generic "Possible SQL string". Classification fields
    # still follow confidence.
    narrator = next((f for f in (keep, drop) if (f.analyzer or "").startswith("llm")), keep)

    return keep.model_copy(
        update={
            "title": narrator.title,
            "description": narrator.description or keep.description or drop.description,
            "evidence": merged_evidence,
            "confidence": max(keep.confidence, drop.confidence),
            "severity": (
                keep.severity
                if _SEV_RANK.get(keep.severity, 0) >= _SEV_RANK.get(drop.severity, 0)
                else drop.severity
            ),
            "recommendation": keep.recommendation or drop.recommendation,
            "analyzer": "+".join(analyzers) if len(analyzers) > 1 else keep.analyzer,
            "rule_id": keep.rule_id or drop.rule_id,
            "metadata": metadata,
        }
    )


def _prefer(a: Finding, b: Finding) -> tuple[Finding, Finding]:
    """Order a duplicate pair as (keep, drop): higher confidence wins."""
    return (a, b) if a.confidence >= b.confidence else (b, a)


async def deduplicate_findings(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Merge duplicate findings across analyzers.

    Two passes: an exact fingerprint pass (identical reports), then a semantic
    pass on file + line + CWE so the LLM and the pattern scanner reporting the
    same flaw collapse into one corroborated finding instead of two.
    """
    from app.services.agent.domain.mappers import fingerprint_components

    findings = list(state.get("normalized_findings") or [])
    seen: dict[str, Finding] = {}
    order: list[str] = []
    for f in findings:
        fp = f.fingerprint or fingerprint_components(
            file_path=f.location.file_path if f.location else None,
            start_line=f.location.start_line if f.location else None,
            title=f.title,
            rule_id=f.rule_id,
            cwe_id=f.cwe_id,
        )
        if fp in seen:
            keep, drop = _prefer(seen[fp], f.model_copy(update={"fingerprint": fp}))
            seen[fp] = _merge_duplicate(keep, drop)
            continue
        seen[fp] = f.model_copy(update={"fingerprint": fp})
        order.append(fp)

    # Semantic pass: same flaw, different wording.
    by_class: dict[tuple[str, int, str], str] = {}
    dropped: set[str] = set()
    for fp in order:
        f = seen[fp]
        key = _dedupe_key(f)
        if key is None:
            continue
        first_fp = by_class.get(key)
        if first_fp is None:
            by_class[key] = fp
            continue
        keep, drop = _prefer(seen[first_fp], f)
        merged = _merge_duplicate(keep, drop)
        # The survivor keeps the slot of whichever report came first.
        seen[first_fp] = merged.model_copy(update={"fingerprint": seen[first_fp].fingerprint})
        seen[fp] = seen[fp].model_copy(update={"duplicate_of": merged.id})
        dropped.add(fp)

    deduped = [seen[k] for k in order if k not in dropped]
    return {
        "status": AuditStatus.AGGREGATING,
        "normalized_findings": deduped,
        "events": [
            _event(
                "node.completed",
                "deduplicate_findings",
                before=len(findings),
                after=len(deduped),
            )
        ],
    }


_SEV_RANK = {
    Severity.CRITICAL: 5,
    Severity.HIGH: 4,
    Severity.MEDIUM: 3,
    Severity.LOW: 2,
    Severity.INFO: 1,
}


async def prioritize_findings(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Sort findings by severity then confidence; assign risk_score."""
    findings = list(state.get("normalized_findings") or [])
    ranked: list[Finding] = []
    for f in findings:
        sev_score = _SEV_RANK.get(f.severity, 0) * 15
        conf_score = f.confidence * 20
        risk = min(100.0, sev_score + conf_score)
        ranked.append(f.model_copy(update={"risk_score": risk}))
    ranked.sort(
        key=lambda x: (_SEV_RANK.get(x.severity, 0), x.confidence, x.risk_score),
        reverse=True,
    )
    return {
        "normalized_findings": ranked,
        "events": [_event("node.completed", "prioritize_findings", count=len(ranked))],
    }


async def verify_findings_node(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Run the M7 verification subgraph over the prioritised findings.

    Gated on ``AuditRequest.enable_verification``, which until now was a field
    nobody read: the subgraph existed and was tested, but nothing in the audit
    graph ever called it, so every finding stayed NOT_RUN regardless.

    Execution stays off unless the request asks for it *and* the runtime is not
    offline — Phase 1's invariant is that the default path runs no untrusted
    code (ADR-003), and that is enforced here rather than assumed.
    """
    runtime = get_runtime(config)
    findings = list(state.get("normalized_findings") or [])
    req = state.get("request")

    if not findings or req is None or not req.enable_verification:
        return {
            "events": [
                _event(
                    "node.completed",
                    "verify_findings skipped",
                    reason="disabled" if req is not None else "no request",
                    count=len(findings),
                )
            ]
        }

    allow_execution = bool(req.enable_verification) and not runtime.offline

    with _node_span(runtime, "verify_findings", count=len(findings)):
        try:
            from app.services.agent.graph.subgraphs import verify_findings

            verified = await verify_findings(
                findings,
                allow_execution=allow_execution,
                sandbox=runtime.extra.get("sandbox"),
            )
        except Exception as exc:  # noqa: BLE001 — verification must not sink a run
            logger.warning("verification failed: %s", exc)
            return {"events": [_event("node.failed", f"verify_findings: {exc}"[:200])]}

    # VerifiedFinding subclasses Finding, so these are the findings — enriched,
    # not wrapped.
    updated: list[Finding] = [vf for vf in verified if isinstance(vf, Finding)]

    counts: dict[str, int] = {}
    for f in updated:
        key = getattr(f.verification_status, "value", str(f.verification_status))
        counts[key] = counts.get(key, 0) + 1

    return {
        "normalized_findings": updated or findings,
        "events": [
            _event(
                "node.completed",
                "verify_findings",
                count=len(updated),
                allow_execution=allow_execution,
                **counts,
            )
        ],
    }


async def generate_report(state: AuditState, config: RunnableConfig | None = None) -> dict:
    """Build AuditReport with explicit Phase-1 NOT_RUN verification note."""
    runtime = get_runtime(config)
    if runtime.is_cancelled() or state.get("cancelled"):
        return {
            "cancelled": True,
            "status": AuditStatus.CANCELLED,
            "events": [_event("task.cancelled", "generate_report aborted")],
        }
    findings = list(state.get("normalized_findings") or [])
    # Enforce Phase 1: verification_status stays NOT_RUN unless already set otherwise
    final_findings: list[Finding] = []
    for f in findings:
        if f.verification_status is None:
            f = f.with_verification(VerificationStatus.NOT_RUN)
        final_findings.append(f)

    not_run = sum(1 for f in final_findings if f.verification_status is VerificationStatus.NOT_RUN)
    budget = state.get("budget")
    pending = list(state.get("pending_task_ids") or [])
    plan = state.get("plan")
    planned = len(plan.tasks) if plan is not None else 0
    budget_hit = bool(budget is not None and budget.is_exhausted())
    meta = dict(state.get("meta") or {})
    scanner_status = meta.get("scanner_status")
    scanner_incomplete = scanner_status in {"partial", "error"}
    manifest_limited = bool(meta.get("manifest_limited"))
    manifest_incomplete = bool(meta.get("manifest_incomplete"))
    cov = _coverage_state(state)
    failed_units = list(cov["failed_units"])
    degraded_units = list(cov["degraded_units"])
    skipped_units = list(cov["skipped_units"])
    truncated_units = list(cov["truncated_units"])
    # The router stops the loop as soon as the budget is spent, so the next
    # analyze node never runs to record the queue. Name those units here.
    if budget_hit and pending:
        known = {unit.get("task_id") for unit in skipped_units if isinstance(unit, dict)}
        for unit in _units_for_ids(plan, pending, "budget_exhausted"):
            if unit["task_id"] not in known:
                skipped_units.append(unit)
    manifest = state.get("manifest")
    source_coverage = dict(state["request"].config.get("source_coverage") or {})
    omitted_units: list[dict[str, Any]] = list(source_coverage.get("omitted_units") or [])
    if manifest_limited and manifest is not None:
        omitted_units.extend(
            [
                {"path": path, "reason": "file_budget"}
                for path in manifest.stats.get("omitted_paths", [])
            ]
        )
    omitted_units.extend(
        {"path": issue.get("path"), "reason": issue.get("code")}
        for issue in meta.get("manifest_issues", [])
    )
    planner_error = cov.get("planner_error")
    tool_errors = int(cov["tool_errors"])
    succeeded = int(cov["succeeded"])
    usage = state.get("usage") or ModelUsage()
    source_windows = list(cov.get("source_windows") or [])
    rejected_findings = list(cov.get("rejected_findings") or [])
    model_unavailable = bool((state["request"].config or {}).get("model_unavailable"))
    coverage_gap = bool(
        failed_units
        or degraded_units
        or skipped_units
        or truncated_units
        or omitted_units
        or planner_error
        or tool_errors
        or rejected_findings
        or model_unavailable
    )
    scope_incomplete = (
        bool(pending)
        or scanner_incomplete
        or manifest_limited
        or manifest_incomplete
        or (budget_hit and planned > 0 and (budget.files_analyzed if budget else 0) < planned)
    )
    # Model/tool gaps are incomplete even when the queue was drained.
    incomplete = scope_incomplete or coverage_gap
    # Every attempted unit failed and nothing usable was produced.
    # Budget stops before any attempt stay partial, not failed.
    closed_failure = (
        planned > 0
        and not final_findings
        and succeeded == 0
        and not degraded_units
        and bool(failed_units or planner_error)
    )
    strict = bool(state["request"].config.get("strict"))
    if strict and scanner_status == "error":
        terminal = AuditStatus.FAILED
    elif closed_failure:
        terminal = AuditStatus.FAILED
    elif incomplete:
        terminal = AuditStatus.PARTIAL
    else:
        terminal = AuditStatus.COMPLETED
    unfinished = [
        str(unit.get("path") or unit.get("task_id"))
        for unit in (
            failed_units + degraded_units + skipped_units + truncated_units + omitted_units
        )
        if isinstance(unit, dict)
    ]
    shown = ", ".join(unfinished[:20])
    if len(unfinished) > 20:
        shown = f"{shown}, and {len(unfinished) - 20} more"
    gap_sentence = ""
    if shown:
        gap_sentence = f" Unfinished or degraded units: {shown}."
    elif planner_error:
        gap_sentence = " Planner model failed; deterministic plan was used."
    if model_unavailable:
        gap_sentence = (
            f"{gap_sentence} No model API key was configured; pattern analysis ran without a model."
        )
    if rejected_findings:
        gap_sentence = f"{gap_sentence} {len(rejected_findings)} model row(s) failed location or evidence checks."
    confirmed = sum(
        1 for f in final_findings if f.verification_status is VerificationStatus.CONFIRMED
    )
    if not_run == len(final_findings):
        phase1 = (
            f"Phase 1: {not_run} finding(s) have verification_status=not_run "
            f"(no untrusted code execution)."
        )
        verify_body = (
            "Sandbox verification was **not run** in this phase. "
            "Treat all results as unverified analysis signals."
        )
    else:
        phase1 = (
            f"Static pattern recheck: {confirmed} confirmed, {not_run} not_run. "
            "Pattern matches remain inconclusive; exploitability was not verified."
        )
        verify_body = phase1
    if terminal is AuditStatus.FAILED:
        summary = (
            f"Audit failed: analysis produced no usable result "
            f"({len(failed_units)} failed unit(s), {len(final_findings)} finding(s))."
            f"{gap_sentence} {phase1}"
        )
    elif incomplete:
        summary = (
            f"Audit incomplete (partial): scanner, budget, model, or scope limited run with "
            f"{len(final_findings)} finding(s); "
            f"{len(pending) + len(skipped_units)} task(s) not fully completed."
            f"{gap_sentence} {phase1}"
        )
    else:
        summary = f"Audit completed with {len(final_findings)} finding(s). {phase1}"
    coverage_meta = {
        "source": source_coverage,
        "planned": planned,
        "succeeded": succeeded,
        "failed_units": failed_units,
        "degraded_units": degraded_units,
        "skipped_units": skipped_units,
        "omitted_units": omitted_units,
        "truncated_units": truncated_units,
        "source_windows": source_windows,
        "rejected_findings": rejected_findings,
        "model_unavailable": model_unavailable,
        "planner_error": planner_error,
        "tool_errors": tool_errors,
        "model_attempts": usage.attempt_count,
        "model_successes": usage.success_count,
        "model_failures": usage.failure_count,
        "unknown_token_calls": usage.unknown_token_calls,
        "invalid_outputs": usage.invalid_output_count,
        "known_call_count": usage.call_count,
    }
    sections = [
        AuditReportSection(
            title="Executive Summary",
            body_markdown=summary,
            order=0,
        ),
        AuditReportSection(
            title="Findings",
            body_markdown="\n".join(
                f"- **{f.severity.value}**: {f.title} "
                f"({f.location.file_path if f.location else 'n/a'})"
                for f in final_findings
            )
            or "_No findings._",
            finding_ids=[f.id for f in final_findings],
            order=1,
        ),
        AuditReportSection(
            title="Verification gaps",
            body_markdown=verify_body,
            order=2,
        ),
    ]
    report = AuditReport(
        audit_id=state["audit_id"],
        title="Security Audit Report",
        status=terminal,
        summary=summary,
        sections=sections,
        findings=final_findings,
        plan=state.get("plan"),
        usage=usage,
        metadata={
            "budget_exhausted": budget_hit,
            "pending_task_ids": pending,
            "manifest_limited": manifest_limited,
            "manifest_incomplete": manifest_incomplete,
            "manifest_issues": meta.get("manifest_issues", []),
            "scanner_status": scanner_status,
            "scanner_coverage": meta.get("scanner_coverage"),
            "scanner_issues": meta.get("scanner_issues", []),
            "coverage": coverage_meta,
        },
    ).recount_severities()

    return {
        "status": terminal,
        "report": report,
        "normalized_findings": final_findings,
        "events": [
            _event(
                "node.completed",
                "generate_report",
                findings=len(final_findings),
                not_run=not_run,
                status=terminal.value,
                budget_exhausted=budget_hit,
            )
        ],
        "meta": {
            "budget_exhausted": budget_hit,
            "terminal_status": terminal.value,
            "manifest_limited": manifest_limited,
            "manifest_incomplete": manifest_incomplete,
        },
    }
