"""R1: execution results stay trustworthy past the default graph step limit."""

from __future__ import annotations

import json

import pytest

from app.services.agent.agents.base import (
    AgentConfig,
    AgentPattern,
    AgentResult,
    AgentType,
    BaseAgent,
)
from app.services.agent.application import AuditRunner
from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    ModelUsage,
    RepositoryRef,
    RunBudget,
)
from app.services.agent.graph.limits import (
    bounded_analysis_units,
    recursion_limit_for_budget,
)
from app.services.agent.graph.llm import LLMResponse
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.persistence import InMemoryBusinessStore


def _request(**kwargs) -> AuditRequest:
    budget = kwargs.pop(
        "budget",
        RunBudget(max_tokens=100_000, max_model_calls=200, max_files=100),
    )
    repository = kwargs.pop(
        "repository",
        RepositoryRef(source_type="local", local_path="fixture://r1"),
    )
    return AuditRequest(repository=repository, budget=budget, **kwargs)


def _files(n: int, body: str = "value = 1\n") -> dict[str, str]:
    return {f"f{i}.py": body for i in range(n)}


class _ScriptedLLM:
    """Queue of responses or exceptions. One entry is consumed per complete()."""

    def __init__(self, steps: list[str | Exception]) -> None:
        self._steps = list(steps)
        self.calls = 0

    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if not self._steps:
            raise TimeoutError("script exhausted")
        step = self._steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return LLMResponse(
            content=step,
            usage=ModelUsage(
                provider="fake",
                model="fake",
                input_tokens=4,
                output_tokens=4,
                total_tokens=8,
                call_count=1,
            ),
        )


class _AlwaysTimeout:
    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raise TimeoutError("model unavailable")


class _ProbeAgent(BaseAgent):
    async def run(self, input_data):  # type: ignore[no-untyped-def]
        return AgentResult(success=True, data=input_data)


def test_recursion_limit_tracks_budget_not_a_fixed_cap():
    wide = RunBudget(max_files=100, max_model_calls=200, max_tokens=100_000)
    assert bounded_analysis_units(wide) == 100
    assert recursion_limit_for_budget(wide) == 117
    assert recursion_limit_for_budget(wide) < 500
    tight = RunBudget(max_files=2, max_model_calls=50, max_tokens=10_000)
    assert recursion_limit_for_budget(tight) < recursion_limit_for_budget(wide)
    # No file cap: the model-call cap is the bound.
    assert bounded_analysis_units(RunBudget(max_files=0, max_model_calls=30)) == 30


def test_model_usage_add_keeps_attempt_accounting():
    left = ModelUsage(
        call_count=1, attempt_count=1, success_count=1, input_tokens=1, output_tokens=1
    )
    right = ModelUsage(attempt_count=1, failure_count=1, unknown_token_calls=1)
    total = left.add(right)
    assert total.call_count == 1
    assert total.attempt_count == 2
    assert total.success_count == 1
    assert total.failure_count == 1
    assert total.unknown_token_calls == 1
    assert total.total_tokens == 2


def test_base_agent_cancel_callback_is_safe_before_cancel():
    config = AgentConfig(
        name="probe",
        agent_type=AgentType.RECON,
        pattern=AgentPattern.REACT,
    )
    agent = _ProbeAgent(config, llm_service=None, tools={})
    assert agent.is_cancelled is False
    agent.set_cancel_callback(lambda: False)
    assert agent.is_cancelled is False
    agent.set_cancel_callback(lambda: True)
    assert agent.is_cancelled is True

    other = _ProbeAgent(config, llm_service=None, tools={})
    other.cancel()
    assert other.is_cancelled is True
    # cancel() must not be required to create the callback attribute.
    assert other._cancel_callback is None


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [16, 30, 100])
async def test_bounded_file_budget_completes(count: int):
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_ScriptedLLM(["{}"] * (count + 1)),
        offline=True,
        enable_heuristic_analysis=False,
        extra={"fixture_files": _files(count)},
    )
    result = await runner.run(
        _request(budget=RunBudget(max_tokens=100_000, max_model_calls=count + 5, max_files=count)),
        runtime=runtime,
    )
    assert result.status is AuditStatus.COMPLETED
    assert result.record is not None
    assert result.record.error_message is None
    assert result.raw_state["budget"].files_analyzed == count
    usage = result.usage
    assert usage is not None
    assert usage.failure_count == 0
    assert usage.unknown_token_calls == 0
    assert usage.success_count >= count
    assert usage.attempt_count == usage.success_count


