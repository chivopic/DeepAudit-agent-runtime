"""R2–R5 product path: gateway, resume, verification, and eval failure."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services.agent.application.activity_log import (
    finding_log_lines,
    user_node_message,
    user_outcome_message,
)
from app.services.agent.application.assembly import assemble_audit_runtime
from app.services.agent.application.product_audit import (
    _gateway_from_user_config,
    run_product_graph_audit,
)
from app.services.agent.application.project_source import (
    ProjectSourceError,
    load_authorized_snapshot,
)
from app.services.agent.application.runner import AuditRunner
from app.services.agent.domain import (
    AuditRequest,
    AuditStatus,
    ModelUsage,
    RepositoryRef,
    RunBudget,
)
from app.services.agent.graph.gateway import LLMServiceGateway
from app.services.agent.graph.llm import FakeLLM, LLMMessage, LLMResponse
from app.services.agent.graph.runtime import GraphRuntime
from app.services.agent.harness import AgentSpec, ModelRouter
from app.services.agent.observability import (
    Tracer,
    get_tracer,
    push_tracer,
    reset_tracer_context,
    set_tracer,
)
from app.services.agent.persistence.checkpointer import create_checkpointer
from app.services.agent.persistence.control import FileControlPlane, LeaseBusy
from app.services.agent.persistence.file_checkpointer import FileCheckpointSaver
from app.services.agent.persistence.sqlite_store import SqliteBusinessStore
from app.services.agent.tooling.mcp_stdio import StdioMCPTransport


def _request(**kwargs) -> AuditRequest:
    budget = kwargs.pop("budget", RunBudget(max_tokens=50_000, max_model_calls=40, max_files=10))
    repository = kwargs.pop(
        "repository",
        RepositoryRef(source_type="local", local_path="fixture://product"),
    )
    return AuditRequest(repository=repository, budget=budget, **kwargs)


class _Service:
    def __init__(self) -> None:
        self.api_key = "sk-test-secret"

    async def chat_completion_raw(self, messages, temperature=0.0, max_tokens=None):  # type: ignore[no-untyped-def]
        return {
            "content": "[]",
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
            "api_key": self.api_key,
        }


class _PathLLM:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        text = messages[-1].content
        self.payloads.append(text)
        if "languages" in text:
            body = '{"ok": true}'
        else:
            body = "[]"
        return LLMResponse(
            content=body,
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2, call_count=1),
        )


def _analyzed_paths(payloads: list[str]) -> list[str]:
    found: list[str] = []
    for text in payloads:
        if '"content"' not in text:
            continue
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            continue
        path = body.get("path")
        if isinstance(path, str):
            found.append(path)
    return found


@pytest.mark.asyncio
async def test_gateway_maps_usage_and_hides_the_key() -> None:
    gateway = LLMServiceGateway(_Service(), provider="openai", model="demo")
    response = await gateway.complete([LLMMessage(role="user", content="hi")])
    assert response.usage.input_tokens == 3
    assert response.usage.output_tokens == 4
    assert response.usage.total_tokens == 7
    assert response.raw == {"has_usage": True}
    assert "sk-test-secret" not in json.dumps(response.raw)


def test_model_router_refuses_a_live_model_without_a_gateway() -> None:
    router = ModelRouter(default=FakeLLM(), production=None)
    spec = AgentSpec(offline=False, provider="openai", model="gpt")
    with pytest.raises(RuntimeError, match="no gateway"):
        router.resolve(spec)


def test_snapshot_stays_inside_the_project(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "hidden.py").write_text("secret = 1\n", encoding="utf-8")
    files = load_authorized_snapshot(root)
    assert files == {"app/a.py": "x = 1\n"}
    with pytest.raises(ProjectSourceError):
        load_authorized_snapshot(root, target_files=["../outside.py"])
    with pytest.raises(ProjectSourceError):
        load_authorized_snapshot(tmp_path / "missing")


@pytest.mark.asyncio
async def test_context_windows_and_location_checks() -> None:
    long_body = "\n".join(f"line_{i} = {i}" for i in range(1, 400))
    llm = _PathLLM()
    runtime = GraphRuntime(
        llm=llm,
        offline=True,
        extra={
            "fixture_files": {"big.py": long_body},
            "context_windows": True,
            "validate_findings": True,
        },
    )
    runner = AuditRunner()
    result = await runner.run(
        _request(budget=RunBudget(max_tokens=50_000, max_model_calls=20, max_files=5)),
        runtime=runtime,
    )
    windows = (result.report.metadata or {})["coverage"]["source_windows"]
    assert len(windows) > 1
    assert windows[0]["path"] == "big.py"

    class _BadLine(_PathLLM):
        async def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            text = messages[-1].content
            self.payloads.append(text)
            if "languages" in text:
                body = '{"ok": true}'
            else:
                body = '[{"title":"ghost","description":"no","severity":"high","line":99999}]'
            return LLMResponse(
                content=body,
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2, call_count=1),
            )

    bad_llm = _BadLine()
    checked = await AuditRunner().run(
        _request(),
        runtime=GraphRuntime(
            llm=bad_llm,
            offline=True,
            extra={
                "fixture_files": {"ok.py": "x = 1\n"},
                "validate_findings": True,
            },
        ),
    )
    assert checked.findings == []
    rejected = (checked.report.metadata or {})["coverage"]["rejected_findings"]
    assert rejected and rejected[0]["reason"] == "line_out_of_range"


@pytest.mark.asyncio
async def test_two_processes_resume_without_reanalyzing(tmp_path: Path) -> None:
    files = {"a.py": "a = 1\n", "b.py": "b = 2\n"}
    ckpt = tmp_path / "audit.pkl"
    store_path = tmp_path / "audits.sqlite3"
    first_llm = _PathLLM()
    first = AuditRunner(
        store=SqliteBusinessStore(store_path),
        checkpointer=FileCheckpointSaver(ckpt),
    )
    request = _request(id="resume-1")

    def stop_when(values: dict) -> bool:
        budget = values.get("budget")
        analyzed = int(getattr(budget, "files_analyzed", 0) or 0)
        pending = list(values.get("pending_task_ids") or [])
        return analyzed >= 1 and len(pending) >= 1

    paused = await first.run(
        request,
        runtime=GraphRuntime(llm=first_llm, offline=True, extra={"fixture_files": files}),
        stop_when=stop_when,
    )
    assert paused.status is AuditStatus.PAUSED
    first_paths = _analyzed_paths(first_llm.payloads)
    assert first_paths == ["a.py"]

    second_llm = _PathLLM()
    second = AuditRunner(
        store=SqliteBusinessStore(store_path),
        checkpointer=FileCheckpointSaver(ckpt),
    )
    continued = await second.resume(
        "resume-1",
        runtime=GraphRuntime(llm=second_llm, offline=True, extra={"fixture_files": files}),
    )
    assert continued.status in {AuditStatus.COMPLETED, AuditStatus.PARTIAL}
    assert _analyzed_paths(second_llm.payloads) == ["b.py"]
    assert len(continued.findings) >= 0


def test_lease_blocks_a_second_owner(tmp_path: Path) -> None:
    plane = FileControlPlane(tmp_path)
    plane.acquire("audit-1", "worker-a")
    with pytest.raises(LeaseBusy):
        plane.acquire("audit-1", "worker-b")
    plane.append_event("audit-1", {"kind": "node", "message": "plan"})
    plane.append_event("audit-1", {"kind": "node", "message": "analyze"})
    replay = plane.read_events("audit-1", after_sequence=1)
    assert [item["message"] for item in replay] == ["analyze"]
    plane.release("audit-1", "worker-a")
    plane.acquire("audit-1", "worker-b")


def test_postgres_checkpointer_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="postgres"):
        create_checkpointer("postgres")


def test_assembly_keeps_execution_closed_and_flags_context() -> None:
    runtime = assemble_audit_runtime(files={"a.py": "x = 1\n"}, offline=True)
    assert runtime.spec.enable_model_calls is False
    assert runtime.spec.allow_execution is False
    graph = runtime._build_runtime()
    assert graph.extra["context_windows"] is True
    assert graph.extra["validate_findings"] is True
    assert graph.extra["enable_parallel_analysis"] is False


@pytest.mark.asyncio
async def test_parallel_width_and_cross_file_links() -> None:
    files = {
        "app/a.py": 'q = "SELECT * FROM users WHERE id = " + name\n',
        "app/b.py": 'q = "SELECT * FROM accounts WHERE id = " + name\n',
    }
    result = await AuditRunner().run(
        _request(),
        runtime=GraphRuntime(
            llm=_PathLLM(),
            offline=True,
            extra={
                "fixture_files": files,
                "enable_parallel_analysis": True,
                "max_parallel_analyzers": 2,
                "cross_file": True,
            },
        ),
    )
    assert result.report.plan is not None
    assert result.report.plan.max_parallel == 2
    linked = [item for item in result.findings if (item.metadata or {}).get("related_paths")]
    assert linked


@pytest.mark.asyncio
async def test_verification_stays_not_run_until_enabled() -> None:
    files = {"app/run.py": "import os\ndef go(cmd):\n    os.system(cmd)\n"}
    plain = await AuditRunner().run(
        _request(),
        runtime=GraphRuntime(llm=_PathLLM(), offline=True, extra={"fixture_files": files}),
    )
    assert plain.findings
    assert all(item.verification_status.value == "not_run" for item in plain.findings)

    checked = await AuditRunner().run(
        _request(enable_verification=True),
        runtime=GraphRuntime(llm=_PathLLM(), offline=True, extra={"fixture_files": files}),
    )
    assert any(item.verification_status.value == "confirmed" for item in checked.findings)


@pytest.mark.asyncio
async def test_mcp_stdio_lists_and_calls_a_local_script(tmp_path: Path) -> None:
    script = tmp_path / "mcp_server.py"
    script.write_text(
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    msg=json.loads(line)\n"
        "    method=msg.get('method')\n"
        "    result={'tools':[{'name':'ping'}]} if method=='tools/list' else {'ok':True}\n"
        "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result})+'\\n')\n"
        "    sys.stdout.flush()\n",
        encoding="utf-8",
    )
    transport = StdioMCPTransport([sys_executable(), str(script)])
    try:
        tools = await transport.list_tools()
        assert tools[0]["name"] == "ping"
        called = await transport.call_tool("ping", {})
        assert called["ok"] is True
    finally:
        await transport.close()


def sys_executable() -> str:
    import sys

    return sys.executable


@pytest.mark.asyncio
async def test_tracer_context_does_not_cross_tasks() -> None:
    set_tracer(Tracer())
    left = Tracer()
    right = Tracer()
    seen: dict[str, Tracer] = {}

    async def run(name: str, tracer: Tracer) -> None:
        token = push_tracer(tracer)
        await asyncio.sleep(0.01)
        seen[name] = get_tracer()
        reset_tracer_context(token)

    await asyncio.gather(run("left", left), run("right", right))
    assert seen["left"] is left
    assert seen["right"] is right
    assert get_tracer() is not left


def test_missing_key_note_does_not_build_a_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    class Config:
        api_key = ""
        provider = type("Provider", (), {"value": "openai"})()
        model = "demo"
        timeout = 30

    class Service:
        def __init__(self, user_config=None) -> None:  # type: ignore[no-untyped-def]
            self.config = Config()

    monkeypatch.setattr("app.services.llm.service.LLMService", Service)
    gateway, note = _gateway_from_user_config({})
    assert gateway is None
    assert note is not None
    assert "模型密钥" in note


@pytest.mark.asyncio
async def test_product_audit_without_a_key_is_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "run.py").write_text(
        "import os\ndef go(cmd):\n    os.system(cmd)\n",
        encoding="utf-8",
    )

    class Config:
        api_key = ""
        provider = type("Provider", (), {"value": "openai"})()
        model = "demo"
        timeout = 30

    class Service:
        def __init__(self, user_config=None) -> None:  # type: ignore[no-untyped-def]
            self.config = Config()

    class Sink:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def emit_info(self, message: str, metadata: dict | None = None) -> None:
            self.messages.append(message)

        async def emit_progress(self, current: int, total: int, message: str | None = None) -> None:
            if message:
                self.messages.append(message)

    sink = Sink()
    monkeypatch.setattr("app.services.llm.service.LLMService", Service)
    summary = await run_product_graph_audit(
        task_id="product-1",
        project_root=root,
        user_config={},
        state_dir=tmp_path / "state",
        max_files=10,
        token_budget=10_000,
        event_sink=sink,
    )
    assert summary["task_status"] == "partial"
    assert summary["findings"]
    assert "sk-" not in json.dumps(summary)
    log = "\n".join(sink.messages)
    assert "LangGraph" not in log
    assert "Tool hit" not in log
    assert "分析进度:" in log
    assert "调用系统命令" in log
    outcome = summary["user_message"]
    assert "个漏洞" not in outcome
    assert "审计完成" not in outcome
    assert "0.0" not in outcome
    assert "没有调用模型" in outcome or "没有配置模型" in outcome
    assert "沙箱" in outcome
    assert summary["duration_ms"] >= 0


def test_activity_log_speaks_to_a_user() -> None:
    assert user_node_message("not_a_node") is None
    assert user_node_message("ingest_repository") == "已读入项目里的源代码"
    progress = user_node_message(
        "analyze_file",
        {"budget": {"files_analyzed": 2}, "manifest": {"files": [{}, {}, {}, {}, {}]}},
    )
    assert progress == "分析进度: 2/5 个文件"
    assert (
        user_node_message(
            "analyze_file",
            {"budget": {"files_analyzed": 2}, "manifest": {"files": [{}, {}, {}]}},
            file_total=15,
        )
        == "分析进度: 2/3 个文件"
    )
    assert user_node_message("build_manifest", file_total=15) == "已列出 15 个要审计的文件"

    lines = finding_log_lines(
        [
            {
                "title": "Tool hit: innerHTML",
                "file_path": "cloud.js",
                "line_start": 1,
                "analyzer": "tool:heuristic_scan",
                "severity": "medium",
                "rule_id": "tool:innerHTML",
            },
            {
                "title": "DOM XSS sink",
                "file_path": "cloud.js",
                "line_start": 12,
                "analyzer": "heuristic",
                "severity": "medium",
                "rule_id": "heur:innerHTML",
            },
            {
                "title": "CSS load order may cause unintended style overrides",
                "file_path": "main.js",
                "line_start": 4,
                "analyzer": "llm",
                "severity": "low",
            },
        ]
    )
    text = "\n".join(str(item["message"]) for item in lines)
    assert "Tool hit" not in text
    assert "第 1 行" not in text
    assert "cloud.js 第 12 行：把内容直接写进页面（中）" in text
    assert "模型意见 · main.js 第 4 行" in text
    assert "个漏洞" not in text
    assert "还不是已经确认的漏洞" in text

    outcome = user_outcome_message(
        status="partial",
        files_analyzed=7,
        files_total=15,
        findings=[
            {
                "analyzer": "tool:heuristic_scan",
                "title": "Tool hit: innerHTML",
                "severity": "medium",
            },
            {"analyzer": "llm", "title": "something", "severity": "high"},
        ],
        duration_ms=312000,
        coverage={
            "skipped_units": [{"reason": "budget_exhausted"}],
            "budget_exhausted": True,
        },
        verification_enabled=False,
        verified_count=0,
    )
    assert "这次没有看完" in outcome
    assert "7/15" in outcome
    assert "5 分 12 秒" in outcome
    assert "个漏洞" not in outcome
    assert "已经确认的漏洞" in outcome
    assert "审计完成" not in outcome
    assert "0.0" not in outcome
    assert "预算" in outcome
    assert "沙箱" in outcome
    assert "1 条是模型意见" in outcome
    assert "1 条是模式扫描线索" in outcome


def test_eval_failure_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.evals import runner as eval_runner
    from tests.evals.evaluators import EvalCase

    bad = EvalCase(
        id="forced-fail",
        language="python",
        category="vulnerable",
        files={"a.py": "x = 1\n"},
        expected_min_findings=99,
    )
    monkeypatch.setattr(eval_runner, "CI_CASES", [bad])
    monkeypatch.setattr("sys.argv", ["runner", "--ci"])
    suite = asyncio.run(eval_runner.run_suite(ci_only=True))
    assert suite.passed is False
    assert eval_runner.main() == 1
