"""Native boundary failures never become timing or Docker removal evidence."""
import asyncio
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


class HarborBoundaryTests(unittest.TestCase):
    def setUp(self):
        try:
            self.boundary = importlib.import_module('agenttime.harbor_fixture')
        except ModuleNotFoundError as error:
            if error.name == 'harbor' or error.name.startswith('harbor.'):
                self.skipTest('Optional Harbor runtime not installed')
            raise
        self.assertTrue(callable(getattr(self.boundary, 'run_case', None)),
                        'The durable native boundary run_case is missing')

    def test_phase_evidence_preserves_harbor_timestamps_and_absent_phase(self):
        from harbor.models.trial.result import TimingInfo
        phase = TimingInfo(started_at=datetime(2026, 10, 9, 9, 44, 33, 123456, tzinfo=timezone.utc),
                           finished_at=datetime(2026, 10, 9, 9, 44, 34, 123456, tzinfo=timezone.utc))
        result = SimpleNamespace(environment_setup=phase, agent_setup=phase,
                                 agent_execution=phase, verifier=None)
        projected = self.boundary._phases(result)
        self.assertEqual(projected['environment_setup'], {
            'started_at': '2026-10-09T09:44:33.123456Z',
            'finished_at': '2026-10-09T09:44:34.123456Z'})
        self.assertIsNone(projected['verifier'])

    def identity(self):
        return {'id': 'a' * 64, 'network_mode': 'none', 'running': True,
                'labels': {'com.docker.compose.project': 'at11-owned',
                           'com.docker.compose.service': 'main'},
                'image': self.boundary.IMAGE, 'image_id': 'sha256:' + 'b' * 64,
                'attempt_id': 'attempt-one', 'execution_id': 'worker-one',
                'endpoint': 'unix:///tmp/owned-docker.sock',
                'daemon_id': 'owned-daemon'}

    def _run_case_with_subject_evidence(self, root, mutate, *, removal_unavailable=False):
        """Keep real setup, receipts, and evidence validation; replace only external operations."""
        boundary = self.boundary
        identity = self.identity()
        identity['labels']['com.docker.compose.project'] = 'at11-crash-attempt-one'
        event = {'schema_version': 1, 'attempt_id': 'attempt-one', 'execution_id': 'worker-one',
                 'event_id': 'release-one', 'sequence': 1, 'clock_id': 'fixture-clock',
                 'monotonic_ns': 1000, 'recorded_at': '2026-10-09T09:44:33Z',
                 'kind': 'prompt_released', 'payload': {
                     'prompt_sha256': hashlib.sha256(boundary.PROMPT).hexdigest(), 'fixture_mode': 'crash'}}

        async def docker(*arguments, endpoint=None):
            if arguments == ('info', '--format', '{{.ID}}'):
                return 'owned-daemon'
            if arguments[0] == 'inspect':
                return json.dumps({key: value for key, value in identity.items()
                                   if key not in {'attempt_id', 'execution_id', 'endpoint', 'daemon_id'}})
            if arguments[0] == 'ps':
                if removal_unavailable:
                    raise RuntimeError('Docker daemon unavailable')
                return ''
            raise AssertionError(f'Unexpected external Docker operation: {arguments}')

        class ExternalTrial:
            _agent_timeout_sec = None

            def __init__(self, config):
                self.logs = config.trials_dir / config.trial_name / 'agent'
                self.logs.mkdir(parents=True)
                self.agent = boundary.FixtureAgent(logs_dir=self.logs, **config.agent.kwargs)
                self.environment = SimpleNamespace(
                    session_id=config.trial_name,
                    _run_docker_compose_command=AsyncMock(return_value=SimpleNamespace(stdout='a' * 64)),
                    upload_file=AsyncMock())
                self.hooks = []

            def add_hook(self, event_kind, callback):
                self.hooks.append(callback)

            async def run(self):
                await self.agent.setup(self.environment)
                for hook in self.hooks:
                    await hook(SimpleNamespace())
                (self.logs / 'release-witness.txt').write_text('attempt-one\n')
                (self.logs / 'native-events.jsonl').write_text(json.dumps(event) + '\n')
                mutate(self.logs)
                return SimpleNamespace(environment_setup=None, agent_setup=None, agent_execution=None,
                                       verifier=None, verifier_result=None,
                                       exception_info=SimpleNamespace(exception_type='RuntimeError'))

        async def create(config):
            return ExternalTrial(config)

        with patch.object(boundary, '_resolve_docker_endpoint', AsyncMock(return_value=identity['endpoint'])), \
                patch.object(boundary, '_docker_output', docker), patch.object(boundary.Trial, 'create', create):
            return asyncio.run(boundary.run_case(root, 'crash', boundary.ASSETS,
                                                  'attempt-one', 'worker-one'))

    def test_subject_identity_tampering_keeps_original_removal_proof_and_fails_integrity(self):
        def mutate_id(logs):
            path = logs / 'network-isolation.json'
            forged = json.loads(path.read_bytes())
            forged['id'] = 'c' * 64
            path.write_text(json.dumps(forged))

        mutations = {
            'foreign_id': mutate_id,
            'malformed': lambda logs: (logs / 'network-isolation.json').write_bytes(b'not json'),
            'missing': lambda logs: (logs / 'network-isolation.json').unlink(),
        }
        for name, mutation in mutations.items():
            with self.subTest(mutation=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                failure = None
                try:
                    self._run_case_with_subject_evidence(root, mutation)
                except Exception as error:
                    failure = error
                self.assertIsInstance(failure, self.boundary.IntegrityError)
                trial = root / 'trials/at11-crash-attempt-one'
                observed = json.loads((trial / 'container-identity.json').read_bytes())
                proof = json.loads((trial / 'stop-proof.json').read_bytes())
                self.assertEqual(observed['id'], 'a' * 64)
                self.assertEqual(proof['container_id'], 'a' * 64)
                self.assertIs(proof['removed'], True)
                self.assertFalse((root / 'crash-report.json').exists())

    def test_multiple_release_witness_is_an_integrity_failure_after_stop_proof(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            failure = None
            try:
                self._run_case_with_subject_evidence(root, lambda logs:
                    (logs / 'release-witness.txt').write_text('attempt-one\nattempt-one\n'))
            except Exception as error:
                failure = error
            self.assertIsInstance(failure, self.boundary.IntegrityError)
            proof = json.loads((root / 'trials/at11-crash-attempt-one/stop-proof.json').read_bytes())
            self.assertEqual(proof['container_id'], 'a' * 64)
            self.assertIs(proof['removed'], True)
            self.assertFalse((root / 'crash-report.json').exists())

    def test_foreign_or_duplicate_release_events_are_integrity_failures(self):
        def rewrite(logs, mode):
            path = logs / 'native-events.jsonl'
            event = json.loads(path.read_text())
            if mode == 'foreign':
                event['execution_id'] = 'another-worker'
                path.write_text(json.dumps(event) + '\n')
            elif mode == 'duplicate':
                duplicate = dict(event, event_id='release-two', sequence=2, monotonic_ns=2000)
                path.write_text(json.dumps(event) + '\n' + json.dumps(duplicate) + '\n')
            else:
                event['payload']['prompt_sha256'] = 'd' * 64
                path.write_text(json.dumps(event) + '\n')

        for mode in ('foreign', 'duplicate', 'wrong_prompt'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                failure = None
                try:
                    self._run_case_with_subject_evidence(root, lambda logs: rewrite(logs, mode))
                except Exception as error:
                    failure = error
                self.assertIsInstance(failure, self.boundary.IntegrityError)
                self.assertTrue((root / 'trials/at11-crash-attempt-one/stop-proof.json').is_file())
                self.assertFalse((root / 'crash-report.json').exists())

    def test_docker_unavailability_is_not_itself_an_evidence_contradiction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            failure = None
            try:
                self._run_case_with_subject_evidence(root, lambda logs: None, removal_unavailable=True)
            except Exception as error:
                failure = error
            self.assertIsInstance(failure, RuntimeError)
            self.assertNotIsInstance(failure, self.boundary.IntegrityError)
            self.assertFalse((root / 'trials/at11-crash-attempt-one/stop-proof.json').exists())
            self.assertFalse((root / 'crash-report.json').exists())

    def test_invalid_modes_or_delays_are_rejected_before_touching_docker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for mode, delay in [('other', .15), ('complete', True), ('complete', -1),
                                ('complete', 5.1), ('complete', float('nan')),
                                ('complete', float('inf'))]:
                with self.subTest(mode=mode, delay=delay), self.assertRaises(ValueError):
                    asyncio.run(self.boundary.run_case(root, mode, self.boundary.ASSETS,
                                                      'attempt-one', 'worker-one', delay))
            self.assertEqual(list(root.iterdir()), [])

    def test_prefix_sharing_attempts_have_distinct_full_owner_trial_names(self):
        configs = []

        async def stop_before_launch(config):
            configs.append(config)
            raise RuntimeError('Stop at the native launch boundary')

        attempts = ['00000000-0000-4000-8000-000000000001',
                    '00000000-0000-4000-8000-000000000002']
        with tempfile.TemporaryDirectory() as folder, patch.object(
                self.boundary, '_resolve_docker_endpoint',
                AsyncMock(return_value='unix:///tmp/owned.sock')), patch.object(
                self.boundary, '_daemon_id', AsyncMock(return_value='owned-daemon')), patch.object(
                self.boundary.Trial, 'create', stop_before_launch):
            for attempt in attempts:
                with self.assertRaisesRegex(RuntimeError, 'Stop at the native launch boundary'):
                    asyncio.run(self.boundary.run_case(Path(folder) / attempt, 'complete',
                                                       self.boundary.ASSETS, attempt, 'worker-one'))
        self.assertEqual([config.trial_name for config in configs],
                         ['at11-complete-' + attempt for attempt in attempts])
        self.assertNotEqual(configs[0].trial_name, configs[1].trial_name)

    def test_remote_docker_host_is_rejected_before_launch(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://remote.example:2375'}):
            with self.assertRaisesRegex(ValueError, 'local Unix'):
                asyncio.run(self.boundary._resolve_docker_endpoint())

    def test_frozen_environment_overrides_context_and_restores_caller(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'unix:///tmp/original.sock',
                                     'DOCKER_CONTEXT': 'some-other-daemon'}):
            with self.boundary._frozen_docker_environment('unix:///tmp/owned.sock'):
                self.assertEqual(os.environ['DOCKER_HOST'], 'unix:///tmp/owned.sock')
                self.assertNotIn('DOCKER_CONTEXT', os.environ)
            self.assertEqual(os.environ['DOCKER_HOST'], 'unix:///tmp/original.sock')
            self.assertEqual(os.environ['DOCKER_CONTEXT'], 'some-other-daemon')

    def test_missing_container_on_changed_daemon_is_not_stop_proof(self):
        # Docker is external. Only its read commands are replaced; receipt validation is real.
        for responses in [['different-daemon'], ['owned-daemon', '', 'different-daemon'],
                          [RuntimeError('daemon unreachable')]]:
            with self.subTest(responses=responses), patch.object(
                    self.boundary, '_docker_output', AsyncMock(side_effect=responses)):
                with self.assertRaises((ValueError, RuntimeError)):
                    asyncio.run(self.boundary._verify_container_removed(self.identity()))

    def test_existing_owned_container_is_not_stop_proof(self):
        with patch.object(self.boundary, '_docker_output', AsyncMock(
                side_effect=['owned-daemon', 'a' * 64])):
            with self.assertRaisesRegex(ValueError, 'remove'):
                asyncio.run(self.boundary._verify_container_removed(self.identity()))

    def test_stop_proof_requires_full_owned_identity(self):
        for change in ({'id': 'a' * 12}, {'network_mode': 'bridge'},
                       {'image': 'python:latest'}, {'labels': {'com.docker.compose.service': 'other'}},
                       {'endpoint': 'tcp://remote.example:2375'}):
            identity = self.identity()
            identity.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                asyncio.run(self.boundary._verify_container_removed(identity))

    def test_stop_proof_binds_exact_daemon_endpoint_and_container(self):
        expected_endpoint = 'unix:///tmp/owned-docker.sock'

        async def docker(*arguments, endpoint=None):
            self.assertEqual(endpoint, expected_endpoint)
            if arguments == ('info', '--format', '{{.ID}}'):
                return 'owned-daemon'
            self.assertEqual(arguments, ('ps', '--all', '--no-trunc', '--quiet',
                                         '--filter', 'id=' + 'a' * 64))
            return ''

        with patch.object(self.boundary, '_docker_output', docker):
            receipt = asyncio.run(self.boundary._verify_container_removed(self.identity()))
        self.assertEqual(receipt['container_id'], 'a' * 64)
        self.assertEqual(receipt['endpoint'], expected_endpoint)
        self.assertEqual(receipt['daemon_id'], 'owned-daemon')
        self.assertEqual(receipt['attempt_id'], 'attempt-one')
        self.assertEqual(receipt['execution_id'], 'worker-one')
        self.assertIs(receipt['removed'], True)


@unittest.skipUnless(os.environ.get('AGENTTIME_NATIVE_DOCKER') == '1',
                     'requires explicit local Docker qualification')
class HarborBoundaryNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.boundary = importlib.import_module('agenttime.harbor_fixture')
        if not callable(getattr(cls.boundary, 'run_case', None)):
            raise AssertionError('The durable native boundary run_case is missing')
        cls.temp = None
        if os.environ.get('AGENTTIME_BOUNDARY_OUTPUT'):
            cls.root = Path(os.environ['AGENTTIME_BOUNDARY_OUTPUT']).resolve()
            cls.root.mkdir(parents=True, exist_ok=False)
        else:
            cls.temp = tempfile.TemporaryDirectory(prefix='agenttime-boundary-', dir='/tmp')
            cls.root = Path(cls.temp.name)

        async def run():
            return {mode: await cls.boundary.run_case(cls.root, mode, cls.boundary.ASSETS,
                                                      'attempt-' + mode, 'worker-' + mode, .1)
                    for mode in ('complete', 'wrong_answer', 'grade_failure', 'crash')}
        cls.reports = asyncio.run(run())

    @classmethod
    def tearDownClass(cls):
        if cls.temp:
            cls.temp.cleanup()

    def paths(self, mode):
        report = self.reports[mode]
        trial = self.root / 'trials' / report['trial_name']
        return report, trial, trial / 'agent'

    def test_complete_binds_supplied_ids_terminal_bytes_and_monotonic_timing(self):
        report, trial, logs = self.paths('complete')
        events = [json.loads(line) for line in (logs / 'native-events.jsonl').read_bytes().splitlines()]
        self.assertEqual([event['kind'] for event in events],
                         ['prompt_released', 'native_terminal', 'owned_work_drained', 'journal_closed'])
        self.assertEqual({event['attempt_id'] for event in events}, {'attempt-complete'})
        self.assertEqual({event['execution_id'] for event in events}, {'worker-complete'})
        self.assertEqual((logs / 'release-witness.txt').read_text(), 'attempt-complete\n')
        self.assertEqual(report['prompt_sha256'], hashlib.sha256(
            b'Submit the answer 42. This is a disposable model-free fixture.\n').hexdigest())
        self.assertEqual(report['execution_id'], 'worker-complete')
        self.assertEqual((logs / 'sealed-submission.bin').read_bytes(), b'42')
        self.assertEqual((logs / 'working-answer.txt').read_bytes(), b'late overwrite')
        self.assertEqual(report['timing_status'], 'valid')
        self.assertEqual(report['score'], 1)
        self.assertEqual(report['quality_status'], 'available')
        self.assertGreaterEqual(report['runtime_seconds'], .1)
        self.assertGreater(report['harbor_agent_seconds'], report['runtime_seconds'])
        self.assertIsNone(report['effective_agent_timeout_seconds'])
        self.assertEqual(set(report['harbor_phases']),
                         {'environment_setup', 'agent_setup', 'agent_execution', 'verifier'})
        native_result = json.loads((trial / 'result.json').read_bytes())
        self.assertEqual(report['harbor_phases'], {name: native_result[name] for name in report['harbor_phases']})
        for phase in report['harbor_phases'].values():
            self.assertIsNotNone(phase['started_at'])
            self.assertIsNotNone(phase['finished_at'])
        isolation = json.loads((logs / 'network-isolation.json').read_bytes())
        self.assertEqual(json.loads((trial / 'container-identity.json').read_bytes()), isolation)
        proof = json.loads((trial / 'stop-proof.json').read_bytes())
        self.assertEqual(len(proof['container_id']), 64)
        self.assertEqual(proof['container_id'], report['container_id'])
        self.assertEqual(proof['daemon_id'], isolation['daemon_id'])
        self.assertEqual(proof['endpoint'], isolation['endpoint'])
        self.assertIs(proof['removed'], True)

    def test_native_subject_identity_replacement_cannot_change_removal_authority(self):
        original = self.boundary.FixtureAgent.run

        async def replace_visible_identity(agent, instruction, environment, context):
            await original(agent, instruction, environment, context)
            changed = await environment.exec("""python3 - <<'PY'
import json
from pathlib import Path
assert not Path('/logs/container-identity.json').exists()
p = Path('/logs/agent/network-isolation.json')
identity = json.loads(p.read_bytes())
identity['id'] = 'c' * 64
p.write_text(json.dumps(identity))
PY""", timeout_sec=None)
            self.assertEqual(changed.return_code, 0)

        root = self.root / 'identity-tamper'
        with patch.object(self.boundary.FixtureAgent, 'run', replace_visible_identity):
            with self.assertRaises(self.boundary.IntegrityError):
                asyncio.run(self.boundary.run_case(root, 'complete', self.boundary.ASSETS,
                                                  'attempt-tamper', 'worker-tamper'))
        trial = root / 'trials/at11-complete-attempt-tamper'
        trusted = json.loads((trial / 'container-identity.json').read_bytes())
        visible = json.loads((trial / 'agent/network-isolation.json').read_bytes())
        proof = json.loads((trial / 'stop-proof.json').read_bytes())
        self.assertEqual(visible['id'], 'c' * 64)
        self.assertNotEqual(trusted['id'], visible['id'])
        self.assertEqual(proof['container_id'], trusted['id'])
        self.assertIs(proof['removed'], True)
        self.assertFalse((root / 'complete-report.json').exists())

    def test_native_duplicate_release_witness_keeps_stop_proof_but_fails_integrity(self):
        original = self.boundary.FixtureAgent.run

        async def duplicate_witness(agent, instruction, environment, context):
            await original(agent, instruction, environment, context)
            changed = await environment.exec("""python3 - <<'PY'
from pathlib import Path
p = Path('/logs/agent/release-witness.txt')
p.write_bytes(p.read_bytes() * 2)
PY""", timeout_sec=None)
            self.assertEqual(changed.return_code, 0)

        root = self.root / 'witness-tamper'
        with patch.object(self.boundary.FixtureAgent, 'run', duplicate_witness):
            with self.assertRaises(self.boundary.IntegrityError):
                asyncio.run(self.boundary.run_case(root, 'complete', self.boundary.ASSETS,
                                                  'attempt-witness', 'worker-witness'))
        trial = root / 'trials/at11-complete-attempt-witness'
        proof = json.loads((trial / 'stop-proof.json').read_bytes())
        self.assertIs(proof['removed'], True)
        self.assertEqual((trial / 'agent/release-witness.txt').read_text(),
                         'attempt-witness\nattempt-witness\n')
        self.assertFalse((root / 'complete-report.json').exists())

    def test_wrong_answer_has_real_zero_without_losing_timing(self):
        report, _, logs = self.paths('wrong_answer')
        self.assertEqual(report['score'], 0)
        self.assertEqual(report['quality_status'], 'available')
        self.assertEqual(report['timing_status'], 'valid')
        self.assertEqual((logs / 'sealed-submission.bin').read_bytes(), b'41')
        self.assertIsNone(report['exception_type'])

    def test_grader_failure_does_not_rerun_or_discard_native_timing(self):
        report, trial, _ = self.paths('grade_failure')
        self.assertIsNone(report['score'])
        self.assertEqual(report['quality_status'], 'unavailable')
        self.assertEqual(report['timing_status'], 'valid')
        self.assertEqual(report['invocations'], 1)
        self.assertIsNotNone(report['exception_type'])
        self.assertFalse((trial / 'verifier' / 'reward.txt').exists())
        self.assertTrue(report['container_removal_verified'])

    def test_crash_retains_only_release_and_no_natural_duration(self):
        report, _, logs = self.paths('crash')
        events = [json.loads(line) for line in (logs / 'native-events.jsonl').read_bytes().splitlines()]
        self.assertEqual([event['kind'] for event in events], ['prompt_released'])
        self.assertNotEqual(report['timing_status'], 'valid')
        self.assertIsNone(report['runtime_seconds'])
        self.assertIsNone(report['score'])
        self.assertEqual(report['invocations'], 1)
        self.assertTrue(report['container_removal_verified'])
