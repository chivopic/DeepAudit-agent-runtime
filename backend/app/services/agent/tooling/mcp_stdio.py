"""Minimal stdio JSON-RPC transport for MCP-style tool servers.

The command is operator configuration, never a string from the model.
The process is started with ``shell=False``. Tests can point it at a fixed
local script. This is not the full MCP SDK.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


class StdioMCPTransport:
    """Speak ``tools/list`` and ``tools/call`` over stdin/stdout JSON lines."""

    def __init__(self, command: list[str], *, cwd: str | None = None) -> None:
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("MCP command must be a non-empty argv list")
        self.command = list(command)
        self.cwd = cwd
        self._process: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def start(self) -> None:
        if self._process is not None:
            return
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=self.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            process.kill()
            await process.wait()

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self._request("tools/list", {})
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            return []
        return [item for item in tools if isinstance(item, dict)]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if isinstance(result, dict):
            return result
        return {"success": False, "error": "mcp result was not an object"}

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        await self.start()
        process = self._process
        if process is None or process.stdin is None or process.stdout is None:
            raise RuntimeError("MCP process is not running")
        request_id = self._next_id
        self._next_id += 1
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        process.stdin.write((json.dumps(body) + "\n").encode("utf-8"))
        await process.stdin.drain()
        line = await asyncio.wait_for(process.stdout.readline(), timeout=10)
        if not line:
            raise RuntimeError("MCP process closed stdout")
        message = json.loads(line.decode("utf-8"))
        if message.get("error"):
            raise RuntimeError(str(message["error"]))
        return message.get("result")
