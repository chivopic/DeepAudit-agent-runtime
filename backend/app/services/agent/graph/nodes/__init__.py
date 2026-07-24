"""Phase 1 audit graph node implementations (skeleton with real domain I/O)."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

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


def _lang_for(path: str) -> Optional[str]:
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


def _resolve_workspace(state: AuditState, runtime: GraphRuntime) -> Optional[Path]:
    if runtime.workspace_root:
        return Path(runtime.workspace_root)
    req = state.get("request")
    if not req:
        return None
    repo: RepositoryRef = req.repository
    if repo.local_path:
        return Path(repo.local_path)
    if runtime.extra.get("fixture_files"):
        # Synthetic workspace: files provided as {rel_path: content}
        return None
    return None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


async def validate_request(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
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


async def ingest_repository(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
    """Snapshot repository metadata (local path or fixture map)."""
    runtime = get_runtime(config)
    req = state["request"]
    root = _resolve_workspace(state, runtime)
    fixture_files: dict[str, str] = dict(runtime.extra.get("fixture_files") or {})

    file_count = 0
    total_bytes = 0
    languages: set[str] = set()
    snapshot_path: Optional[str] = None

    if fixture_files:
        for p, content in fixture_files.items():
            file_count += 1
            total_bytes += len(content.encode("utf-8"))
            lang = _lang_for(p)
            if lang:
                languages.add(lang)
        snapshot_path = "fixture://" + (state.get("audit_id") or "aud")
    elif root and root.exists():
        snapshot_path = str(root)
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(part in _SKIP_DIR_NAMES for part in path.parts):
                continue
            if path.suffix.lower() not in _SOURCE_EXTS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
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


async def build_manifest(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
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
    files: list[FileArtifact] = []
    excluded: list[str] = []
    fixture_files: dict[str, str] = dict(runtime.extra.get("fixture_files") or {})

    def _allowed(rel: str) -> bool:
        if any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in exclude):
            return False
        if not include:
            return True
        return any(rel == i or rel.startswith(i.rstrip("/") + "/") for i in include)

    if fixture_files:
        for rel, content in sorted(fixture_files.items()):
            rel_n = rel.replace("\\", "/")
            if not _allowed(rel_n):
                excluded.append(rel_n)
                continue
            raw = content.encode("utf-8")
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
        if root and root.exists():
            for path in sorted(root.rglob("*")):
                if not path.is_file():
                    continue
                if any(part in _SKIP_DIR_NAMES for part in path.parts):
                    continue
                if path.suffix.lower() not in _SOURCE_EXTS:
                    continue
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                if not _allowed(rel):
                    excluded.append(rel)
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
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
    if budget.max_files > 0 and len(files) > budget.max_files:
        files = files[: budget.max_files]

    manifest = RepositoryManifest(
        snapshot_id=snap.id,
        files=files,
        excluded_paths=excluded,
        stats={"selected": len(files), "excluded": len(excluded)},
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
        "meta": {"manifest_built": True},
    }


async def plan_audit(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
    """Create an AuditPlan — FakeLLM optional structured assist, then deterministic plan."""
    runtime = get_runtime(config)
    req = state["request"]
    manifest = state.get("manifest")
    if manifest is None or not manifest.files:
        # Empty plan is valid (no files)
        plan = AuditPlan(
            audit_id=state["audit_id"],
            tasks=[],
            strategy="file_parallel",
            rationale="no files in manifest",
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
    with _node_span(runtime, "plan_audit", **{"audit.id": state.get("audit_id")}):
        # Optional LLM call (FakeLLM in tests) — counts against budget
        try:
            resp = await runtime.llm.complete(
                [
                    LLMMessage(
                        role="system", content="You plan security audits. Reply JSON."
                    ),
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
            usage = usage.add(resp.usage)
            tokens = resp.usage.total_tokens or 0
            budget = budget.consume_model_call(tokens=tokens)
            if resp.content:
                rationale = f"llm:{resp.content[:200]}"
        except Exception as exc:  # noqa: BLE001 — planner must not fail hard
            logger.warning("plan_audit llm failed: %s", exc)

        tasks: list[AuditTaskSpec] = []
        for f in manifest.files:
            tasks.append(
                AuditTaskSpec(
                    target_path=f.path,
                    language=f.language,
                    analyzer="llm" if runtime.offline else "hybrid",
                    priority=f.priority,
                    max_tokens=min(4000, budget.remaining_tokens() or 4000),
                )
            )
        tasks.sort(key=lambda t: t.priority, reverse=True)

        plan = AuditPlan(
            audit_id=state["audit_id"],
            tasks=tasks,
            strategy="sequential",  # M2: sequential analyze loop
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
        "events": [
            _event("node.completed", "plan_audit", task_count=len(tasks))
        ],
        "meta": {"planned": True},
    }


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
        ("SELECT * FROM", "Possible SQL string", Severity.MEDIUM, "CWE-89"),
        ("password =", "Hardcoded secret pattern", Severity.MEDIUM, "CWE-798"),
    ]
    out: list[CandidateFinding] = []
    lines = content.splitlines()
    for i, line in enumerate(lines, start=1):
        for needle, title, sev, cwe in patterns:
            if needle in line:
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


async def finalize_cancelled(
    state: AuditState, config: Optional[RunnableConfig] = None
) -> dict:
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


async def analyze_file(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
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
        return {
            "errors": [
                NodeError(
                    code=NodeErrorCode.BUDGET,
                    node="analyze_file",
                    message="budget exhausted before analyze",
                    retriable=False,
                )
            ],
            "events": [_event("node.failed", "analyze_file budget")],
            "pending_task_ids": [],  # stop loop
        }

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
        else:
            root = _resolve_workspace(state, runtime)
            if root:
                content = _safe_read_under_root(Path(root), task.target_path)

        candidates = _heuristic_candidates(task, content)
        usage = state.get("usage") or ModelUsage()

        # Optional ToolRegistry: allowlisted heuristic_scan
        tools = runtime.get_tools() if hasattr(runtime, "get_tools") else None
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
                if tout.success and isinstance(tout.data, dict):
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
                tool_events.append(
                    _event("tool.error", str(exc)[:200], path=task.target_path)
                )

        # Fake / real LLM enrichment
        try:
            from contextlib import nullcontext

            from app.services.agent.observability import SPAN_LLM_CALL

            tracer = runtime.get_tracer() if hasattr(runtime, "get_tracer") else None
            llm_cm = (
                tracer.span(
                    SPAN_LLM_CALL, **{"path": task.target_path, "node": "analyze_file"}
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
                                "Return JSON array of findings "
                                "[{title,description,severity,line}]."
                            ),
                        ),
                        LLMMessage(
                            role="user",
                            content=json.dumps(
                                {
                                    "path": task.target_path,
                                    "content": content[:4000],
                                }
                            ),
                        ),
                    ]
                )
            usage = usage.add(resp.usage)
            tokens = resp.usage.total_tokens or 0
            budget = budget.consume_model_call(tokens=tokens)
            # Parse optional structured findings from FakeLLM
            text = (resp.content or "").strip()
            if text.startswith("["):
                try:
                    arr = json.loads(text)
                    for item in arr:
                        line = int(item.get("line") or 1)
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
                        candidates.append(
                            CandidateFinding(
                                title=str(item.get("title") or "LLM finding"),
                                description=str(
                                    item.get("description") or item.get("title") or ""
                                ),
                                severity=sev,
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
                except json.JSONDecodeError:
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.warning("analyze_file llm failed: %s", exc)

        budget = budget.consume_file()
        _sync_budget_manager(runtime, budget)

    return {
        "status": AuditStatus.ANALYZING,
        "pending_task_ids": rest,
        "current_task_id": task_id,
        "candidate_findings": candidates,
        "budget": budget,
        "usage": usage,
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
    }


async def aggregate_findings(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
    """Normalize candidates into Finding list; drop evidence-less items."""
    candidates = list(state.get("candidate_findings") or [])
    normalized: list[Finding] = []
    dropped = 0
    for c in candidates:
        if not c.evidence and not c.location:
            dropped += 1
            continue
        normalized.append(
            Finding(
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
                confidence=c.confidence,
                analyzer=c.analyzer,
                rule_id=c.rule_id,
                fingerprint=c.fingerprint,
                metadata={"source_task_id": c.source_task_id, **(c.metadata or {})},
            )
        )
    return {
        "status": AuditStatus.AGGREGATING,
        "normalized_findings": normalized,
        "events": [
            _event(
                "node.completed",
                "aggregate_findings",
                kept=len(normalized),
                dropped=dropped,
            )
        ],
    }


async def deduplicate_findings(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
    """Dedupe by path + line + title/rule fingerprint."""
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
            # Keep higher confidence
            if f.confidence > seen[fp].confidence:
                seen[fp] = f.model_copy(update={"fingerprint": fp})
            continue
        seen[fp] = f.model_copy(update={"fingerprint": fp})
        order.append(fp)

    deduped = [seen[k] for k in order]
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


async def prioritize_findings(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
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
        "events": [
            _event("node.completed", "prioritize_findings", count=len(ranked))
        ],
    }


async def generate_report(state: AuditState, config: Optional[RunnableConfig] = None) -> dict:
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

    not_run = sum(
        1
        for f in final_findings
        if f.verification_status is VerificationStatus.NOT_RUN
    )
    budget = state.get("budget")
    pending = list(state.get("pending_task_ids") or [])
    plan = state.get("plan")
    planned = len(plan.tasks) if plan is not None else 0
    budget_hit = bool(budget is not None and budget.is_exhausted())
    incomplete = bool(pending) or (
        budget_hit and planned > 0 and (budget.files_analyzed if budget else 0) < planned
    )
    terminal = AuditStatus.PARTIAL if incomplete else AuditStatus.COMPLETED
    if incomplete:
        summary = (
            f"Audit partial ({terminal.value}): budget or scope limited run with "
            f"{len(final_findings)} finding(s); "
            f"{len(pending)} task(s) remaining. "
            f"Phase 1: {not_run} finding(s) have verification_status=not_run."
        )
    else:
        summary = (
            f"Audit completed with {len(final_findings)} finding(s). "
            f"Phase 1: {not_run} finding(s) have verification_status=not_run "
            f"(no untrusted code execution)."
        )
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
            body_markdown=(
                "Sandbox verification was **not run** in this phase. "
                "Treat all results as unverified static/LLM signals."
            ),
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
        usage=state.get("usage") or ModelUsage(),
        metadata={
            "budget_exhausted": budget_hit,
            "pending_task_ids": pending,
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
        "meta": {"budget_exhausted": budget_hit, "terminal_status": terminal.value},
    }
