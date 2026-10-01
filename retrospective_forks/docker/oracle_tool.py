#!/usr/bin/env python3
"""Stdio MCP server with one tool, elapsed_seconds, returning the parent run's recorded runtime in seconds (env
ELAPSED_SECONDS). Used by R-oracle (installed in the agenttime-retro:1 image as /opt/retro/oracle_tool.py) and by the
elapsed-oracle arm of ../../preliminary/programbench_separate_turn (linked there). Both studies ran the same file."""
import json
import os
import sys
from decimal import Decimal, InvalidOperation

# serverInfo.name, renamed from the run's internal name; the model never saw it (tools take the config key "elapsed").
SERVER_NAME = "agenttime-elapsed-oracle"
SERVER_VERSION = "1"
TOOL_NAME = "elapsed_seconds"
PROTOCOL_VERSION = "2024-11-05"
TOOL = {"name": TOOL_NAME,
        "description": "Return the controller-recorded elapsed seconds for the completed parent execution.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}


def canonical_elapsed_seconds(raw):
    """Plain decimal, no exponent, no trailing zeros ('5219.4926077360005', '0')."""
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("elapsed seconds must be a finite non-negative number") from exc
    if not value.is_finite() or value < 0:
        raise ValueError("elapsed seconds must be a finite non-negative number")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_request(request, elapsed_seconds):
    request_id, method = request.get("id"), request.get("method")
    if method == "initialize":
        params = request.get("params")
        version = params.get("protocolVersion") if isinstance(params, dict) else None
        return _result(request_id, {"protocolVersion": version if isinstance(version, str) and version else PROTOCOL_VERSION,
                                    "capabilities": {"tools": {}},
                                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}})
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [TOOL]})
    if method == "tools/call":
        params = request.get("params")
        if (params.get("name") if isinstance(params, dict) else None) != TOOL_NAME:
            return _error(request_id, -32602, "unknown tool")
        return _result(request_id, {"content": [{"type": "text", "text": f"ELAPSED_SECONDS={elapsed_seconds}"}],
                                    "structuredContent": {"elapsed_seconds": elapsed_seconds,
                                                          "source": "controller_monotonic"},
                                    "isError": False})
    if request_id is None:
        return None
    return _error(request_id, -32601, "method not found")


def serve(elapsed_seconds):
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = handle_request(request, elapsed_seconds)
        except ValueError as exc:  # includes json.JSONDecodeError
            response = _error(None, -32700, str(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


def main():
    try:
        raw = os.environ.get("ELAPSED_SECONDS")
        if raw is None:
            raise ValueError("elapsed seconds are required via ELAPSED_SECONDS")
        elapsed = canonical_elapsed_seconds(raw)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return serve(elapsed)


if __name__ == "__main__":
    raise SystemExit(main())
