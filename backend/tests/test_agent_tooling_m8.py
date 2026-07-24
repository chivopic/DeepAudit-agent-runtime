"""M8 governed tool integration tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agent.tooling import (
    FORBIDDEN_TOOL_NAMES,
    BuiltinToolAdapter,
    InMemoryMCPTransport,
    MCPToolAdapter,
    MockToolAdapter,
    ToolInput,
    ToolOutput,
    ToolRegistry,
    default_tool_registry,
)


@pytest.mark.asyncio
async def test_builtin_list_and_read_snippet():
    files = {"src/a.py": "line1\neval(user)\nline3\n"}
    ad = BuiltinToolAdapter(files=files)
    names = {s.name for s in ad.list_tools()}
    assert "list_files" in names
    assert "read_snippet" in names

    listed = await ad.invoke(ToolInput(name="list_files", arguments={"prefix": "src/"}))
    assert listed.success
    assert listed.data == ["src/a.py"]

    snip = await ad.invoke(
        ToolInput(
            name="read_snippet",
            arguments={"path": "src/a.py", "start_line": 2, "end_line": 2},
        )
    )
    assert snip.success
    assert "eval" in snip.data


@pytest.mark.asyncio
async def test_builtin_rejects_path_traversal():
    ad = BuiltinToolAdapter(files={"a.py": "x"})
    out = await ad.invoke(
        ToolInput(name="read_snippet", arguments={"path": "../etc/passwd"})
    )
    assert not out.success
    assert "unsafe" in (out.error or "").lower() or "path" in (out.error or "").lower()

    win = await ad.invoke(
        ToolInput(name="read_snippet", arguments={"path": "C:/Windows/system.ini"})
    )
    assert not win.success


@pytest.mark.asyncio
async def test_forbidden_tool_name_rejected_at_input():
    with pytest.raises(ValidationError):
        ToolInput(name="shell.exec", arguments={"cmd": "id"})
    with pytest.raises(ValidationError):
        ToolInput(name="bash", arguments={})


@pytest.mark.asyncio
async def test_registry_denies_forbidden_and_allowlist():
    reg = ToolRegistry(
        adapters=[BuiltinToolAdapter(files={"a.py": "password = 'x'"})],
        allowlist={"list_files", "echo"},
    )
    # bypass pydantic by constructing after validation? Use mock name that isn't forbidden
    denied = await reg.invoke(ToolInput(name="heuristic_scan", arguments={"path": "a.py"}))
    assert not denied.success
    assert denied.metadata.get("policy_denied") is True

    ok = await reg.invoke(ToolInput(name="list_files", arguments={}))
    assert ok.success


@pytest.mark.asyncio
async def test_mcp_adapter_filters_forbidden_and_allowlist():
    transport = InMemoryMCPTransport(
        tools=[
            {"name": "safe_scan", "description": "ok"},
            {"name": "shell.exec", "description": "bad"},
            {"name": "other", "description": "not allowed"},
        ],
        handlers={
            "safe_scan": lambda a: {"hits": 1},
            "shell.exec": lambda a: "should never run",
            "other": lambda a: "nope",
        },
    )
    ad = MCPToolAdapter(transport, allowlist={"safe_scan"})
    specs = await ad.alist_tools()
    names = {s.name for s in specs}
    assert "safe_scan" in names
    assert "shell.exec" not in names
    assert "other" not in names

    out = await ad.invoke(ToolInput(name="safe_scan", arguments={}))
    assert out.success
    assert out.data == {"hits": 1}

    blocked = await ad.invoke(ToolInput(name="other", arguments={}))
    assert not blocked.success
    assert blocked.metadata.get("policy_denied") is True


@pytest.mark.asyncio
async def test_mock_adapter_and_registry_routing():
    mock = MockToolAdapter(responses={"ping": "pong"})
    reg = default_tool_registry(files={"f.py": "x"}, mock=mock)
    out = await reg.invoke(ToolInput(name="ping", arguments={}, audit_id="aud_1"))
    assert out.success
    assert out.data == "pong"
    assert mock.calls[0].name == "ping"
    assert reg.audit_log
    assert reg.audit_log[-1]["tool"] == "ping"
    assert "arg_keys" in reg.audit_log[-1]
    # never store full payloads
    assert "arguments" not in reg.audit_log[-1]


@pytest.mark.asyncio
async def test_heuristic_scan_and_echo():
    reg = default_tool_registry(files={"b.py": "os.system(x)\n"})
    h = await reg.invoke(ToolInput(name="heuristic_scan", arguments={"path": "b.py"}))
    assert h.success
    assert "os.system" in h.data["hits"]
    e = await reg.invoke(ToolInput(name="echo", arguments={"message": "hi"}))
    assert e.data == "hi"


def test_forbidden_set_covers_shell_family():
    assert "shell.exec" in FORBIDDEN_TOOL_NAMES
    assert "filesystem.write_anywhere" in FORBIDDEN_TOOL_NAMES


def test_tool_output_to_llm_string_truncates():
    long = "x" * 5000
    out = ToolOutput(name="t", success=True, data=long)
    s = out.to_llm_string(max_length=100)
    assert len(s) < 200
    assert "truncated" in s
