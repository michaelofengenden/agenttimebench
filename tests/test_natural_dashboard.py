"""Read-only status serving must not turn missing evidence into live execution."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import importlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest


NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def sample():
    return {
        'schema_version': 1, 'campaign_id': 'pilot-test',
        'snapshot_at': '2026-10-09T12:00:00Z',
        'source_observed_at': '2026-10-09T12:00:00Z',
        'refresh_seconds': 5, 'stale_after_seconds': 30,
        'campaign': {'phase': 'preparing', 'model': 'claude-opus-5-5',
                     'effort': 'max', 'route': 'subscription',
                     'selected_tasks': 1, 'target_concurrency': 50,
                     'paused_reason': None},
        'counts': {'running': 50, 'peak_active_native': 0},
        'concurrency': [{'at': '2026-10-09T12:00:00Z', 'running': 0,
                         'waiting': 0, 'unknown': 0}],
        'tasks': [{'task_id': 'test-01', 'label': 'Example task',
                   'benchmark': 'Example benchmark', 'attempt_id': None,
                   'state': 'preparing', 'detail': 'Environment check pending',
                   'heartbeat_at': None, 'prompt_released_at': None,
                   'native_terminal_at': None, 'owned_work_drained_at': None,
                   'stop_verified': False,
                   'timing': {'status': 'not_started', 'elapsed_seconds': None,
                              'runtime_seconds': None, 'reason': None},
                   'grade': {'status': 'not_started', 'value': None, 'scale': None},
                   'archive': {'status': 'none', 'restore_status': 'unchecked'},
                   'issue': None}]
    }


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('agenttime.natural_dashboard'),
                             'The read-only natural dashboard is not implemented')
        self.dashboard = importlib.import_module('agenttime.natural_dashboard')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'status.json'
        self.write(sample())

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')

    @contextmanager
    def server(self):
        server = self.dashboard.make_server(self.path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def request(self, server, path, method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_stale_observation_is_not_refreshed_by_snapshot_generation_or_http(self):
        data = sample()
        data['source_observed_at'] = '2026-10-09T11:58:00Z'
        self.write(data)
        projected = self.dashboard.read_snapshot(self.path, now=NOW)
        self.assertEqual(projected['freshness']['status'], 'stale')
        self.assertEqual(projected['freshness']['age_seconds'], 120)
        self.assertEqual(projected['source_observed_at'], '2026-10-09T11:58:00Z')
        with self.server() as server:
            code, headers, body = self.request(server, '/api/pilot-status')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['freshness']['status'], 'stale')
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_unknown_or_future_observation_does_not_claim_freshness(self):
        for observed in (None, '2026-10-09T12:01:00Z'):
            with self.subTest(observed=observed):
                data = sample()
                data['source_observed_at'] = observed
                self.write(data)
                self.assertEqual(self.dashboard.read_snapshot(self.path, now=NOW)
                                 ['freshness']['status'], 'unknown')

    def test_output_whitelist_removes_nested_prompts_and_forecast_data(self):
        data = sample()
        data['prompt'] = 'PRIVATE_TOP_LEVEL'
        data['campaign']['credential'] = 'PRIVATE_CREDENTIAL'
        data['tasks'][0]['transcript'] = 'PRIVATE_TRANSCRIPT'
        data['tasks'][0]['timing']['forecast_seconds'] = 'PRIVATE_FORECAST'
        data['tasks'][0]['grade']['answer'] = 'PRIVATE_ANSWER'
        self.write(data)
        projected = self.dashboard.read_snapshot(self.path, now=NOW)
        encoded = json.dumps(projected)
        self.assertNotIn('PRIVATE_', encoded)
        self.assertEqual(projected['counts']['running'], 0)
        self.assertEqual(projected['counts']['preparing'], 1)

    def test_running_without_native_release_becomes_unknown(self):
        data = sample()
        data['tasks'][0]['state'] = 'running'
        self.write(data)
        projected = self.dashboard.read_snapshot(self.path, now=NOW)
        self.assertEqual(projected['tasks'][0]['state'], 'unknown')
        self.assertEqual(projected['counts']['active_native'], 0)
        self.assertEqual(projected['counts']['unknown'], 1)

    def test_collecting_keeps_capacity_without_inflating_active_native(self):
        data = sample()
        data['counts']['capacity_held'] = 1
        row = data['tasks'][0]
        row.update(state='collecting', attempt_id='a1',
                   prompt_released_at='2026-10-09T11:00:00Z',
                   native_terminal_at='2026-10-09T11:59:00Z',
                   owned_work_drained_at='2026-10-09T11:59:01Z')
        self.write(data)
        projected = self.dashboard.read_snapshot(self.path, now=NOW)
        self.assertEqual(projected['tasks'][0]['state'], 'collecting')
        self.assertEqual(projected['counts']['active_native'], 0)
        self.assertEqual(projected['counts']['capacity_held'], 1)
        row['owned_work_drained_at'] = None
        self.write(data)
        self.assertEqual(self.dashboard.read_snapshot(self.path, now=NOW)
                         ['tasks'][0]['state'], 'unknown')

    def test_terminal_without_owned_work_drain_does_not_become_finished(self):
        data = sample()
        data['tasks'][0].update(state='finished', prompt_released_at='2026-10-09T11:00:00Z',
                                native_terminal_at='2026-10-09T11:59:00Z')
        self.write(data)
        projected = self.dashboard.read_snapshot(self.path, now=NOW)
        self.assertEqual(projected['tasks'][0]['state'], 'unknown')
        self.assertEqual(projected['counts']['finished'], 0)

    def test_pending_timing_cannot_display_an_unverified_final_runtime(self):
        data = sample()
        data['tasks'][0]['timing'].update(status='pending', runtime_seconds=120)
        self.write(data)
        self.assertIsNone(self.dashboard.read_snapshot(self.path, now=NOW)
                          ['tasks'][0]['timing']['runtime_seconds'])

    def test_incomplete_completion_evidence_is_unknown_and_withholds_runtime(self):
        for missing in ('prompt_released_at', 'native_terminal_at',
                        'owned_work_drained_at', 'stop_verified'):
            with self.subTest(missing=missing):
                data = sample()
                row = data['tasks'][0]
                row.update(state='finished', attempt_id='attempt-one', stop_verified=True,
                           prompt_released_at='2026-10-09T11:00:00Z',
                           native_terminal_at='2026-10-09T11:02:00Z',
                           owned_work_drained_at='2026-10-09T11:02:01Z')
                row[missing] = False if missing == 'stop_verified' else None
                row['timing'].update(status='valid', runtime_seconds=121)
                self.write(data)
                projected = self.dashboard.read_snapshot(self.path, now=NOW)
                self.assertEqual(projected['tasks'][0]['state'], 'unknown')
                self.assertIsNone(projected['tasks'][0]['timing']['runtime_seconds'])
                self.assertEqual(projected['counts']['finished'], 0)

    def test_finished_wrong_answer_preserves_zero_and_separate_archive_state(self):
        data = sample()
        row = data['tasks'][0]
        row.update(state='finished', attempt_id='attempt-one', stop_verified=True,
                   prompt_released_at='2026-10-09T11:00:00Z',
                   native_terminal_at='2026-10-09T11:02:00Z',
                   owned_work_drained_at='2026-10-09T11:02:01Z')
        row['timing'].update(status='valid', runtime_seconds=121)
        row['grade'].update(status='available', value=0, scale=1)
        row['archive'].update(status='local')
        self.write(data)
        projected = self.dashboard.read_snapshot(self.path, now=NOW)
        self.assertEqual(projected['tasks'][0]['grade']['value'], 0)
        self.assertEqual(projected['tasks'][0]['timing']['runtime_seconds'], 121)
        self.assertEqual(projected['tasks'][0]['archive']['status'], 'local')
        self.assertTrue(projected['tasks'][0]['stop_verified'])

    def test_missing_malformed_or_wrong_shape_snapshot_returns_unavailable(self):
        variants = (None, '{not-json', '[]', '{"schema_version":1}',
                    '{"schema_version":1,"schema_version":1}',
                    json.dumps(sample()).replace('"running": 50', '"running": NaN'))
        for contents in variants:
            with self.subTest(contents=contents):
                if contents is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_text(contents)
                with self.server() as server:
                    code, _, body = self.request(server, '/api/pilot-status')
                result = json.loads(body)
                self.assertEqual(code, 503)
                self.assertFalse(result['available'])
                self.assertNotIn(str(self.path), body.decode())
                self.assertNotIn('tasks', result)

    def test_duplicate_tasks_and_nonfinite_timing_are_rejected(self):
        data = sample()
        data['tasks'].append(dict(data['tasks'][0]))
        self.write(data)
        with self.assertRaises(self.dashboard.SnapshotError):
            self.dashboard.read_snapshot(self.path, now=NOW)
        data = sample()
        data['tasks'][0]['timing']['elapsed_seconds'] = float('inf')
        self.write(data)
        with self.assertRaises(self.dashboard.SnapshotError):
            self.dashboard.read_snapshot(self.path, now=NOW)

    def test_only_exact_routes_are_served_and_mutations_are_rejected(self):
        (self.root / 'secret.txt').write_text('DO_NOT_SERVE_ME')
        with self.server() as server:
            self.assertEqual(server.server_address[0], '127.0.0.1')
            for route in ('/../secret.txt', '/%2e%2e/secret.txt', '/status.json',
                          '/api/pilot-status/../../secret.txt',
                          '/api/pilot-status?file=secret.txt'):
                with self.subTest(route=route):
                    code, _, body = self.request(server, route)
                    self.assertEqual(code, 404)
                    self.assertNotIn(b'DO_NOT_SERVE_ME', body)
            for method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                with self.subTest(method=method):
                    self.assertEqual(self.request(server, '/api/pilot-status', method)[0], 405)

    def test_untrusted_host_cannot_read_status(self):
        with self.server() as server:
            code, _, _ = self.request(server, '/api/pilot-status', headers={'Host': 'evil.test'})
        self.assertEqual(code, 403)

    def test_serving_status_does_not_modify_snapshot_or_create_files(self):
        before = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                  for p in self.root.iterdir()}
        with self.server() as server:
            for _ in range(3):
                self.assertEqual(self.request(server, '/api/pilot-status')[0], 200)
                self.assertEqual(self.request(server, '/')[0], 200)
        after = {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                 for p in self.root.iterdir()}
        self.assertEqual(after, before)

    def test_snapshot_symlink_is_not_followed(self):
        alternate = self.root / 'private.json'
        alternate.write_text(json.dumps(sample()))
        self.path.unlink()
        self.path.symlink_to(alternate)
        with self.assertRaises(self.dashboard.SnapshotError):
            self.dashboard.read_snapshot(self.path, now=NOW)

    def test_task_text_never_enters_html_and_api_is_json_not_script(self):
        data = sample()
        attack = '</script><script>window.injected=true</script>'
        data['tasks'][0]['label'] = attack
        self.write(data)
        with self.server() as server:
            code, headers, body = self.request(server, '/')
            self.assertEqual(code, 200)
            self.assertNotIn(attack.encode(), body)
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
            code, headers, body = self.request(server, '/api/pilot-status')
            self.assertEqual(code, 200)
            self.assertTrue(headers['Content-Type'].startswith('application/json'))
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(json.loads(body)['tasks'][0]['label'], attack)


if __name__ == '__main__':
    unittest.main()
