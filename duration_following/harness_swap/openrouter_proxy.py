"""Local forwarding proxy from a CLI to https://openrouter.ai/api, one per run, started before the timed process:

    PROXY_CLIENT=codex PROXY_CACHE_HINT=1 PROXY_PORT=18790 PROXY_LEDGER=ledger.jsonl python openrouter_proxy.py

codex (base_url http://127.0.0.1:PORT/v1): POST only; JSON bodies are re-serialized and, with PROXY_CACHE_HINT=1
(Fable), get a top-level cache_control hint so that OpenRouter applies Anthropic prompt caching as Claude Code does.
claude-code (ANTHROPIC_BASE_URL=http://127.0.0.1:PORT): bytes pass unchanged. One ledger line per request (path, status,
time to first byte, duration); headers, and so the key, are never logged.
"""
import http.client
import http.server
import json
import os
import ssl
import threading
import time

PORT = int(os.environ.get('PROXY_PORT', '18790'))
LEDGER = os.environ.get('PROXY_LEDGER', 'proxy-ledger.jsonl')
CODEX = os.environ.get('PROXY_CLIENT', 'codex') == 'codex'
CACHE_HINT = os.environ.get('PROXY_CACHE_HINT', '1') != '0'
UPSTREAM = 'openrouter.ai'
HOP = ('host', 'content-length', 'connection', 'accept-encoding')
_lock = threading.Lock()


def codex_body(body, cache_hint):
    """A JSON object body re-serialized, with a cache_control hint if asked and absent; (body, hint added)."""
    try:
        j = json.loads(body)
    except ValueError:
        return body, False
    if not isinstance(j, dict):
        return body, False
    hint = cache_hint and 'cache_control' not in j
    if hint:
        j['cache_control'] = {'type': 'ephemeral'}
    return json.dumps(j).encode(), hint


def ledger(obj):
    with _lock, open(LEDGER, 'a') as f:
        f.write(json.dumps(obj) + '\n')


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def forward(self, method):
        t0 = time.time()
        body = self.rfile.read(int(self.headers.get('content-length', 0) or 0)) if method == 'POST' else None
        entry = {'t_start_ms': int(t0 * 1000), 'method': method, 'path': self.path}
        if CODEX:
            body, entry['cache_hint_injected'] = codex_body(body, CACHE_HINT)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        if body is not None:
            headers['Content-Length'] = str(len(body))
        try:
            conn = http.client.HTTPSConnection(UPSTREAM, 443, timeout=900 if CODEX else 1800,
                                               context=ssl.create_default_context())
            conn.request(method, '/api' + self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as e:
            ledger(dict(entry, dt_ms=int((time.time() - t0) * 1000), proxy_error=repr(e)))
            error = ({'error': {'message': 'proxy upstream error: %r' % (e,)}} if CODEX else
                     {'type': 'error', 'error': {'type': 'api_error', 'message': 'proxy upstream error'}})
            msg = json.dumps(error).encode()
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() not in ('transfer-encoding', 'content-length', 'connection', 'content-encoding'):
                self.send_header(k, v)
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        first = None
        try:
            while chunk := resp.read1(65536):         # stream the response through as it arrives
                first = first or time.time()
                self.wfile.write(b'%x\r\n%s\r\n' % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b'0\r\n\r\n')
            self.wfile.flush()
        except Exception as e:
            entry['stream_error'] = repr(e)
        ledger(dict(entry, http_status=resp.status, ttfb_ms=int((first - t0) * 1000) if first else None,
                    dt_ms=int((time.time() - t0) * 1000)))

    def do_POST(self):
        self.forward('POST')

    def do_GET(self):
        if CODEX:
            self.send_error(501, 'Unsupported method (%r)' % self.command)
        else:
            self.forward('GET')

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
