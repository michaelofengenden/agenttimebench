"""Send one streaming OpenRouter request and timestamp its server-sent events.

All times are client monotonic nanoseconds since dispatch (taken just before the request is
opened); every record in a chunk gets the chunk's arrival time. The endpoint is `first_text`:
the first SSE record whose `choices[0].delta.content` has non-whitespace text. The request is
cut off at `deadline_seconds` (2T); run.py also kills this process at 2T in case a read blocks.
A stream completes only with `finish_reason: stop`, `[DONE]`, a single generation from an
allowed model and provider, and public text. Any other finish (e.g. content_filter) fails it.
Reasoning is excluded in the request and not stored.

run.py starts one process per request:
    OPENROUTER_API_KEY=... python openrouter_capture.py --manifest manifest.json --cell <id> --out attempts
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'
PROVIDERS = {'openai': 'OpenAI', 'anthropic': 'Anthropic'}


class InvalidStream(Exception):
    pass


def _has_tools(value):
    if isinstance(value, dict):
        return any((k in {'tool_calls', 'function_call'} and bool(v)) or _has_tools(v) for k, v in value.items())
    return isinstance(value, list) and any(_has_tools(v) for v in value)


def _no_constants(name):
    raise ValueError(name)  # NaN and Infinity are not JSON


def capture(payload, folder, api_key, deadline_seconds, allowed_models):
    folder = Path(folder)
    expected_provider = PROVIDERS[payload['model'].split('/')[0]]
    wire = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    receipt = {'status': 'running', 'eligible': False, 'reason': None, 'requested_model': payload['model'],
               'observed_models': [], 'observed_providers': [], 'generation_ids': [], 'http_status': None,
               'timings_ns': dict.fromkeys(['headers', 'first_byte', 'first_event', 'first_text',
                                            'last_text', 'terminal', 'finished']),
               'finish_reason': None, 'done_seen': False, 'request_sha256': hashlib.sha256(wire).hexdigest()}
    timings = receipt['timings_ns']
    request = urllib.request.Request(ENDPOINT, data=wire, method='POST', headers={
        'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json', 'Accept': 'text/event-stream'})
    (folder / 'request.json').write_text(json.dumps(payload, ensure_ascii=False) + '\n')
    events = (folder / 'events.jsonl').open('x')

    def record(raw, at):
        try:
            lines = raw.decode('utf-8').splitlines()
        except UnicodeDecodeError:
            raise InvalidStream('malformed_event') from None
        if any(line and not line.startswith((':', 'id:', 'retry:', 'data:', 'event:')) for line in lines):
            raise InvalidStream('malformed_event')
        data = [line[5:].removeprefix(' ') for line in lines if line.startswith('data:')]
        event_types = [line[6:].strip() for line in lines if line.startswith('event:')]
        if not data:
            events.write(json.dumps({'kind': 'heartbeat', 'offset_ns': at}) + '\n')
            return
        if timings['first_event'] is None:
            timings['first_event'] = at
        body = '\n'.join(data)
        if body.strip() == '[DONE]':
            receipt['done_seen'], timings['terminal'] = True, at
            events.write(json.dumps({'kind': 'done', 'offset_ns': at}) + '\n')
            return
        try:
            obj = json.loads(body, parse_constant=_no_constants)
        except ValueError:
            raise InvalidStream('malformed_event') from None
        if not isinstance(obj, dict):
            raise InvalidStream('malformed_event')
        errors, row = [], {'kind': 'sse', 'offset_ns': at}
        already_finished = receipt['finish_reason'] is not None
        if _has_tools(obj):
            errors.append('unexpected_tools')
        for key, seen, allowed, error in (('model', 'observed_models', allowed_models, 'model_mismatch'),
                                          ('provider', 'observed_providers', [expected_provider], 'provider_mismatch'),
                                          ('id', 'generation_ids', None, 'generation_mismatch')):
            value = obj.get(key)
            if value is None:
                continue
            if not isinstance(value, str) or not value:
                errors.append('malformed_event')
                continue
            row[key] = value
            if value not in receipt[seen]:
                receipt[seen].append(value)
            if (allowed is not None and value not in allowed) or (key == 'id' and len(receipt[seen]) > 1):
                errors.append(error)
        if 'error' in obj or event_types[-1:] == ['error']:
            errors.append('stream_error')
        choices = obj.get('choices', [])
        if not isinstance(choices, list) or len(choices) > 1:
            errors.append('malformed_event')
            choices = []
        text = ''
        for choice in choices:
            if not isinstance(choice, dict) or choice.get('index', 0) != 0 or not isinstance(choice.get('delta', {}), dict):
                errors.append('malformed_event')
                continue
            if 'error' in choice:
                errors.append('stream_error')
            content = choice.get('delta', {}).get('content')
            if content is not None and not isinstance(content, str):
                errors.append('malformed_event')
            elif content:
                text += content
            finish = choice.get('finish_reason')
            if finish is not None:
                if receipt['finish_reason'] not in (None, finish):
                    errors.append('conflicting_finish_reason')
                receipt['finish_reason'] = row['finish_reason'] = finish if isinstance(finish, str) else None
                if finish == 'length':
                    errors.append('token_exhaustion')
                elif finish in {'tool_calls', 'function_call'}:
                    errors.append('unexpected_tools')
                elif finish != 'stop':
                    errors.append('unsupported_finish_reason')  # e.g. content_filter
        if text:
            if already_finished:
                errors.append('content_after_finish')
            if timings['first_text'] is None and text.strip():
                timings['first_text'] = at
            timings['last_text'] = at
            row['text'] = text
        if errors:
            row['errors'] = errors
        events.write(json.dumps(row, ensure_ascii=False) + '\n')
        if errors:
            raise InvalidStream(errors[0])

    start = time.monotonic_ns()
    (folder / 'dispatch.json').write_text(json.dumps({'dispatch_monotonic_ns': start}) + '\n')
    offset = lambda: time.monotonic_ns() - start
    limit = deadline_seconds * 1_000_000_000
    try:
        with urllib.request.urlopen(request, timeout=deadline_seconds) as response:
            receipt['http_status'], timings['headers'] = response.status, offset()
            if response.status != 200:
                raise InvalidStream('http_error')
            if response.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'text/event-stream':
                raise InvalidStream('unexpected_content_type')
            pending = b''
            while not receipt['done_seen']:
                if offset() >= limit:
                    raise TimeoutError()
                chunk = response.read1(65536)
                at = offset()
                if not chunk:
                    if at >= limit:
                        raise TimeoutError()
                    if pending.strip():
                        raise InvalidStream('truncated_stream')
                    break
                if timings['first_byte'] is None:
                    timings['first_byte'] = at
                pending += chunk
                while not receipt['done_seen'] and (match := re.search(rb'\r\n\r\n|\n\n|\r\r', pending)):
                    raw, pending = pending[:match.start()], pending[match.end():]
                    record(raw, at)
                if receipt['done_seen'] and pending.strip():
                    raise InvalidStream('content_after_done')
                if at >= limit:
                    raise TimeoutError()
            if not receipt['done_seen']:
                raise InvalidStream('missing_done')
            if receipt['finish_reason'] != 'stop':
                raise InvalidStream('missing_finish_reason')
            if not (receipt['observed_models'] and receipt['observed_providers'] and receipt['generation_ids']):
                raise InvalidStream('missing_identity')
            if timings['first_text'] is None:
                raise InvalidStream('missing_public_answer')
            receipt.update(status='completed', eligible=True, reason='completed')
    except urllib.error.HTTPError as error:
        receipt.update(http_status=error.code, reason='http_error')
    except TimeoutError:
        receipt.update(status='cutoff', reason='deadline_exceeded')
    except InvalidStream as error:
        receipt['reason'] = str(error)
    except (OSError, EOFError) as error:
        timed_out = isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
        receipt.update(status='cutoff' if timed_out else 'failed',
                       reason='deadline_exceeded' if timed_out else 'transport_error')
    timings['finished'] = offset()
    if receipt['status'] == 'running':
        receipt['status'] = 'failed'
    events.close()
    (folder / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--cell', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    cell = next(c for c in json.loads(args.manifest.read_text())['cells'] if c['id'] == args.cell)
    capture(cell['payload'], args.out / cell['id'], os.environ['OPENROUTER_API_KEY'], cell['backstop_seconds'],
            allowed_models=[cell['model'], cell['canonical_model']])
