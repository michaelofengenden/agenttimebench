"""Loopback-only, read-only view of an allowlisted natural-run metadata snapshot.

This module never imports the runner, reads transcripts, or starts/reconciles work.
The controller is responsible for atomically publishing metadata, not task content.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import stat


MAX_BYTES = 4 * 1024 * 1024
STATES = ('preparing', 'ready', 'running', 'waiting', 'collecting', 'finished', 'blocked', 'unknown')
PAGE = Path(__file__).parent / 'static' / 'natural_dashboard.html'


class SnapshotError(ValueError):
    """The snapshot cannot support a trustworthy view."""


def _text(value, limit=600, nullable=False):
    if nullable and value is None:
        return None
    if not isinstance(value, str) or len(value) > limit:
        raise SnapshotError('Invalid metadata text')
    return value


def _object(value):
    if not isinstance(value, dict):
        raise SnapshotError('Invalid metadata object')
    return value


def _choice(value, choices):
    if value not in choices or not isinstance(value, str):
        raise SnapshotError('Invalid metadata state')
    return value


def _number(value, nullable=False, integer=False, maximum=None, minimum=0):
    if nullable and value is None:
        return None
    if (type(value) not in (int, float) or not math.isfinite(value)
            or integer and type(value) is not int
            or minimum is not None and value < minimum
            or maximum is not None and value > maximum):
        raise SnapshotError('Invalid metadata number')
    return value


def _timestamp(value, nullable=False):
    if nullable and value is None:
        return None
    value = _text(value, 64)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise SnapshotError('Observation requires a timezone')
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _date(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotError('Duplicate metadata key')
        result[key] = value
    return result


def _reject_constant(value):
    raise SnapshotError('Nonfinite metadata value')


def _task(raw):
    raw = _object(raw)
    task_id = _text(raw['task_id'], 180)
    if not task_id:
        raise SnapshotError('Task identity is missing')
    row = {key: _text(raw[key], limit) for key, limit in
           (('task_id', 180), ('label', 400), ('benchmark', 160))}
    row['attempt_id'] = _text(raw.get('attempt_id'), 180, nullable=True)
    row['state'] = _choice(raw['state'], STATES)
    row['detail'] = _text(raw.get('detail', ''))
    for name in ('heartbeat_at', 'prompt_released_at', 'native_terminal_at', 'owned_work_drained_at'):
        row[name] = _timestamp(raw.get(name), nullable=True)
    if type(raw.get('stop_verified', False)) is not bool:
        raise SnapshotError('Invalid stop observation')
    row['stop_verified'] = raw.get('stop_verified', False)
    issue = raw.get('issue')
    row['issue'] = None if issue is None else {
        'code': _text(_object(issue)['code'], 100), 'message': _text(issue['message'])}
    # A living worker may only be preparing. Never promote it to native execution.
    if row['state'] in ('running', 'waiting') and (
            not row['prompt_released_at'] or row['owned_work_drained_at']):
        row['state'] = 'unknown'
        row['detail'] = 'Native execution evidence is incomplete or conflicting.'
    if row['state'] == 'collecting' and not all(row[name] for name in (
            'prompt_released_at', 'native_terminal_at', 'owned_work_drained_at')):
        row['state'] = 'unknown'
        row['detail'] = 'Collection evidence is incomplete; native state is unknown.'
    completion_verified = row['stop_verified'] and all(row[name] for name in (
        'prompt_released_at', 'native_terminal_at', 'owned_work_drained_at'))
    if row['state'] == 'finished' and not completion_verified:
        row['state'] = 'unknown'
        row['detail'] = 'Completion evidence is incomplete; native state is unknown.'
    timing = _object(raw.get('timing', {}))
    timing_status = _choice(timing.get('status', 'not_started'),
                            ('not_started', 'live', 'pending', 'valid', 'invalid'))
    elapsed = _number(timing.get('elapsed_seconds'), nullable=True)
    runtime = _number(timing.get('runtime_seconds'), nullable=True)
    if timing_status != 'valid' or not completion_verified:
        runtime = None
        if timing_status == 'valid':
            timing_status = 'pending'
    row['timing'] = {'status': timing_status, 'elapsed_seconds': elapsed,
                     'runtime_seconds': runtime,
                     'reason': _text(timing.get('reason'), nullable=True)}
    grade = _object(raw.get('grade', {}))
    grade_status = _choice(grade.get('status', 'not_started'),
                           ('not_started', 'queued', 'running', 'available', 'failed', 'unavailable'))
    value = _number(grade.get('value'), nullable=True, minimum=None)
    scale = grade.get('scale')
    if scale is not None:
        scale = _text(scale, 100) if isinstance(scale, str) else _number(scale, minimum=None)
    row['grade'] = {'status': grade_status, 'value': value if grade_status == 'available' else None,
                    'scale': scale}
    archive = _object(raw.get('archive', {}))
    row['archive'] = {
        'status': _choice(archive.get('status', 'none'),
                          ('none', 'local', 'transferring', 'acknowledged', 'failed')),
        'restore_status': _choice(archive.get('restore_status', 'unchecked'),
                                  ('unchecked', 'verified', 'failed'))}
    return row


def read_snapshot(path, *, now=None):
    """Read only the exact regular metadata file; strip every unrecognized field."""
    now = now or datetime.now(timezone.utc)
    try:
        path = Path(path)
        if not path.is_absolute():
            raise SnapshotError('An absolute snapshot path is required')
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SnapshotError('Snapshot must be a regular file')
            content = stream.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise SnapshotError('Snapshot exceeds the metadata limit')
        raw = _object(json.loads(content, object_pairs_hook=_pairs, parse_constant=_reject_constant))
        if type(raw.get('schema_version')) is not int or raw['schema_version'] != 1:
            raise SnapshotError('Unsupported metadata schema')
        campaign = _object(raw['campaign'])
        selected = _number(campaign['selected_tasks'], integer=True, maximum=220)
        tasks = raw['tasks']
        if not isinstance(tasks, list) or len(tasks) != selected:
            raise SnapshotError('The snapshot must include every selected task')
        rows = [_task(task) for task in tasks]
        if len({row['task_id'] for row in rows}) != len(rows):
            raise SnapshotError('Duplicate task identity')
        history = raw.get('concurrency', [])
        if not isinstance(history, list) or len(history) > 20000:
            raise SnapshotError('Invalid concurrency history')
        series = []
        for point in history:
            point = _object(point)
            item = {'at': _timestamp(point['at'])}
            item.update({key: _number(point.get(key, 0), nullable=True, integer=True, maximum=selected)
                         for key in ('running', 'waiting', 'unknown')})
            if series and _date(item['at']) < _date(series[-1]['at']):
                raise SnapshotError('Concurrency history is out of order')
            if all(item[key] is not None for key in ('running', 'waiting', 'unknown')):
                if sum(item[key] for key in ('running', 'waiting', 'unknown')) > selected:
                    raise SnapshotError('Concurrency exceeds selected task count')
            series.append(item)
        counts = {state: sum(row['state'] == state for row in rows) for state in STATES}
        counts['active_native'] = counts['running'] + counts['waiting']
        counts['capacity_held'] = _number(_object(raw.get('counts', {})).get(
            'capacity_held', counts['active_native'] + counts['unknown']), integer=True, maximum=selected)
        peak = _number(_object(raw.get('counts', {})).get('peak_active_native', 0),
                       integer=True, maximum=selected)
        counts['peak_active_native'] = max(peak, counts['active_native'])
        observed = _timestamp(raw.get('source_observed_at'), nullable=True)
        stale_after = _number(raw.get('stale_after_seconds', 30), integer=True, maximum=300, minimum=1)
        age = None if observed is None else (now - _date(observed)).total_seconds()
        freshness = 'unknown' if age is None or age < -5 else 'stale' if age > stale_after else 'fresh'
        return {
            'schema_version': 1, 'available': True,
            'campaign_id': _text(raw['campaign_id'], 180),
            'snapshot_at': _timestamp(raw['snapshot_at']), 'source_observed_at': observed,
            'served_at': now.isoformat().replace('+00:00', 'Z'),
            'refresh_seconds': _number(raw.get('refresh_seconds', 5), integer=True, minimum=1, maximum=60),
            'stale_after_seconds': stale_after,
            'freshness': {'status': freshness, 'age_seconds': max(0, age) if age is not None else None},
            'campaign': {
                'phase': _choice(campaign['phase'], ('preparing', 'qualifying', 'running', 'paused', 'collecting', 'complete')),
                **{key: _text(campaign[key], 120) for key in ('model', 'effort', 'route')},
                'selected_tasks': selected,
                'target_concurrency': _number(campaign['target_concurrency'], integer=True, minimum=1, maximum=220),
                'paused_reason': _text(campaign.get('paused_reason'), nullable=True)},
            'counts': counts, 'concurrency': series, 'tasks': rows}
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        raise SnapshotError('Status metadata is missing, incomplete or invalid.') from error


def _content_policy(page):
    import base64
    def hashes(tag):
        return ' '.join("'sha256-" + base64.b64encode(hashlib.sha256(part).digest()).decode() + "'"
                        for part in re.findall(b'<' + tag + b'>(.*?)</' + tag + b'>', page, re.DOTALL))
    return ("default-src 'none'; connect-src 'self'; script-src " + hashes(b'script')
            + '; style-src ' + hashes(b'style')
            + "; img-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def make_server(snapshot, *, port=4181):
    """Construct a loopback server. GET never dispatches, reconciles or writes."""
    snapshot = Path(snapshot)
    if not snapshot.is_absolute():
        raise ValueError('Use an absolute snapshot path')
    page = PAGE.read_bytes()
    policy = _content_policy(page)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'AgentTimeStatus/1'

        def log_message(self, *args):
            pass

        def _send(self, status, body, content_type='application/json; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', policy)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def do_GET(self):
            allowed_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in allowed_hosts:
                self._send(403, b'{"error":"Local host required"}')
                return
            if self.path in ('/', '/index.html'):
                self._send(200, page, 'text/html; charset=utf-8')
            elif self.path == '/api/pilot-status':
                try:
                    data, status = read_snapshot(snapshot), 200
                except SnapshotError:
                    data = {'schema_version': 1, 'available': False, 'error': {
                        'code': 'snapshot_unavailable',
                        'message': 'Status metadata is unavailable. Last observed state may be stale.'}}
                    status = 503
                self._send(status, json.dumps(data, allow_nan=False).encode('utf-8'))
            else:
                self._send(404, b'{"error":"Not found"}')

        do_HEAD = do_GET

        def do_POST(self):
            self._send(405, b'{"error":"Read-only dashboard"}')

        do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_POST

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--port', type=int, default=4181)
    args = parser.parse_args()
    if not args.snapshot.is_absolute() or not 0 <= args.port <= 65535:
        parser.error('Use an absolute snapshot path and a valid local port')
    with make_server(args.snapshot, port=args.port) as server:
        print(f'Read-only AgentTime dashboard: http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