@pytest.mark.asyncio
async def test_model_timeout_is_failed_not_empty_success():
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_AlwaysTimeout(),
        offline=True,
        enable_heuristic_analysis=False,
        extra={"fixture_files": {"app.py": "eval(user_input)\n"}},
    )
    result = await runner.run(_request(), runtime=runtime)
    assert result.status is AuditStatus.FAILED
    assert result.findings == []
    assert result.report is not None
    assert result.report.status is AuditStatus.FAILED
    coverage = result.report.metadata["coverage"]
    assert coverage["model_failures"] >= 1
    assert coverage["model_successes"] == 0
    assert coverage["unknown_token_calls"] >= 1
    assert coverage["known_call_count"] == 0
    assert coverage["failed_units"] or coverage["planner_error"]
    assert "failed" in result.report.summary.lower()
    assert result.raw_state["errors"]


@pytest.mark.asyncio
async def test_heuristic_degrades_to_partial_when_model_times_out():
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_AlwaysTimeout(),
        offline=True,
        enable_heuristic_analysis=True,
        extra={"fixture_files": {"app.py": "eval(user_input)\n"}},
    )
    result = await runner.run(_request(), runtime=runtime)
    assert result.status is AuditStatus.PARTIAL
    assert result.findings
    coverage = result.report.metadata["coverage"]
    assert coverage["degraded_units"]
    assert "app.py" in result.report.summary
    assert "partial" in result.report.summary.lower()


class _FailLateFile:
    """Plan and ok.py succeed; the late.py analysis call times out."""

    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        blob = "\n".join(getattr(message, "content", "") for message in messages)
        if '"path"' in blob and "late.py" in blob:
            raise TimeoutError("late timeout")
        if '"path"' in blob and "ok.py" in blob:
            return LLMResponse(
                content=json.dumps(
                    [
                        {
                            "title": "SQL Injection",
                            "description": "concat",
                            "severity": "high",
                            "line": 1,
                        }
                    ]
                ),
                usage=ModelUsage(call_count=1, input_tokens=4, output_tokens=4, total_tokens=8),
            )
        return LLMResponse(
            content="{}",
            usage=ModelUsage(call_count=1, input_tokens=4, output_tokens=4, total_tokens=8),
        )


@pytest.mark.asyncio
async def test_partial_model_failure_keeps_earlier_findings():
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_FailLateFile(),
        offline=True,
        enable_heuristic_analysis=False,
        extra={"fixture_files": {"ok.py": "value = 1\n", "late.py": "value = 2\n"}},
    )
    result = await runner.run(
        _request(budget=RunBudget(max_tokens=50_000, max_model_calls=10, max_files=10)),
        runtime=runtime,
    )
    assert result.status is AuditStatus.PARTIAL
    assert any(f.title == "SQL Injection" for f in result.findings)
    failed = result.report.metadata["coverage"]["failed_units"]
    assert any(unit["path"] == "late.py" for unit in failed)
    assert "late.py" in result.report.summary


@pytest.mark.asyncio
async def test_invalid_model_output_is_structured_failure():
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_ScriptedLLM(["{}", "this is not json"]),
        offline=True,
        enable_heuristic_analysis=False,
        extra={"fixture_files": {"app.py": "value = 1\n"}},
    )
    result = await runner.run(_request(), runtime=runtime)
    assert result.status is AuditStatus.FAILED
    coverage = result.report.metadata["coverage"]
    assert coverage["invalid_outputs"] >= 1
    assert coverage["model_successes"] >= 1
    assert any(unit["reason"] == "invalid_model_output" for unit in coverage["failed_units"])


