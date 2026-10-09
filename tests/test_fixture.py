"""Exercise real detached workers and controller restart, without any model."""
import base64
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from agenttime.evidence import EvidenceStore, IntegrityError, canonical_json

from db_support import DatabaseTestCase
from agenttime.ledger import Ledger


class FixtureTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        try:
            self.fixture = importlib.import_module('agenttime.fixture')
        except ModuleNotFoundError:
            self.fail('Autonomous fixture executor is not implemented')
        self.temp = tempfile.TemporaryDirectory(prefix='agenttime-fixture-', dir='/tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.dsn)
        self.ledger.initialize(2)

    def create(self, tasks):
        self.ledger.create_campaign('c', self.fixture.build_manifest(self.root, tasks))

    def controller(self):
        return subprocess.Popen([sys.executable, '-m', 'agenttime', 'fixture', 'run', '--campaign', 'c'],
            env=dict(os.environ, AGENTTIME_DSN=self.dsn), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for(self, predicate):
        end = time.monotonic() + 20
        while time.monotonic() < end:
            value = predicate()
            if value:
                return value
            time.sleep(.03)
        self.fail('Fixture condition did not arrive within the test watchdog')

    def test_dispatch_preflight_failure_reserves_no_task(self):
        self.create([{'id':'one'}, {'id':'two'}])
        with patch.object(self.fixture, '_dispatch_environment', side_effect=ValueError('Docker endpoint unavailable')), patch.object(self.fixture.subprocess, 'Popen') as popen:
            result = self.fixture.tick(self.ledger, 'c')
        self.assertEqual(self.ledger.attempts('c'), [])
        self.assertIsNotNone(result['paused_reason'])
        popen.assert_not_called()

    def test_dispatch_exception_holds_one_claim_and_pauses_before_next(self):
        self.create([{'id':'one'}, {'id':'two'}])
        with patch.object(self.fixture.subprocess, 'Popen', side_effect=OSError('injected spawn failure')):
            result = self.fixture.tick(self.ledger, 'c')
        self.assertEqual(len(self.ledger.attempts('c')), 1)
        self.assertEqual(result['held'], 1)
        self.assertEqual(result['queued'], 1)
        self.assertIsNotNone(result['paused_reason'])

    def test_controller_restart_retains_one_subject_invocation(self):
        self.create([{'id': 'slow', 'work_seconds': .8}])
        first = self.controller()
        try:
            self.wait_for(lambda: any('prompt_released' in p.read_text() for p in self.root.glob('attempts/*/events.jsonl')))
        finally:
            first.kill()
            first.communicate(timeout=5)
        second = self.controller()
        stdout, stderr = second.communicate(timeout=20)
        self.assertEqual(second.returncode, 0, stderr)
        status = json.loads(stdout)
        self.assertEqual(status['finished'], 1)
        attempts = self.ledger.attempts('c')
        self.assertEqual(len(attempts), 1)
        report = attempts[0]['report']
        self.assertEqual(report['timing_status'], 'valid')
        self.assertEqual(report['score'], 1)
        self.assertEqual(report['admission'], 'excluded_fixture')
        self.assertEqual(len(list(self.root.glob('attempts/*/invocation.json'))), 1)
        events = [json.loads(s) for s in next(self.root.glob('attempts/*/events.jsonl')).read_text().splitlines()]
        self.assertEqual(sum(e['kind'] == 'prompt_released' for e in events), 1)

    def test_duplicate_worker_commands_cannot_invoke_again(self):
        self.create([{'id': 'one', 'work_seconds': .1}])
        a = self.ledger.reserve('c')
        workers = [subprocess.Popen([sys.executable, '-m', 'agenttime', 'fixture', 'worker', '--attempt', a['id']],
            env=dict(os.environ, AGENTTIME_DSN=self.dsn), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
        for w in workers:
            stdout, stderr = w.communicate(timeout=20)
            self.assertEqual(w.returncode, 0, stderr)
        self.fixture.tick(self.ledger, 'c')
        self.assertEqual(len(self.ledger.attempts('c')), 1)
        self.assertEqual(self.ledger.attempt(a['id'])['state'], 'finished')
        events = [json.loads(s) for s in next(self.root.glob('attempts/*/events.jsonl')).read_text().splitlines()]
        self.assertEqual(sum(e['kind'] == 'prompt_released' for e in events), 1)

    def test_late_background_write_cannot_change_terminal_grade(self):
        self.create([{'id': 'late', 'behavior': 'post_terminal_write', 'setup_seconds': .15,
                      'work_seconds': .1, 'grade_seconds': .15}])
        status = self.fixture.run_campaign(self.ledger, 'c')
        self.assertEqual(status['finished'], 1)
        a = self.ledger.attempts('c')[0]
        report = a['report']
        self.assertEqual(report['score'], 1)
        self.assertEqual((self.root / 'attempts' / a['id'] / 'answer.txt').read_text(), 'late overwrite')
        events = [json.loads(s) for s in (self.root / 'attempts' / a['id'] / 'events.jsonl').read_text().splitlines()]
        by_kind = {e['kind']: e for e in events}
        expected = (by_kind['native_terminal']['monotonic_ns'] - by_kind['prompt_released']['monotonic_ns']) / 1e9
        self.assertEqual(report['runtime_seconds'], expected)
        self.assertLess(report['runtime_seconds'], (events[-1]['monotonic_ns'] - events[0]['monotonic_ns']) / 1e9)

    def test_grade_loss_and_artifact_loss_preserve_timing_without_reruns(self):
        self.create([{'id': 'grade', 'behavior': 'grade_failure'}, {'id': 'artifact', 'behavior': 'artifact_missing'},
                     {'id': 'wrong', 'behavior': 'wrong_answer'}])
        status = self.fixture.run_campaign(self.ledger, 'c')
        self.assertEqual(status['finished'], 3)
        reports = {a['task_id']: a['report'] for a in self.ledger.attempts('c')}
        for r in reports.values():
            self.assertEqual(r['timing_status'], 'valid')
        self.assertIsNone(reports['grade']['score'])
        self.assertEqual(reports['grade']['quality_status'], 'unavailable')
        self.assertIsNone(reports['artifact']['score'])
        self.assertEqual(reports['artifact']['artifact_status'], 'unavailable')
        self.assertEqual(reports['wrong']['score'], 0)
        self.assertEqual(len(self.ledger.attempts('c')), 3)

    def test_dead_worker_is_held_while_unaffected_task_finishes(self):
        self.create([{'id': 'lost', 'behavior': 'crash_before_release'}, {'id': 'okay'}])
        status = self.fixture.run_campaign(self.ledger, 'c')
        self.assertEqual(status['finished'], 1)
        self.assertEqual(status['held'], 1)
        rows = {a['task_id']: a for a in self.ledger.attempts('c')}
        self.assertTrue(rows['lost']['holds_capacity'])
        self.assertEqual(rows['lost']['state'], 'quarantined')
        self.assertEqual(rows['okay']['report']['score'], 1)
        self.fixture.tick(self.ledger, 'c')
        self.assertEqual(len(self.ledger.attempts('c')), 2)

    def test_ambiguous_dispatch_is_never_replayed(self):
        self.create([{'id': 'lost'}, {'id': 'okay'}])
        original = self.ledger.reserve('c')  # Controller died before recording whether Popen happened.
        status = self.fixture.run_campaign(self.ledger, 'c')
        self.assertEqual(status['held'], 1)
        self.assertEqual(status['finished'], 1)
        self.assertEqual(self.ledger.attempt(original['id'])['state'], 'reserved')
        self.assertFalse((self.root / 'attempts' / original['id'] / 'invocation.json').exists())

    def test_changed_effective_manifest_and_invalid_task_are_refused(self):
        self.create([{'id': 'one'}])
        with self.assertRaises(ValueError):
            self.ledger.create_campaign('c', self.fixture.build_manifest(self.root, [{'id':'other'}]))
        for task in ({'id':'bad','work_seconds': float('nan')}, {'id':'bad','behavior':'real_model'},
                     {'id':'bad','duration_request': 60}):
            with self.subTest(task=task), self.assertRaises(ValueError):
                self.fixture.build_manifest(self.root, [task])

    def pending_completion(self):
        """Make real worker evidence while leaving release to reconciliation."""
        self.create([{'id': 'one', 'work_seconds': 0}])
        attempt = self.ledger.reserve('c')
        with patch.object(self.ledger, 'finish', return_value=None):
            self.assertTrue(self.fixture.run_worker(self.ledger, attempt['id']))
        folder = self.root / 'attempts' / attempt['id']
        receipt = json.loads((folder / 'completion.json').read_bytes())
        store = EvidenceStore(self.root / 'evidence')
        report = json.loads(store.get(receipt['report_sha256']))
        return self.ledger.attempt(attempt['id']), folder, receipt, report, store

    def replace_report(self, folder, receipt, report, store):
        receipt = dict(receipt, report_sha256=store.put(canonical_json(report)))
        (folder / 'completion.json').write_bytes(canonical_json(receipt))

    def test_receipt_refreshes_owner_after_reserved_snapshot(self):
        self.create([{'id': 'one', 'work_seconds': 0}])
        snapshot = self.ledger.reserve('c')
        self.assertIsNone(snapshot['worker_id'])
        self.assertTrue(self.fixture.run_worker(self.ledger, snapshot['id']))
        try:
            self.fixture._reconcile(self.ledger, self.ledger.campaign('c'), snapshot)
        except IntegrityError as error:
            self.fail(f'Completion raced a stale reserved snapshot: {error}')
        self.assertIsNone(self.ledger.gate()['paused_reason'])
        self.assertFalse(self.ledger.attempt(snapshot['id'])['holds_capacity'])

    def missing_published_object_pauses(self, reference):
        attempt, folder, receipt, report, store = self.pending_completion()
        digest = receipt['report_sha256'] if reference == 'report' else report[reference]
        (store.root / digest).unlink()
        result = self.fixture.tick(self.ledger, 'c')
        self.assertIsNotNone(result['paused_reason'])
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])
        self.assertEqual(self.ledger.attempt(attempt['id'])['state'], 'quarantined')

    def test_published_receipt_missing_report_pauses(self):
        self.missing_published_object_pauses('report')

    def test_published_receipt_missing_journal_pauses(self):
        self.missing_published_object_pauses('journal_sha256')

    def test_published_receipt_missing_submission_pauses(self):
        self.missing_published_object_pauses('submission_sha256')

    def test_null_completion_receipt_pauses_instead_of_crashing(self):
        attempt, folder, _, _, _ = self.pending_completion()
        (folder / 'completion.json').write_text('null')
        try:
            result = self.fixture.tick(self.ledger, 'c')
        except TypeError as error:
            self.fail(f'Invalid receipt bypassed evidence handling: {error}')
        self.assertIsNotNone(result['paused_reason'])
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])

    def test_receipt_shapes_are_strict(self):
        attempt, folder, receipt, _, _ = self.pending_completion()
        for malformed in [None, [], {}, dict(receipt, extra=True),
                          dict(receipt, report_sha256=None), dict(receipt, worker_id=True)]:
            with self.subTest(receipt=malformed):
                (folder / 'completion.json').write_bytes(canonical_json(malformed))
                with self.assertRaises(IntegrityError):
                    self.fixture._reconcile(self.ledger, self.ledger.campaign('c'), attempt)

    def test_report_fields_are_bound_to_manifest_and_stage_events(self):
        attempt, folder, receipt, report, store = self.pending_completion()
        changed = {'source_sha256': '0' * 64, 'executor': 'harbor', 'schema_version': True,
                   'prompt_sha256': '0' * 64, 'timing_reason': 'invented',
                   'artifact_status': 'unavailable', 'submission_sha256': None,
                   'quality_status': 'unavailable', 'score': 0, 'limitations': [], 'extra': True}
        for field, value in changed.items():
            with self.subTest(field=field):
                self.replace_report(folder, receipt, dict(report, **{field: value}), store)
                with patch.object(self.ledger, 'finish', return_value=None):
                    with self.assertRaises(IntegrityError):
                        self.fixture._reconcile(self.ledger, self.ledger.campaign('c'), attempt)
        for value in [None, [], {'schema_version': 1}, dict(report, score=True)]:
            with self.subTest(shape=value):
                self.replace_report(folder, receipt, value, store)
                with self.assertRaises(IntegrityError):
                    self.fixture._reconcile(self.ledger, self.ledger.campaign('c'), attempt)

    def test_stage_history_cannot_be_omitted_reordered_or_forged(self):
        attempt, folder, receipt, report, store = self.pending_completion()
        original = [json.loads(line) for line in store.get(report['journal_sha256']).splitlines()]
        for change in ['missing_capture', 'drain_before_grade', 'wrong_prompt', 'wrong_grade', 'wrong_terminal_digest']:
            with self.subTest(change=change):
                events = deepcopy(original)
                if change == 'missing_capture':
                    events = [e for e in events if e['kind'] != 'capture_finished']
                elif change == 'drain_before_grade':
                    events[4], events[5] = events[5], events[4]
                    events[4]['monotonic_ns'], events[5]['monotonic_ns'] = events[5]['monotonic_ns'], events[4]['monotonic_ns']
                elif change == 'wrong_prompt':
                    events[1]['payload']['prompt_sha256'] = '0' * 64
                elif change == 'wrong_grade':
                    events[4]['payload']['score'] = 0
                else:
                    events[2]['payload']['submission_sha256'] = '0' * 64
                for sequence, event in enumerate(events, 1):
                    event['sequence'] = sequence
                events[-1]['payload']['final_sequence'] = len(events)
                altered = dict(report, journal_sha256=store.put(b''.join(canonical_json(e) + b'\n' for e in events)))
                self.replace_report(folder, receipt, altered, store)
                with patch.object(self.ledger, 'finish', return_value=None):
                    with self.assertRaises(IntegrityError):
                        self.fixture._reconcile(self.ledger, self.ledger.campaign('c'), attempt)

    def test_actual_capture_exception_preserves_native_timing_without_replay(self):
        self.create([{'id': 'one', 'work_seconds': 0}])
        attempt = self.ledger.reserve('c')
        original = EvidenceStore.put
        def capture_fault(store, data):
            if data == b'42':
                raise OSError('injected capture failure')
            return original(store, data)
        with patch.object(EvidenceStore, 'put', capture_fault):
            self.assertTrue(self.fixture.run_worker(self.ledger, attempt['id']))
        row = self.ledger.attempt(attempt['id'])
        self.assertEqual(row['state'], 'finished')
        self.assertEqual(row['report']['timing_status'], 'valid')
        self.assertEqual(row['report']['artifact_status'], 'unavailable')
        self.assertEqual(row['report']['quality_status'], 'unavailable')
        self.assertIsNone(row['report']['score'])
        self.assertFalse(self.fixture.run_worker(self.ledger, attempt['id']))
        self.assertEqual(len(list(self.root.glob('attempts/*/invocation.json'))), 1)

    def test_actual_grade_exception_preserves_artifact_and_native_timing(self):
        self.create([{'id': 'one', 'work_seconds': 0}])
        attempt = self.ledger.reserve('c')
        original = EvidenceStore.get
        failed = False
        def grade_fault(store, digest):
            nonlocal failed
            if digest == hashlib.sha256(b'42').hexdigest() and not failed:
                failed = True
                raise OSError('injected grader failure')
            return original(store, digest)
        with patch.object(EvidenceStore, 'get', grade_fault):
            self.assertTrue(self.fixture.run_worker(self.ledger, attempt['id']))
        report = self.ledger.attempt(attempt['id'])['report']
        self.assertEqual(report['timing_status'], 'valid')
        self.assertEqual(report['artifact_status'], 'preserved')
        self.assertEqual(report['quality_status'], 'unavailable')
        self.assertIsNone(report['score'])

    def test_publication_failure_retains_closed_local_evidence_and_reservation(self):
        self.create([{'id': 'one', 'work_seconds': 0}])
        attempt = self.ledger.reserve('c')
        with patch.object(EvidenceStore, 'put', side_effect=OSError('storage unavailable')):
            self.assertFalse(self.fixture.run_worker(self.ledger, attempt['id']))
        row = self.ledger.attempt(attempt['id'])
        self.assertTrue(row['holds_capacity'])
        self.assertIsNone(row['report'])
        folder = self.root / 'attempts' / attempt['id']
        self.assertTrue((folder / 'report.json').is_file())
        local = json.loads((folder / 'report.json').read_bytes())
        self.assertEqual(local['timing_status'], 'valid')
        self.assertFalse((folder / 'completion.json').exists())
        events = [json.loads(line) for line in (folder / 'events.jsonl').read_bytes().splitlines()]
        self.assertEqual([e['kind'] for e in events][-2:], ['owned_work_drained', 'journal_closed'])
        self.assertFalse(self.fixture.run_worker(self.ledger, attempt['id']))
        with patch.object(self.fixture, '_alive', return_value=False):
            self.fixture.tick(self.ledger, 'c')
        self.assertIn('evidence_publication_failed', self.ledger.attempt(attempt['id'])['reason'])

    def test_fresh_admission_recheck_continues_after_capacity_race(self):
        self.create([{'id': 'one'}, {'id': 'two'}, {'id': 'queued'}])
        first, second = self.ledger.reserve('c'), self.ledger.reserve('c')
        status = self.fixture.status
        raced = False
        def finish_after_snapshot(ledger, campaign_id):
            nonlocal raced
            value = status(ledger, campaign_id)
            if value['held'] == 2 and not raced:
                raced = True
                self.fixture.run_worker(self.ledger, first['id'])
                self.fixture.run_worker(self.ledger, second['id'])
            return value  # Completions race with the controller's stale snapshot.
        with patch.object(self.fixture, 'status', finish_after_snapshot):
            result = self.fixture.run_campaign(self.ledger, 'c')
        self.assertEqual(result['finished'], 3)
        self.assertEqual(result['queued'], 0)

    def test_finished_owned_worker_handles_are_reaped(self):
        self.create([{'id': 'one', 'work_seconds': 0}])
        attempt = self.ledger.reserve('c')
        self.assertTrue(self.fixture.run_worker(self.ledger, attempt['id']))
        child = subprocess.Popen([sys.executable, '-c', 'pass'])
        self.addCleanup(child.wait, timeout=5)
        self.addCleanup(self.fixture._CHILDREN.pop, attempt['id'], None)
        self.fixture._CHILDREN[attempt['id']] = child
        self.wait_for(lambda: self.fixture.psutil.Process(child.pid).status() == self.fixture.psutil.STATUS_ZOMBIE)
        self.fixture.status(self.ledger, 'c')
        self.assertNotIn(attempt['id'], self.fixture._CHILDREN)
        self.assertIsNotNone(child.returncode)

    def test_finished_attempt_with_live_worker_does_not_block_status(self):
        self.create([{'id': 'one', 'work_seconds': 0}])
        attempt = self.ledger.reserve('c')
        self.assertTrue(self.fixture.run_worker(self.ledger, attempt['id']))
        child = subprocess.Popen(
            [sys.executable, '-c', 'import sys; print("ready", flush=True); sys.stdin.buffer.read()'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        pool = ThreadPoolExecutor(max_workers=1)
        self.fixture._CHILDREN[attempt['id']] = child
        try:
            self.assertEqual(child.stdout.readline(), b'ready\n')
            future = pool.submit(self.fixture.status, self.ledger, 'c')
            try:
                result = future.result(timeout=1)
            except TimeoutError:
                self.fail('Status blocked waiting for a finished worker that has not exited')
            self.assertEqual(result['finished'], 1)
            self.assertIs(self.fixture._CHILDREN.get(attempt['id']), child)
            self.assertIsNone(child.poll())
        finally:
            # Closing this test-owned pipe releases only this child.
            child.communicate(timeout=5)
            pool.shutdown(wait=True)
            self.fixture._CHILDREN.pop(attempt['id'], None)

    def test_frozen_source_disagreement_pauses_shared_admission(self):
        self.create([{'id': 'one'}])
        attempt = self.ledger.reserve('c')
        with patch.object(self.fixture, 'source_digest', return_value='0' * 64):
            self.assertFalse(self.fixture.run_worker(self.ledger, attempt['id']))
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])
        self.assertFalse((self.root / 'attempts' / attempt['id'] / 'invocation.json').exists())

    def test_frozen_runtime_disagreement_pauses_shared_admission(self):
        self.create([{'id': 'one'}])
        attempt = self.ledger.reserve('c')
        with patch.object(self.fixture, 'runtime_identity', return_value={'python': 'other'}):
            self.assertFalse(self.fixture.run_worker(self.ledger, attempt['id']))
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])

    def test_frozen_prompt_disagreement_cannot_claim_the_actual_fixture_prompt(self):
        manifest = self.fixture.build_manifest(self.root, [{'id': 'one'}])
        manifest['prompt_sha256'] = '0' * 64
        self.ledger.create_campaign('c', manifest)
        attempt = self.ledger.reserve('c')
        self.assertFalse(self.fixture.run_worker(self.ledger, attempt['id']))
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])
        self.assertFalse((self.root / 'attempts' / attempt['id'] / 'invocation.json').exists())
