"""Durable Harbor claims must never replay or admit unverifiable completions."""
import asyncio
from importlib.metadata import PackageNotFoundError, version
from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agenttime.evidence import EvidenceStore, IntegrityError, canonical_json
from agenttime.ledger import Ledger
from db_support import DatabaseTestCase


try:
    version('harbor')
    HARBOR_INSTALLED = True
except PackageNotFoundError:
    HARBOR_INSTALLED = False


@unittest.skipUnless(HARBOR_INSTALLED, 'optional pinned Harbor runtime required')
class HarborManifestTests(unittest.TestCase):
    def setUp(self):
        try:
            self.worker = importlib.import_module('agenttime.harbor_worker')
        except ModuleNotFoundError:
            self.fail('Durable Harbor fixture executor is not implemented')

    def test_arbitrary_models_commands_paths_and_limits_are_rejected(self):
        for extra in ({'command':'echo 42'}, {'model':'claude'}, {'path':'/tmp/task'},
                      {'duration_request':60}, {'work_seconds':True}, {'work_seconds':float('nan')},
                      {'mode':'real_agent'}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                self.worker.build_manifest(Path('/tmp/no-launch'), [dict(id='one', **extra)])

    def test_loaded_worker_and_controller_must_match_current_sources(self):
        manifest = self.worker.build_manifest(Path('/tmp/no-launch'), [{'id':'one'}])
        for module, field in ((self.worker, '_LOADED_SOURCE'), (self.worker.fixture, '_LOADED_SOURCE_SHA256')):
            with self.subTest(field=field), patch.object(module, field, '0'*64), self.assertRaises(IntegrityError):
                self.worker.validate_manifest(manifest, loaded=True)

    def test_manifest_freezes_all_native_inputs_and_retains_exclusion(self):
        manifest = self.worker.build_manifest(Path('/tmp/no-launch'), [{'id':'one'}])
        self.assertEqual(manifest['executor'], 'harbor-fixture-v1')
        self.assertEqual(manifest['admission'], 'excluded_fixture')
        self.assertIsNone(manifest['duration_request'])
        self.assertIsNone(manifest['experiment_time_cap_seconds'])
        self.assertEqual(set(manifest['asset_sha256']), {'subject.py','test.sh','docker-compose.yaml'})
        self.assertFalse(manifest['continuation_supported'])
        self.assertFalse(manifest['replacement_supported'])


@unittest.skipUnless(os.environ.get('AGENTTIME_NATIVE_DOCKER') == '1', 'opt-in real Docker qualification')
class DurableHarborTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.worker = importlib.import_module('agenttime.harbor_worker')
        from agenttime import fixture
        self.controller = fixture
        output = os.environ.get('AGENTTIME_DURABLE_OUTPUT')
        if output:
            self.root = Path(output).resolve() / self._testMethodName
            self.root.mkdir(parents=True, exist_ok=False)
        else:
            temp = tempfile.TemporaryDirectory(prefix='agenttime-durable-harbor-', dir='/tmp')
            self.addCleanup(temp.cleanup)
            self.root = Path(temp.name)
        self.ledger = Ledger(self.dsn)
        self.ledger.initialize(2)

    def create(self, tasks):
        manifest = self.worker.build_manifest(self.root, tasks)
        self.ledger.create_campaign('native', manifest)

    def controller_process(self):
        return subprocess.Popen([sys.executable, '-m', 'agenttime', 'harbor-fixture', 'run', '--campaign','native'],
            env=dict(os.environ, AGENTTIME_DSN=self.dsn), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 90  # Test watchdog only; no subject time limit.
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(.05)
        self.fail('Native fixture condition did not arrive within the test watchdog')

    def released(self):
        return any('prompt_released' in p.read_text() for p in self.root.glob('attempts/*/harbor/trials/*/agent/native-events.jsonl'))

    def run_one(self, mode='complete'):
        self.create([{'id':'one','mode':mode}])
        attempt = self.ledger.reserve('native')
        self.assertTrue(self.worker.run_worker(self.ledger, attempt['id']))
        return self.ledger.attempt(attempt['id'])

    def test_controller_restart_and_duplicate_worker_leave_one_native_release(self):
        self.create([{'id':'one','work_seconds':2}])
        process = self.controller_process()
        try:
            self.wait_for(self.released)
        finally:
            process.kill(); process.communicate(timeout=10)
        attempt = self.ledger.attempts('native')[0]
        self.assertFalse(self.worker.run_worker(self.ledger, attempt['id']))
        restarted = self.controller_process()
        stdout, stderr = restarted.communicate(timeout=90)
        self.assertEqual(restarted.returncode, 0, stderr)
        self.assertEqual(json.loads(stdout)['finished'], 1)
        row = self.ledger.attempt(attempt['id'])
        self.assertFalse(row['holds_capacity'])
        self.assertEqual(row['report']['score'], 1)
        self.assertEqual(row['report']['invocations'], 1)
        self.assertEqual(len(self.ledger.attempts('native')), 1)
        events = next(self.root.glob('attempts/*/harbor/trials/*/agent/native-events.jsonl')).read_text()
        self.assertEqual(sum(json.loads(line)['kind']=='prompt_released' for line in events.splitlines()), 1)

    def test_concurrent_cli_workers_consume_one_claim(self):
        self.create([{'id':'one'}])
        attempt = self.ledger.reserve('native')
        env = self.controller._dispatch_environment(self.dsn, 'harbor-fixture')
        workers = [subprocess.Popen([sys.executable, '-m', 'agenttime', 'harbor-fixture', 'worker', '--attempt', attempt['id']],
                   env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
        try:
            for worker in workers:
                stdout, stderr = worker.communicate(timeout=90)
                self.assertEqual(worker.returncode, 0, stderr)
        finally:
            for worker in workers:
                if worker.poll() is None:
                    worker.kill(); worker.communicate(timeout=10)
        self.controller.tick(self.ledger, 'native')
        row = self.ledger.attempt(attempt['id'])
        self.assertEqual(row['state'], 'finished')
        self.assertEqual(row['report']['invocations'], 1)
        self.assertEqual(len(list(self.root.glob('attempts/*/harbor/trials/*/agent/release-witness.txt'))), 1)

    def test_source_drift_after_execution_holds_completion(self):
        from agenttime import harbor_fixture
        self.create([{'id':'one'}])
        attempt = self.ledger.reserve('native')
        original = harbor_fixture.run_case
        digest = self.controller.source_digest
        drifted = False
        async def run_then_drift(*args, **kwargs):
            nonlocal drifted
            result = await original(*args, **kwargs)
            drifted = True
            return result
        with patch.object(harbor_fixture, 'run_case', run_then_drift), patch.object(
                self.controller, 'source_digest', side_effect=lambda: '0'*64 if drifted else digest()):
            self.assertFalse(self.worker.run_worker(self.ledger, attempt['id']))
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])
        self.assertFalse(list(self.root.glob('attempts/*/completion.json')))
        self.assertEqual(len(list(self.root.glob('attempts/*/harbor/trials/*/agent/release-witness.txt'))), 1)

    def test_unavailable_removal_proof_keeps_capacity(self):
        from agenttime import harbor_fixture
        self.create([{'id':'one'}])
        attempt = self.ledger.reserve('native')
        with patch.object(harbor_fixture, '_verify_container_removed', side_effect=RuntimeError('Docker daemon unreachable at removal')):
            self.assertFalse(self.worker.run_worker(self.ledger, attempt['id']))
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])
        self.assertFalse(list(self.root.glob('attempts/*/completion.json')))
        self.assertFalse(self.worker.run_worker(self.ledger, attempt['id']))
        self.assertEqual(len(list(self.root.glob('attempts/*/harbor/trials/*/agent/release-witness.txt'))), 1)

    def test_late_write_does_not_change_terminal_grade_or_native_interval(self):
        row = self.run_one()
        report = row['report']
        self.assertEqual(report['timing_status'], 'valid')
        self.assertEqual(report['score'], 1)
        self.assertTrue(report['late_write_observed'])
        self.assertTrue(report['container_removal_verified'])
        self.assertGreater(report['harbor_agent_seconds'], report['runtime_seconds'])
        self.assertEqual(report['admission'], 'excluded_fixture')
        self.assertIsNone(report['effective_agent_timeout_seconds'])

    def test_native_grader_failure_preserves_timing_and_never_replays(self):
        row = self.run_one('grade_failure')
        self.assertEqual(row['report']['timing_status'], 'valid')
        self.assertIsNone(row['report']['score'])
        self.assertEqual(row['report']['quality_status'], 'unavailable')
        self.controller.tick(self.ledger, 'native')
        self.assertEqual(len(self.ledger.attempts('native')), 1)
        self.assertFalse(self.worker.run_worker(self.ledger, row['id']))

    def test_native_crash_is_accounted_for_without_inventing_runtime(self):
        row = self.run_one('crash')
        self.assertEqual(row['state'], 'finished')
        self.assertNotEqual(row['report']['timing_status'], 'valid')
        self.assertIsNone(row['report']['runtime_seconds'])
        self.assertIsNone(row['report']['score'])
        self.assertTrue(row['report']['container_removal_verified'])
        self.assertEqual(row['report']['invocations'], 1)

    def test_ambiguous_dispatch_holds_capacity_while_other_task_finishes(self):
        self.create([{'id':'lost'}, {'id':'okay'}])
        lost = self.ledger.reserve('native')
        report = self.controller.run_campaign(self.ledger, 'native')
        self.assertEqual(report['held'], 1)
        self.assertEqual(report['finished'], 1)
        self.assertEqual(self.ledger.attempt(lost['id'])['state'], 'reserved')
        self.assertFalse((self.root/'attempts'/lost['id']/'invocation.json').exists())

    def test_failed_archive_publication_preserves_local_timing_and_reservation(self):
        self.create([{'id':'one'}])
        attempt = self.ledger.reserve('native')
        with patch.object(EvidenceStore, 'put', side_effect=OSError('injected archive publication failure')):
            self.assertFalse(self.worker.run_worker(self.ledger, attempt['id']))
        row = self.ledger.attempt(attempt['id'])
        self.assertTrue(row['holds_capacity'])
        local = json.loads((self.root/'attempts'/row['id']/'report.json').read_bytes())
        self.assertEqual(local['timing_status'], 'valid')
        self.assertFalse(self.worker.run_worker(self.ledger, row['id']))
        self.assertEqual(len(list(self.root.glob('attempts/*/harbor/trials/*/agent/release-witness.txt'))), 1)

    def test_corrupt_archived_native_evidence_pauses_without_release(self):
        self.create([{'id':'one'}])
        attempt = self.ledger.reserve('native')
        with patch.object(self.ledger, 'finish', return_value=None):
            self.assertTrue(self.worker.run_worker(self.ledger, attempt['id']))
        folder = self.root/'attempts'/attempt['id']
        receipt = json.loads((folder/'completion.json').read_bytes())
        store = EvidenceStore(self.root/'evidence')
        inventory = json.loads(store.get(receipt['archive_sha256']))
        native = next(digest for name,digest in inventory['files'].items() if name.endswith('native-events.jsonl'))
        (store.root/native).write_bytes(b'corrupted')
        report = self.controller.tick(self.ledger, 'native')
        self.assertIsNotNone(report['paused_reason'])
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])

    def test_worker_death_after_release_is_held_and_other_work_continues(self):
        self.create([{'id':'lost','work_seconds':4}, {'id':'okay'}])
        attempt = self.ledger.reserve('native')
        process = subprocess.Popen([sys.executable,'-m','agenttime','harbor-fixture','worker','--attempt',attempt['id']],
            env=dict(os.environ,AGENTTIME_DSN=self.dsn), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        identity = None
        try:
            self.wait_for(self.released)
            identity = json.loads(next(self.root.glob('attempts/*/harbor/trials/*/agent/network-isolation.json')).read_bytes())
        finally:
            process.kill(); process.wait(timeout=10)
            # Kill/cleanup only this exact disposable fixture's retained container.
            if identity is not None:
                from agenttime.harbor_fixture import _daemon_id, _docker_output
                self.assertEqual(asyncio.run(_daemon_id(identity['endpoint'])), identity['daemon_id'])
                asyncio.run(_docker_output('rm','--force',identity['id'],endpoint=identity['endpoint']))
        report = self.controller.run_campaign(self.ledger,'native')
        self.assertEqual(report['held'], 1)
        self.assertEqual(report['finished'], 1)
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])
        self.assertFalse(self.worker.run_worker(self.ledger,attempt['id']))

    def test_source_drift_during_setup_stops_before_native_prompt_release(self):
        from agenttime import harbor_fixture
        self.create([{'id':'one'}])
        attempt = self.ledger.reserve('native')
        setup = harbor_fixture.FixtureAgent.setup
        digest = self.controller.source_digest
        drifted = False

        async def setup_then_drift(agent, environment):
            nonlocal drifted
            await setup(agent, environment)
            drifted = True

        with patch.object(harbor_fixture.FixtureAgent, 'setup', setup_then_drift), patch.object(
                self.controller, 'source_digest', side_effect=lambda: '0'*64 if drifted else digest()):
            self.assertFalse(self.worker.run_worker(self.ledger, attempt['id']))
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertFalse(list(self.root.glob('attempts/*/harbor/trials/*/agent/release-witness.txt')))
        stop = json.loads(next(self.root.glob('attempts/*/harbor/trials/*/stop-proof.json')).read_bytes())
        self.assertTrue(stop['removed'])

    def test_forged_native_facts_cannot_pass_reconciliation(self):
        self.create([{'id':'one'}])
        attempt = self.ledger.reserve('native')
        with patch.object(self.ledger, 'finish', return_value=None):
            self.assertTrue(self.worker.run_worker(self.ledger, attempt['id']))
        attempt = self.ledger.attempt(attempt['id'])
        folder = self.root/'attempts'/attempt['id']
        receipt = json.loads((folder/'completion.json').read_bytes())
        store = EvidenceStore(self.root/'evidence')
        inventory = json.loads(store.get(receipt['archive_sha256']))
        original = {name:store.get(digest) for name,digest in inventory['files'].items()}
        event_path = next(name for name in original if name.endswith('native-events.jsonl'))
        identity_path = next(name for name in original if name.endswith('network-isolation.json'))
        stop_path = next(name for name in original if name.endswith('stop-proof.json'))
        trusted_identity_path = next(name for name in original if name.endswith('container-identity.json'))
        result_path = next(name for name in original if name.endswith('/result.json'))
        for change in ('prompt','owner','clock','extra_release','daemon','project','stop_owner','isolation_owner','stop_project','image','asset','time_cap','grade','effective_verifier','effective_compose','missing_effective_verifier','bad_endpoint_type','bad_phase_type','bad_rewards_type','task_config','task_path','asset_path','consistent_daemon','missing_trusted_identity','boundary_daemon'):
            with self.subTest(change=change):
                files = dict(original)
                if change in {'prompt','owner','clock','extra_release'}:
                    events = [json.loads(line) for line in files[event_path].splitlines()]
                    if change == 'prompt': events[0]['payload']['prompt_sha256'] = '0'*64
                    elif change == 'owner': events[0]['execution_id'] = 'different-owner'
                    elif change == 'clock': events[1]['clock_id'] = 'different-clock'
                    else: events.insert(1, deepcopy(events[0]))
                    files[event_path] = b''.join(canonical_json(e)+b'\n' for e in events)
                elif change == 'daemon':
                    record = json.loads(files[stop_path]); record['daemon_id'] = 'different-daemon'
                    files[stop_path] = canonical_json(record)
                elif change == 'consistent_daemon':
                    for name in (identity_path, stop_path, trusted_identity_path):
                        record = json.loads(files[name]); record['daemon_id'] = 'other-consistent-daemon'
                        files[name] = canonical_json(record)
                elif change == 'missing_trusted_identity':
                    files.pop(trusted_identity_path)
                elif change == 'boundary_daemon':
                    name = 'harbor/complete-report.json'
                    record = json.loads(files[name]); record['docker_daemon_id'] = 'wrong-daemon'
                    files[name] = canonical_json(record)
                elif change == 'project':
                    record = json.loads(files[identity_path]); record['labels']['com.docker.compose.project'] = 'unrelated-project'
                    files[identity_path] = canonical_json(record)
                elif change in {'stop_owner','stop_project','image','isolation_owner'}:
                    name = identity_path if change == 'isolation_owner' else stop_path
                    record = json.loads(files[name])
                    if change == 'stop_project': record['compose_project'] = 'other'
                    elif change == 'image': record['image_id'] = 'sha256:' + '0'*64
                    else: record['execution_id'] = 'other-worker'
                    files[name] = canonical_json(record)
                elif change in {'effective_verifier','effective_compose','missing_effective_verifier'}:
                    name = 'harbor/task-complete/tests/test.sh' if change != 'effective_compose' else 'harbor/task-complete/environment/docker-compose.yaml'
                    if change == 'missing_effective_verifier': files.pop(name)
                    else: files[name] += b'\n# edited after inputs froze\n'
                elif change == 'bad_endpoint_type':
                    record = json.loads(files[stop_path]); record['endpoint'] = 42
                    files[stop_path] = canonical_json(record)
                elif change in {'task_path','asset_path','bad_rewards_type'}:
                    record = json.loads(files[result_path])
                    if change == 'task_path': record['config']['task']['path'] = '/tmp/other-task'
                    elif change == 'asset_path': record['config']['agent']['kwargs']['assets_dir'] = '/tmp/other-assets'
                    else: record['verifier_result']['rewards'] = 42
                    files[result_path] = canonical_json(record)
                elif change == 'task_config':
                    name='harbor/task-complete/task.toml'; files[name] += b'\n[agent]\ntimeout_sec=60\n'
                elif change == 'bad_phase_type':
                    record = json.loads(files[result_path]); record['agent_execution']['started_at'] = 42
                    files[result_path] = canonical_json(record)
                elif change == 'asset': files['sources/subject.py'] += b'\n# unexpected change\n'
                else:
                    record = json.loads(files[result_path])
                    if change == 'time_cap': record['config']['agent']['max_timeout_sec'] = 60
                    else: record['verifier_result']['rewards']['reward'] = 0
                    files[result_path] = canonical_json(record)
                with self.assertRaises(IntegrityError):
                    self.worker.derive_report(self.ledger.campaign('native'), attempt, files, receipt['archive_sha256'])
                if change == 'bad_endpoint_type':
                    altered = dict(inventory, files={name:store.put(data) for name,data in files.items()})
                    changed_receipt = dict(receipt, archive_sha256=store.put(canonical_json(altered)))
                    (folder/'completion.json').write_bytes(canonical_json(changed_receipt))
                    observed = self.controller.tick(self.ledger, 'native')
                    self.assertIsNotNone(observed['paused_reason'])
                    self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])