@pytest.mark.asyncio
async def test_long_file_is_partial_because_model_preview_is_truncated():
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_ScriptedLLM(["{}", "{}"]),
        offline=True,
        enable_heuristic_analysis=False,
        extra={"fixture_files": {"big.py": "x = 1\n" * 3000}},
    )
    result = await runner.run(_request(), runtime=runtime)
    assert result.status is AuditStatus.PARTIAL
    truncated = result.report.metadata["coverage"]["truncated_units"]
    assert any(unit["path"] == "big.py" for unit in truncated)
    assert "big.py" in result.report.summary


@pytest.mark.asyncio
async def test_file_budget_stops_as_partial_and_names_skipped_units():
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_ScriptedLLM(["{}"] * 8),
        offline=True,
        enable_heuristic_analysis=False,
        extra={"fixture_files": _files(5)},
    )
    result = await runner.run(
        _request(budget=RunBudget(max_tokens=50_000, max_model_calls=20, max_files=2)),
        runtime=runtime,
    )
    assert result.status is AuditStatus.PARTIAL
    assert result.raw_state["budget"].files_analyzed == 2
    assert result.raw_state["budget"].is_exhausted()
    # The file cap drops paths before planning; the report still names them.
    omitted = result.report.metadata["coverage"]["omitted_units"]
    assert len(omitted) == 3
    assert all(unit["reason"] == "file_budget" for unit in omitted)
    assert "f4.py" in result.report.summary
    assert "partial" in result.report.summary.lower()


@pytest.mark.asyncio
async def test_model_call_budget_names_units_left_in_the_queue():
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_ScriptedLLM(["{}"] * 8),
        offline=True,
        enable_heuristic_analysis=False,
        extra={"fixture_files": _files(5)},
    )
    # Plan uses one call, then two files; the router stops with three still queued.
    result = await runner.run(
        _request(budget=RunBudget(max_tokens=50_000, max_model_calls=3, max_files=20)),
        runtime=runtime,
    )
    assert result.status is AuditStatus.PARTIAL
    assert result.raw_state["budget"].files_analyzed == 2
    skipped = result.report.metadata["coverage"]["skipped_units"]
    assert len(skipped) == 3
    assert {unit["path"] for unit in skipped} == {"f2.py", "f3.py", "f4.py"}


@pytest.mark.asyncio
async def test_graph_exception_keeps_findings_already_produced(monkeypatch):
    import app.services.agent.graph.nodes as nodes

    original = nodes.analyze_file
    seen = {"n": 0}

    async def _boom(state, config=None):  # type: ignore[no-untyped-def]
        seen["n"] += 1
        if seen["n"] >= 2:
            raise RuntimeError("boom-after-progress")
        return await original(state, config)

    monkeypatch.setattr(nodes, "analyze_file", _boom)
    runner = AuditRunner(store=InMemoryBusinessStore())
    runtime = GraphRuntime(
        llm=_ScriptedLLM(["{}"] * 4),
        offline=True,
        enable_heuristic_analysis=True,
        extra={
            "fixture_files": {
                "a.py": "eval(user_input)\n",
                "b.py": "eval(user_input)\n",
            }
        },
    )
    result = await runner.run(
        _request(budget=RunBudget(max_tokens=50_000, max_model_calls=10, max_files=10)),
        runtime=runtime,
    )
    assert result.status is AuditStatus.FAILED
    assert result.record is not None
    assert result.record.error_message is not None
    assert "boom-after-progress" in result.record.error_message
    assert result.findings
    assert any(
        "eval" in (f.description or "").lower() or "Injection" in f.title for f in result.findings
    )
