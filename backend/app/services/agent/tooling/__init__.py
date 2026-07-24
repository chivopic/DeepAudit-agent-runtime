"""Governed tool integration (M8).

Nodes depend on ``ToolProtocol`` only — never import MCP SDK or raw shell tools.

Adapters:
- BuiltinToolAdapter: local allowlisted functions (read snippet, list paths, heuristic scan)
- MCPToolAdapter: thin client over an MCP-like transport (mockable)
- MockToolAdapter: scripted responses for tests
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Hard denylist — never register / invoke these via governed tools
FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "shell.exec",
        "shell",
        "bash",
        "exec",
        "filesystem.write_anywhere",
        "write_anywhere",
        "run_code",
        "docker.exec",
    }
)


class ToolAuthzError(PermissionError):
    """Tool denied by policy / authz."""


class ToolInput(BaseModel):
    """Normalized tool invocation input."""

    name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    audit_id: Optional[str] = None
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("tool name must not be blank")
        if v in FORBIDDEN_TOOL_NAMES or v.lower() in FORBIDDEN_TOOL_NAMES:
            raise ValueError(f"forbidden tool: {v}")
        return v


class ToolOutput(BaseModel):
    """Normalized tool result."""

    name: str
    success: bool
    data: Any = None
    error: Optional[str] = None
    duration_ms: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_llm_string(self, max_length: int = 4000) -> str:
        if not self.success:
            return f"Error ({self.name}): {self.error}"
        text = self.data if isinstance(self.data, str) else str(self.data)
        if len(text) > max_length:
            return text[:max_length] + f"\n… truncated ({len(text)} chars)"
        return text


@dataclass
class ToolSpec:
    """Catalog entry for a tool."""

    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    requires_authz: bool = True
    tags: list[str] = field(default_factory=list)


@runtime_checkable
class ToolProtocol(Protocol):
    """What graph nodes may call."""

    async def invoke(self, request: ToolInput) -> ToolOutput: ...

    def list_tools(self) -> list[ToolSpec]: ...


# ---------------------------------------------------------------------------
# Builtin adapter
# ---------------------------------------------------------------------------

BuiltinHandler = Callable[[dict[str, Any]], Awaitable[Any] | Any]


class BuiltinToolAdapter:
    """Local allowlisted tools (no network, no shell)."""

    def __init__(
        self,
        *,
        files: Optional[dict[str, str]] = None,
        allowed: Optional[set[str]] = None,
    ) -> None:
        self.files = dict(files or {})
        self._handlers: dict[str, BuiltinHandler] = {
            "list_files": self._list_files,
            "read_snippet": self._read_snippet,
            "heuristic_scan": self._heuristic_scan,
            "echo": self._echo,
        }
        if allowed is not None:
            self._handlers = {k: v for k, v in self._handlers.items() if k in allowed}

    def list_tools(self) -> list[ToolSpec]:
        descs = {
            "list_files": "List known workspace file paths",
            "read_snippet": "Read a line range from a known file",
            "heuristic_scan": "Run pattern heuristics on a file",
            "echo": "Echo a message (debug)",
        }
        return [
            ToolSpec(name=n, description=descs.get(n, n), tags=["builtin"])
            for n in sorted(self._handlers)
        ]

    async def invoke(self, request: ToolInput) -> ToolOutput:
        t0 = time.perf_counter()
        if request.name in FORBIDDEN_TOOL_NAMES:
            return ToolOutput(
                name=request.name,
                success=False,
                error="forbidden tool",
                duration_ms=0,
                metadata={"policy_denied": True},
            )
        handler = self._handlers.get(request.name)
        if handler is None:
            return ToolOutput(
                name=request.name,
                success=False,
                error=f"unknown builtin tool: {request.name}",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        try:
            result = handler(request.arguments)
            if asyncio.iscoroutine(result):
                result = await asyncio.wait_for(result, timeout=request.timeout_seconds)
            return ToolOutput(
                name=request.name,
                success=True,
                data=result,
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        except asyncio.TimeoutError:
            return ToolOutput(
                name=request.name,
                success=False,
                error="timeout",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolOutput(
                name=request.name,
                success=False,
                error=str(exc),
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )

    async def _list_files(self, args: dict[str, Any]) -> list[str]:
        prefix = str(args.get("prefix") or "")
        return sorted(p for p in self.files if p.startswith(prefix))

    async def _read_snippet(self, args: dict[str, Any]) -> str:
        path = str(args.get("path") or "").replace("\\", "/")
        if (
            not path
            or path.startswith("/")
            or ".." in path.split("/")
            or (len(path) > 1 and path[1] == ":")
        ):
            raise ToolAuthzError(f"unsafe path: {path}")
        if path not in self.files:
            raise FileNotFoundError(path)
        start = int(args.get("start_line") or 1)
        end = int(args.get("end_line") or start + 40)
        lines = self.files[path].splitlines()
        return "\n".join(lines[max(0, start - 1) : end])

    async def _heuristic_scan(self, args: dict[str, Any]) -> dict[str, Any]:
        path = str(args.get("path") or "").replace("\\", "/")
        if path and (
            path.startswith("/")
            or ".." in path.split("/")
            or (len(path) > 1 and path[1] == ":")
        ):
            raise ToolAuthzError(f"unsafe path: {path}")
        content = self.files.get(path, "")
        needles = ["eval(", "os.system", "innerHTML", "SELECT *", "password =", "verify=False"]
        hits = [n for n in needles if n in content]
        return {"path": path, "hits": hits}

    async def _echo(self, args: dict[str, Any]) -> str:
        return str(args.get("message") or "")


# ---------------------------------------------------------------------------
# MCP adapter (transport-abstracted; no real SDK required)
# ---------------------------------------------------------------------------


@runtime_checkable
class MCPTransport(Protocol):
    """Minimal MCP-like transport: list tools + call tool."""

    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]: ...


class MCPToolAdapter:
    """Adapter over an MCP transport. Never exposes forbidden tool names.

    Discovery is required before invoke: empty discovery + no allowlist → deny.
    Sync ``list_tools`` returns a cached discovery snapshot when available.
    """

    def __init__(
        self,
        transport: MCPTransport,
        *,
        allowlist: Optional[set[str]] = None,
    ) -> None:
        self.transport = transport
        self.allowlist = allowlist
        self._discovered: Optional[set[str]] = None
        self._cached_specs: list[ToolSpec] = []

    def list_tools(self) -> list[ToolSpec]:
        """Return last discovery snapshot (empty until ``alist_tools`` / ``refresh``)."""
        return list(self._cached_specs)

    async def alist_tools(self) -> list[ToolSpec]:
        raw = await self.transport.list_tools()
        out: list[ToolSpec] = []
        names: set[str] = set()
        for item in raw:
            name = str(item.get("name") or "")
            if not name or name in FORBIDDEN_TOOL_NAMES:
                continue
            if self.allowlist is not None and name not in self.allowlist:
                continue
            names.add(name)
            out.append(
                ToolSpec(
                    name=name,
                    description=str(item.get("description") or name),
                    input_schema=dict(item.get("inputSchema") or item.get("input_schema") or {}),
                    tags=["mcp"],
                )
            )
        self._discovered = names
        self._cached_specs = list(out)
        return out

    def _mcp_name_allowed(self, name: str) -> tuple[bool, str]:
        if name in FORBIDDEN_TOOL_NAMES:
            return False, "forbidden tool"
        if self.allowlist is not None and name not in self.allowlist:
            return False, f"tool not in MCP allowlist: {name}"
        # Fail closed: require discovery cache or explicit allowlist membership.
        if self._discovered is not None:
            if name not in self._discovered:
                return False, f"tool not discovered via MCP list_tools: {name}"
            return True, ""
        if self.allowlist is not None and name in self.allowlist:
            # Allowlist alone is an explicit operator grant (still no shell family).
            return True, ""
        return False, "MCP tools not discovered; call alist_tools first or set allowlist"

    async def invoke(self, request: ToolInput) -> ToolOutput:
        t0 = time.perf_counter()
        ok, reason = self._mcp_name_allowed(request.name)
        if not ok:
            return ToolOutput(
                name=request.name,
                success=False,
                error=reason,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                metadata={"policy_denied": True},
            )
        try:
            raw = await asyncio.wait_for(
                self.transport.call_tool(request.name, request.arguments),
                timeout=request.timeout_seconds,
            )
            success = bool(raw.get("success", True))
            return ToolOutput(
                name=request.name,
                success=success,
                data=raw.get("content", raw.get("data")),
                error=raw.get("error"),
                duration_ms=int((time.perf_counter() - t0) * 1000),
                metadata={"mcp": True},
            )
        except asyncio.TimeoutError:
            return ToolOutput(
                name=request.name,
                success=False,
                error="mcp timeout",
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("MCP call failed: %s", exc)
            return ToolOutput(
                name=request.name,
                success=False,
                error=str(exc),
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )


class InMemoryMCPTransport:
    """Test/dev MCP transport with scripted tools."""

    def __init__(
        self,
        tools: Optional[list[dict[str, Any]]] = None,
        handlers: Optional[dict[str, Callable[[dict[str, Any]], Any]]] = None,
    ) -> None:
        self._tools = list(tools or [])
        self._handlers = dict(handlers or {})

    async def list_tools(self) -> list[dict[str, Any]]:
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._handlers:
            return {"success": False, "error": f"unknown mcp tool: {name}"}
        data = self._handlers[name](arguments)
        if asyncio.iscoroutine(data):
            data = await data
        return {"success": True, "content": data}


# ---------------------------------------------------------------------------
# Mock adapter
# ---------------------------------------------------------------------------


class MockToolAdapter:
    """Scripted tool responses for deterministic tests."""

    def __init__(
        self,
        responses: Optional[dict[str, ToolOutput | Any]] = None,
        specs: Optional[list[ToolSpec]] = None,
    ) -> None:
        self._responses = dict(responses or {})
        self._specs = list(specs or [])
        self.calls: list[ToolInput] = []

    def list_tools(self) -> list[ToolSpec]:
        if self._specs:
            return list(self._specs)
        return [
            ToolSpec(name=n, description="mock", tags=["mock"])
            for n in self._responses
        ]

    def set_response(self, name: str, value: ToolOutput | Any) -> None:
        self._responses[name] = value

    async def invoke(self, request: ToolInput) -> ToolOutput:
        self.calls.append(request)
        if request.name in FORBIDDEN_TOOL_NAMES:
            return ToolOutput(
                name=request.name,
                success=False,
                error="forbidden",
                metadata={"policy_denied": True},
            )
        raw = self._responses.get(request.name)
        if raw is None:
            return ToolOutput(
                name=request.name,
                success=False,
                error=f"no mock for {request.name}",
            )
        if isinstance(raw, ToolOutput):
            return raw
        return ToolOutput(name=request.name, success=True, data=raw)


# ---------------------------------------------------------------------------
# Registry / router
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Routes tool calls to the first adapter that knows the tool.

    Also enforces global denylist and optional per-audit allowlist.
    """

    def __init__(
        self,
        adapters: Optional[list[ToolProtocol]] = None,
        *,
        allowlist: Optional[set[str]] = None,
    ) -> None:
        self.adapters: list[ToolProtocol] = list(adapters or [])
        self.allowlist = allowlist
        self.audit_log: list[dict[str, Any]] = []

    def register(self, adapter: ToolProtocol) -> None:
        self.adapters.append(adapter)

    def list_tools(self) -> list[ToolSpec]:
        seen: set[str] = set()
        out: list[ToolSpec] = []
        for ad in self.adapters:
            for spec in ad.list_tools():
                if spec.name in FORBIDDEN_TOOL_NAMES:
                    continue
                if self.allowlist is not None and spec.name not in self.allowlist:
                    continue
                if spec.name in seen:
                    continue
                seen.add(spec.name)
                out.append(spec)
        return out

    def _adapter_knows(self, adapter: ToolProtocol, name: str) -> bool:
        """True only when the adapter's *sync* discovery lists the tool."""
        try:
            return any(s.name == name for s in adapter.list_tools())
        except Exception:  # noqa: BLE001
            return False

    async def invoke(self, request: ToolInput) -> ToolOutput:
        if request.name in FORBIDDEN_TOOL_NAMES:
            out = ToolOutput(
                name=request.name,
                success=False,
                error="forbidden tool",
                metadata={"policy_denied": True},
            )
            self._log(request, out)
            return out
        if self.allowlist is not None and request.name not in self.allowlist:
            out = ToolOutput(
                name=request.name,
                success=False,
                error=f"tool not allowed: {request.name}",
                metadata={"policy_denied": True},
            )
            self._log(request, out)
            return out

        # Route only to adapters that advertise the tool (no blind try-all).
        candidates = [ad for ad in self.adapters if self._adapter_knows(ad, request.name)]
        if not candidates:
            # MCP adapters may need async discovery before list_tools is populated.
            for ad in self.adapters:
                alist = getattr(ad, "alist_tools", None)
                if callable(alist):
                    try:
                        await alist()
                    except Exception:  # noqa: BLE001
                        continue
                    if self._adapter_knows(ad, request.name):
                        candidates.append(ad)
        if not candidates:
            out = ToolOutput(
                name=request.name,
                success=False,
                error=f"no adapter for tool: {request.name}",
                metadata={"policy_denied": True, "undiscovered": True},
            )
            self._log(request, out)
            return out

        last = ToolOutput(
            name=request.name,
            success=False,
            error=f"no adapter for tool: {request.name}",
        )
        for ad in candidates:
            out = await ad.invoke(request)
            last = out
            if out.success:
                self._log(request, out)
                return out
            if out.metadata.get("policy_denied"):
                self._log(request, out)
                return out
            # Skip "unknown" from wrong adapter; try next candidate
            err = out.error or ""
            if err.startswith("unknown builtin") or err.startswith("no mock"):
                continue
            # Non-policy failure from the owning adapter — return it
            self._log(request, out)
            return out
        self._log(request, last)
        return last

    def _log(self, request: ToolInput, out: ToolOutput) -> None:
        # Never log secrets / full payloads
        self.audit_log.append(
            {
                "tool": request.name,
                "audit_id": request.audit_id,
                "success": out.success,
                "duration_ms": out.duration_ms,
                "error": out.error,
                "arg_keys": sorted(request.arguments.keys()),
            }
        )


def default_tool_registry(
    *,
    files: Optional[dict[str, str]] = None,
    mock: Optional[MockToolAdapter] = None,
) -> ToolRegistry:
    adapters: list[ToolProtocol] = [BuiltinToolAdapter(files=files)]
    if mock is not None:
        adapters.insert(0, mock)
    return ToolRegistry(adapters=adapters)
