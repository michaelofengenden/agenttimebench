"""The callable clock for GPQA and HLE in Claude Code: a stdio MCP server, agenttime_clock.

Its one tool, current_time (seen by Claude as mcp__agenttime_clock__current_time),
returns {"utc": "YYYY-MM-DDTHH:MM:SS.ffffffZ"}. It speaks newline-delimited
JSON-RPC 2.0 and does nothing else. Standard library only; installed in the
container as clock_tool.py and run as `python3 -I -B clock_tool.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from typing import Any

SERVER_NAME = "agenttime_clock"
TOOL_NAME = "current_time"
TOOL_SCHEMA = {
    "name": TOOL_NAME,
    "description": "Return the current UTC time.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def mcp_config(server_path: str) -> dict[str, Any]:
    """The --mcp-config file given to Claude Code."""
    return {"mcpServers": {SERVER_NAME: {
        "type": "stdio", "command": "/usr/local/bin/python3",
        "args": ["-I", "-B", server_path]}}}


def current_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _reply(request: Any) -> dict[str, Any] | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32600, "message": "Invalid Request"}}
    request_id = request.get("id")
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        params = request.get("params")
        protocol_version = (params.get("protocolVersion", "2025-06-18")
                            if isinstance(params, dict) else "2025-06-18")
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
        }}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"tools": [TOOL_SCHEMA]}}
    if method == "tools/call":
        params = request.get("params")
        if (not isinstance(params, dict) or params.get("name") != TOOL_NAME
                or params.get("arguments", {}) != {}):
            return {"jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32602, "message": "Only current_time with no arguments is available"}}
        return {"jsonrpc": "2.0", "id": request_id, "result": {
            "content": [{"type": "text", "text": json.dumps(
                {"utc": current_utc()}, separators=(",", ":"))}], "isError": False}}
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": "Method not found"}}


def serve(stdin=None, stdout=None) -> None:
    """Serve newline-delimited MCP JSON-RPC on stdio without other I/O."""
    source = stdin or sys.stdin.buffer
    sink = stdout or sys.stdout.buffer
    for raw in source:
        try:
            response = _reply(json.loads(raw))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}
        if response is not None:
            sink.write(json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n")
            sink.flush()


if __name__ == "__main__":
    serve()
