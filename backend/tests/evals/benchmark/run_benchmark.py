"""Run both audit engines over the same labelled corpus and compare them.

This is the instrument for the dual-path decision: it answers "is the LangGraph
path good enough to replace ReAct yet" with numbers instead of impressions.

Not part of the default test run — it needs a real model and costs tokens.

    uv run python -m tests.evals.benchmark.run_benchmark --engines graph,react
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from tests.evals.benchmark.labels import CORPUS
from tests.evals.benchmark.scorer import EngineScore, score

CORPUS_DIR = Path(__file__).parent / "corpus"


def materialise() -> str:
    """Copy the corpus to a scratch directory so engines see a normal project."""
    dest = tempfile.mkdtemp(prefix="deepaudit-benchmark-")
    shutil.copytree(CORPUS_DIR, Path(dest) / "src")
    return str(Path(dest) / "src")


async def react_preconditions() -> tuple[list[str], list[str]]:
    """What ReAct needs to run at full strength, and what is missing.

    Returns ``(blocking, advisory)``.

    A benchmark that quietly reports a crippled baseline is worse than no
    benchmark: it would "prove" the new engine wins. But not every missing
    piece invalidates a run to the same degree, so the two are separated and
    only *blocking* gaps withhold the verdict.
    """
    blocking: list[str] = []
    advisory: list[str] = []

    from app.core.config import settings

    # Blocking: the external scanners are a primary detection path. Without
    # them ReAct is missing findings it would normally get for free.
    try:
        import docker

        client = docker.from_env()
        client.ping()
        try:
            client.images.get(settings.SANDBOX_IMAGE)
        except Exception:  # noqa: BLE001
            blocking.append(
                f"sandbox image {settings.SANDBOX_IMAGE} not present — "
                "Semgrep/Bandit/Gitleaks unavailable"
            )
    except Exception as exc:  # noqa: BLE001
        blocking.append(f"docker unavailable ({exc}) — external scanners disabled")

    # Advisory: RAG is a retrieval aid — it helps the agent decide *where* to
    # look in a large repository. On a corpus this small the agent can
    # enumerate every file directly, so its absence should cost little. Stated
    # loudly anyway, because "should" is not "does".
    if not (
        getattr(settings, "EMBEDDING_API_KEY", None)
        or getattr(settings, "OPENAI_API_KEY", None)
    ):
        advisory.append(
            "embeddings (no EMBEDDING_API_KEY/OPENAI_API_KEY) — RAG code search "
            "disabled; matters less on a small corpus, but treat ReAct's recall "
            "as a lower bound"
        )

    return blocking, advisory


def _llm_service():
    from app.services.llm.service import LLMService

    # Credentials come from the environment, as everywhere else.
    return LLMService(user_config=None)


# ---------------------------------------------------------------------------
# LangGraph engine
# ---------------------------------------------------------------------------


async def run_graph(root: str) -> EngineScore:
    from app.services.agent.application.facade import GraphAuditFacade
    from app.services.agent.domain import AuditRequest, RunBudget
    from app.services.agent.domain.repository import RepositoryRef
    from app.services.agent.graph.llm_gateway import LLMServiceGateway
    from app.services.agent.graph.runtime import GraphRuntime

    req = AuditRequest(
        repository=RepositoryRef(source_type="local", local_path=root),
        languages=["python", "javascript"],
        budget=RunBudget(max_tokens=400_000, max_model_calls=80),
    )
    runtime = GraphRuntime(llm=LLMServiceGateway(_llm_service()), offline=False)
    facade = GraphAuditFacade()

    t0 = time.time()
    try:
        task = await facade.start(req, runtime=runtime)
        findings = await facade.list_findings(req.id)
    except Exception as exc:  # noqa: BLE001 — a crashed engine is a result too
        return EngineScore(
            engine="graph",
            labels_total=len(CORPUS.vulnerable),
            error=f"{type(exc).__name__}: {exc}",
            seconds=time.time() - t0,
        )

    return score(
        "graph",
        findings,
        CORPUS.vulnerable,
        CORPUS.safe_paths,
        tokens=int(task.get("tokens_used") or 0),
        seconds=time.time() - t0,
    )


# ---------------------------------------------------------------------------
# ReAct engine (production path), driven headlessly
# ---------------------------------------------------------------------------


async def run_react(root: str) -> EngineScore:
    blocking, advisory = await react_preconditions()

    from app.api.v1.endpoints.agent_tasks import (
        _collect_project_info,
        _initialize_tools,
    )
    from app.services.agent.agents.analysis import AnalysisAgent
    from app.services.agent.agents.orchestrator import OrchestratorAgent
    from app.services.agent.agents.recon import ReconAgent
    from app.services.agent.agents.verification import VerificationAgent

    llm = _llm_service()
    t0 = time.time()
    try:
        tools = await _initialize_tools(root, llm, None, sandbox_manager=None)
        sub = {
            "recon": ReconAgent(llm_service=llm, tools=tools.get("recon", {})),
            "analysis": AnalysisAgent(llm_service=llm, tools=tools.get("analysis", {})),
            "verification": VerificationAgent(
                llm_service=llm, tools=tools.get("verification", {})
            ),
        }
        orchestrator = OrchestratorAgent(
            llm_service=llm, tools=tools.get("orchestrator", {}), sub_agents=sub
        )
        project_info = await _collect_project_info(root, "benchmark")
        result = await orchestrator.run(
            {
                "project_info": project_info,
                "config": {
                    "target_vulnerabilities": [],
                    # analysis_only: the benchmark compares detection, and
                    # sandbox verification needs a worker + images.
                    "verification_level": "analysis_only",
                    "exclude_patterns": [],
                    "target_files": [],
                    "max_iterations": 30,
                },
                "project_root": root,
                "task_id": "benchmark",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return EngineScore(
            engine="react",
            labels_total=len(CORPUS.vulnerable),
            error=f"{type(exc).__name__}: {exc}",
            seconds=time.time() - t0,
            degraded=blocking,
            advisory=advisory,
        )

    findings: list[dict[str, Any]] = []
    if getattr(result, "data", None):
        findings = [f for f in (result.data.get("findings") or []) if isinstance(f, dict)]

    scored = score(
        "react",
        findings,
        CORPUS.vulnerable,
        CORPUS.safe_paths,
        tokens=int(getattr(result, "tokens_used", 0) or 0),
        seconds=time.time() - t0,
    )
    scored.degraded = blocking
    scored.advisory = advisory
    return scored


# ---------------------------------------------------------------------------


ENGINES = {"graph": run_graph, "react": run_react}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engines", default="graph,react")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help=(
            "runs per engine. Both engines are nondeterministic — ReAct scored "
            "8, 10 and 8 out of 14 on three identical runs — so a single run is "
            "a smoke reading, not a measurement. Use 3+ before concluding."
        ),
    )
    args = parser.parse_args()

    names = [n.strip() for n in args.engines.split(",") if n.strip()]
    root = materialise()
    print(f"corpus: {root}")
    print(f"labels: {len(CORPUS.vulnerable)} vulnerable, {len(CORPUS.safe_paths)} safe files\n")

    results: list[EngineScore] = []
    spreads: dict[str, list[int]] = {}
    for name in names:
        runner = ENGINES.get(name)
        if runner is None:
            print(f"unknown engine: {name}")
            continue
        for i in range(max(1, args.repeat)):
            label = f"{name} ({i + 1}/{args.repeat})" if args.repeat > 1 else name
            print(f"running {label} ...")
            r = await runner(root)
            spreads.setdefault(name, []).append(r.labels_found)
            results.append(r)

    print("\n" + "=" * 100)
    for r in results:
        print(r.row())
        if r.degraded:
            print("    ⚠ DEGRADED — not a valid comparison, this engine ran without:")
            for d in r.degraded:
                print(f"      - {d}")
        if r.advisory:
            print("    ⓘ caveat:")
            for a in r.advisory:
                print(f"      - {a}")
        if r.error:
            print(f"    ERROR: {r.error}")
        if r.missed:
            print(f"    missed ({len(r.missed)}): " + "; ".join(r.missed[:6]))
        if r.fp_examples:
            print("    false positives on safe files:")
            for e in r.fp_examples:
                print(f"      - {e}")
    print("=" * 100)

    for name, found in spreads.items():
        if len(found) > 1:
            print(
                f"{name}: recall across {len(found)} runs "
                f"min {min(found)} / max {max(found)} / mean "
                f"{sum(found) / len(found):.1f} of {len(CORPUS.vulnerable)}"
            )
            if max(found) != min(found):
                print(
                    f"    spread of {max(found) - min(found)} — single runs of "
                    "this engine cannot be compared"
                )

    if any(r.degraded for r in results) and len(results) > 1:
        print(
            "\nVERDICT WITHHELD: at least one engine ran degraded, so these\n"
            "numbers cannot be used to compare engines. Fix the preconditions\n"
            "above and re-run before drawing any conclusion."
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
