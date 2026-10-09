#!/usr/bin/env python3
"""Local stand-in for the Anthropic API, used only to qualify the forecast sessions.

It records every request Claude Code sends (headers minus credentials, full JSON body) and answers
/v1/messages with a canned forecast, so the exact request a forecast session would make can be
inspected without real credentials or quota.

    python3 fake_anthropic.py <capture_dir> [port=0] [--fail-first N] [--delay SECONDS] [--thinking]

Prints the bound port on stdout. --fail-first answers the first N message requests with HTTP 529
to observe Claude Code's own retry behavior. --thinking puts a signed thinking block with no visible
text before the answer, the way Opus 5.5 replied in the real run. Each answer's signature and request-id
are unique to its request.
"""
import argparse
import itertools
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SECRET_HEADERS = {'authorization', 'x-api-key', 'cookie', 'proxy-authorization'}
REPLY = 'minutes = 42'
counter = itertools.count(1)
lock = threading.Lock()
failures_left = 0


def sse(event, data):
    return f'event: {event}\ndata: {json.dumps(data)}\n\n'.encode()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _record(self, body):
        n = next(counter)
        headers = {k: ('<redacted>' if k.lower() in SECRET_HEADERS else v) for k, v in self.headers.items()}
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = {'_unparsed': body.decode(errors='replace')}
        record = {'n': n, 'at': time.time(), 'method': self.command, 'path': self.path,
                  'headers': headers, 'body': parsed}
        (self.server.capture_dir / f'{n:03d}.json').write_text(json.dumps(record, indent=2) + '\n')
        self.request_id = f'req_fake_{n:03d}'
        return parsed

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('request-id', self.request_id)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._record(b'')
        self._send(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'fake server'}})

    def do_POST(self):
        global failures_left
        body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        parsed = self._record(body) or {}
        if not self.path.startswith('/v1/messages') or 'count_tokens' in self.path:
            if 'count_tokens' in self.path:
                return self._send(200, {'input_tokens': 100})
            return self._send(404, {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'fake'}})
        with lock:
            fail = failures_left > 0
            failures_left -= fail
        if fail:
            return self._send(529, {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}})
        time.sleep(self.server.delay)
        model = parsed.get('model', 'unknown')
        usage = {'input_tokens': 100, 'output_tokens': 5, 'cache_creation_input_tokens': 0,
                 'cache_read_input_tokens': 0}
        if not parsed.get('stream'):
            return self._send(200, {'id': 'msg_fake', 'type': 'message', 'role': 'assistant', 'model': model,
                                    'content': [{'type': 'text', 'text': REPLY}], 'stop_reason': 'end_turn',
                                    'stop_sequence': None, 'usage': usage})
        self.send_response(200)
        self.send_header('request-id', self.request_id)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        start = {'id': 'msg_fake', 'type': 'message', 'role': 'assistant', 'model': model, 'content': [],
                 'stop_reason': None, 'stop_sequence': None, 'usage': {**usage, 'output_tokens': 1}}
        thinking = []
        if self.server.thinking:
            thinking = [sse('content_block_start', {'type': 'content_block_start', 'index': 0,
                                                    'content_block': {'type': 'thinking', 'thinking': '',
                                                                      'signature': ''}}),
                        sse('content_block_delta', {'type': 'content_block_delta', 'index': 0,
                                                    'delta': {'type': 'signature_delta',
                                                              'signature': f'sig_{self.request_id}'}}),
                        sse('content_block_stop', {'type': 'content_block_stop', 'index': 0})]
        text = len(thinking) and 1
        for chunk in (sse('message_start', {'type': 'message_start', 'message': start}),
                      *thinking,
                      sse('content_block_start', {'type': 'content_block_start', 'index': text,
                                                  'content_block': {'type': 'text', 'text': ''}}),
                      sse('content_block_delta', {'type': 'content_block_delta', 'index': text,
                                                  'delta': {'type': 'text_delta', 'text': REPLY}}),
                      sse('content_block_stop', {'type': 'content_block_stop', 'index': text}),
                      sse('message_delta', {'type': 'message_delta',
                                            'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
                                            'usage': {'output_tokens': 5}}),
                      sse('message_stop', {'type': 'message_stop'})):
            self.wfile.write(chunk)
            self.wfile.flush()
        self.close_connection = True


def main():
    global failures_left
    parser = argparse.ArgumentParser()
    parser.add_argument('capture_dir')
    parser.add_argument('port', nargs='?', type=int, default=0)
    parser.add_argument('--fail-first', type=int, default=0)
    parser.add_argument('--delay', type=float, default=0.0)
    parser.add_argument('--thinking', action='store_true')
    args = parser.parse_args()
    failures_left = args.fail_first
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    server.capture_dir = Path(args.capture_dir)
    server.capture_dir.mkdir(parents=True, exist_ok=True)
    server.delay = args.delay
    server.thinking = args.thinking
    print(server.server_address[1], flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
