#!/usr/bin/env python3
"""Proxy between Codex and OpenRouter, run inside the fork container by run.sh (fork_codex.py).

Each /responses body goes through rewrite(): `model` becomes the OpenRouter slug (Codex keeps the parent's own model
name), the provider is pinned (`provider: {order: [P], allow_fallbacks: false}`, which Codex's config cannot express)
and, for R-context-only, `tools`, `tool_choice`, `parallel_tool_calls` and every `additional_tools` input item go, so
the model is offered no tool at all. The proxy adds the OpenRouter key (Codex and any shell the model starts never
have it) and logs the tools offered and the HTTP status of each request, never headers or content.

Env: PROXY_PORT, PROXY_LOG (jsonl path), PROXY_PROVIDER, PROXY_MODEL, PROXY_STRIP_TOOLS=1, OPENROUTER_API_KEY.
"""
import json
import os
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "https://openrouter.ai/api/v1"
LOG = os.environ.get("PROXY_LOG", "/tmp/proxy.jsonl")
PROVIDER = os.environ.get("PROXY_PROVIDER") or None
MODEL = os.environ.get("PROXY_MODEL") or None
STRIP_TOOLS = os.environ.get("PROXY_STRIP_TOOLS") == "1"
_KEY = os.environ.pop("OPENROUTER_API_KEY", "") or None
HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding", "keep-alive", "authorization"}
_lock = threading.Lock()


def rewrite(body, model=None, provider=None, strip_tools=False):
    """The /responses body as sent upstream (edited in place)."""
    if model:
        body["model"] = model
    if provider and "provider" not in body:
        body["provider"] = {"order": [provider], "allow_fallbacks": False}
    if strip_tools:
        body["input"] = [it for it in body.get("input") or []
                         if not (isinstance(it, dict) and it.get("type") == "additional_tools")]
        for key in ("tools", "tool_choice", "parallel_tool_calls"):
            body.pop(key, None)
    return body


def tool_names(tools):
    out = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        if t.get("type") == "function":
            out.append(t.get("name") or (t.get("function") or {}).get("name"))
        elif t.get("type") == "namespace":
            out.append({t.get("name"): tool_names(t.get("tools"))})
        else:
            out.append(t.get("name") or t.get("type"))
    return out


def offered(body):
    """Names of the tools a /responses body offers the model (additional_tools input items, then `tools`)."""
    items = body.get("input") if isinstance(body.get("input"), list) else []
    return [tool_names(it.get("tools")) for it in items
            if isinstance(it, dict) and it.get("type") == "additional_tools"] + [tool_names(body.get("tools"))]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        rec = {"method": method, "path": self.path}
        body = None
        if raw and not self.headers.get("Content-Encoding"):
            try:
                body = json.loads(raw)
            except ValueError:
                body = None
        if isinstance(body, dict) and self.path.rstrip("/").endswith("/responses"):
            body = rewrite(body, MODEL, PROVIDER, STRIP_TOOLS)
            rec["offered"] = offered(body)
            raw = json.dumps(body).encode()
        path = self.path
        for prefix in ("/api/v1", "/v1"):          # Codex base_url is http://127.0.0.1:PORT/api/v1
            if path.startswith(prefix + "/"):
                path = path[len(prefix):]
                break
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        if _KEY:
            headers["Authorization"] = "Bearer " + _KEY
        req = urllib.request.Request(UPSTREAM + path, data=raw or None, method=method, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=900)
            status = resp.status
        except urllib.error.HTTPError as e:
            resp, status = e, e.code
        except Exception as e:  # network failure
            msg = json.dumps({"error": {"message": f"proxy upstream failure: {e!r}"[:500]}}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        self.send_response(status)
        self.send_header("Content-Type", resp.headers.get("Content-Type", "application/octet-stream"))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            while True:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True
        with _lock, open(LOG, "a") as f:
            f.write(json.dumps({**rec, "status": status}) + "\n")

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        self._proxy("GET")


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PROXY_PORT", "18080"))), Handler).serve_forever()