@unittest.skipUnless(HARBOR_INSTALLED, 'optional pinned Harbor runtime required')
class HarborLedgerTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.worker = importlib.import_module('agenttime.harbor_worker')
        self.ledger = Ledger(self.dsn); self.ledger.initialize(2)
        temp = tempfile.TemporaryDirectory(prefix='agenttime-native-pins-', dir='/tmp')
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)

    def test_changed_source_is_rejected_before_any_native_invocation(self):
        from agenttime import fixture
        self.ledger.create_campaign('native', self.worker.build_manifest(self.root,[{'id':'one'}]))
        attempt = self.ledger.reserve('native')
        with patch.object(fixture,'source_digest',return_value='0'*64):
            self.assertFalse(self.worker.run_worker(self.ledger,attempt['id']))
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertFalse(list(self.root.glob('attempts/*/invocation.json')))
        self.assertFalse(self.worker.run_worker(self.ledger,attempt['id']))

    def test_imported_boundary_drift_is_rejected_before_native_invocation(self):
        from agenttime import harbor_fixture
        self.ledger.create_campaign('native', self.worker.build_manifest(self.root,[{'id':'one'}]))
        attempt = self.ledger.reserve('native')
        with patch.object(harbor_fixture, '_LOADED_MODULE_SHA256', '0'*64), patch.object(
                harbor_fixture, 'run_case', side_effect=AssertionError('Native launch must not be reached')):
            self.assertFalse(self.worker.run_worker(self.ledger,attempt['id']))
        self.assertIsNotNone(self.ledger.gate()['paused_reason'])
        self.assertFalse(list(self.root.glob('attempts/*/invocation.json')))

    def test_excessively_nested_committed_receipt_pauses_and_holds(self):
        from agenttime import fixture
        self.ledger.create_campaign('native', self.worker.build_manifest(self.root,[{'id':'one'}]))
        attempt = self.ledger.reserve('native')
        self.ledger.claim(attempt['id'], 'owned-worker')
        folder = self.root/'attempts'/attempt['id']; folder.mkdir(parents=True)
        (folder/'completion.json').write_bytes(b'['*2000+b'0'+b']'*2000)
        observed = fixture.tick(self.ledger, 'native')
        self.assertIsNotNone(observed['paused_reason'])
        self.assertTrue(self.ledger.attempt(attempt['id'])['holds_capacity'])

    def test_unreachable_docker_preflight_reserves_no_task(self):
        from agenttime import fixture
        self.ledger.create_campaign('native', self.worker.build_manifest(self.root,[{'id':'one'},{'id':'two'}]))
        with patch.dict(os.environ, {'DOCKER_HOST':'unix:///tmp/unreachable-fixture.sock'}), patch.object(
                fixture.subprocess, 'check_output', side_effect=subprocess.CalledProcessError(1, 'docker info')), patch.object(
                fixture.subprocess, 'Popen', side_effect=OSError('Native launch must not be reached')):
            observed = fixture.tick(self.ledger, 'native')
        self.assertIsNotNone(observed['paused_reason'])
        self.assertEqual(self.ledger.attempts('native'), [])

    def test_model_or_command_cannot_be_inserted_into_a_harbor_manifest(self):
        manifest = self.worker.build_manifest(self.root,[{'id':'one'}])
        for change in ('model','task_command','admission','cap','executor'):
            with self.subTest(change=change), self.assertRaises(ValueError):
                altered = deepcopy(manifest)
                if change == 'model': altered['model'] = 'real-model'
                elif change == 'task_command': altered['tasks'][0]['command'] = 'arbitrary'
                elif change == 'admission': altered['admission'] = 'study'
                elif change == 'cap': altered['experiment_time_cap_seconds'] = 60
                else: altered['executor'] = 'harbor'
                self.ledger.create_campaign(change, altered)
        self.assertEqual(self.ledger.attempts('native'), [])
