"""Opt-in native Harbor boundary probe. No model or PostgreSQL campaign is launched."""
import asyncio
import base64
from contextlib import contextmanager
import hashlib
from importlib.metadata import version
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import threading
from urllib.parse import urlsplit
from uuid import uuid4

from harbor.agents.base import BaseAgent
from harbor.environments.docker.docker import _sanitize_docker_compose_project_name
from harbor.models.trial.config import AgentConfig, EnvironmentConfig, TaskConfig, TrialConfig
from harbor.trial.hooks import TrialEvent
from harbor.trial.trial import Trial

from .evidence import IntegrityError, canonical_json
from .fixture import _read_json, _write_once
from .jsonio import loads_strict
from .measurement import project_events

IMAGE = 'python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea'
PROMPT = b'Submit the answer 42. This is a disposable model-free fixture.\n'
ASSETS = Path(__file__).resolve().parents[2] / 'qualification' / 'harbor'
_MODES = {'complete', 'crash', 'grade_failure', 'wrong_answer'}
_DOCKER_ENVIRONMENT_LOCK = threading.Lock()
_LOADED_MODULE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _validate_case(mode, attempt_id, execution_id, work_seconds):
    if (type(mode) is not str or mode not in _MODES
            or type(work_seconds) not in {int, float} or not math.isfinite(work_seconds)
            or not 0 <= work_seconds <= 5):
        raise ValueError('Only bounded disposable model-free fixture modes are supported')
    for identity in (attempt_id, execution_id):
        if type(identity) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', identity) is None:
            raise ValueError('A safe supplied attempt and execution identity is required')


def _local_endpoint(endpoint):
    if type(endpoint) is not str or any(character.isspace() for character in endpoint):
        raise ValueError('Qualification requires a local Unix Docker socket')
    parsed = urlsplit(endpoint)
    if (parsed.scheme != 'unix' or parsed.netloc or not parsed.path.startswith('/')
            or parsed.query or parsed.fragment or '\x00' in endpoint):
        raise ValueError('Qualification requires a local Unix Docker socket')
    return endpoint


async def _docker_output(*arguments, endpoint=None):
    env = os.environ.copy()
    if endpoint is not None:
        env['DOCKER_HOST'] = _local_endpoint(endpoint)
        for name in ('DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
            env.pop(name, None)
    process = await asyncio.create_subprocess_exec(
        'docker', *arguments, env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f'Docker inspection failed: {stderr.decode().strip()}')
    return stdout.decode().strip()


async def _resolve_docker_endpoint():
    endpoint = os.environ.get('DOCKER_HOST')
    if not endpoint:
        context = await _docker_output('context', 'show')
        endpoint = await _docker_output('context', 'inspect', context, '--format', '{{.Endpoints.docker.Host}}')
    return _local_endpoint(endpoint)


@contextmanager
def _frozen_docker_environment(endpoint):
    """Harbor Compose inherits this fixed endpoint for its entire Trial lifecycle."""
    endpoint = _local_endpoint(endpoint)
    if not _DOCKER_ENVIRONMENT_LOCK.acquire(blocking=False):
        raise RuntimeError('A fixture Trial already owns the process Docker environment')
    names = ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ['DOCKER_HOST'] = endpoint
        for name in names[1:]:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        _DOCKER_ENVIRONMENT_LOCK.release()


async def _daemon_id(endpoint):
    identity = await _docker_output('info', '--format', '{{.ID}}', endpoint=endpoint)
    if not identity or re.fullmatch(r'[A-Za-z0-9:_-]+', identity) is None:
        raise ValueError('Docker daemon identity is unavailable')
    return identity


def _validate_isolation(identity):
    labels = identity.get('labels') or {}
    if (re.fullmatch(r'[a-f0-9]{64}', str(identity.get('id'))) is None
            or re.fullmatch(r'sha256:[a-f0-9]{64}', str(identity.get('image_id'))) is None
            or identity.get('network_mode') != 'none' or identity.get('running') is not True
            or type(labels) is not dict or labels.get('com.docker.compose.service') != 'main'
            or re.fullmatch(r'[a-z0-9][a-z0-9_-]*', str(labels.get('com.docker.compose.project'))) is None
            or identity.get('image') != IMAGE or not identity.get('daemon_id')):
        raise ValueError('Fixture container identity or isolation is not verified')
    _local_endpoint(identity.get('endpoint'))
    _validate_case('complete', identity.get('attempt_id'), identity.get('execution_id'), 0)


async def _verify_container_removed(identity):
    """Absence counts only on the daemon where this full owned ID was observed."""
    _validate_isolation(identity)
    endpoint, daemon_id = identity['endpoint'], identity['daemon_id']
    if await _daemon_id(endpoint) != daemon_id:
        raise ValueError('Docker daemon changed before the removal check')
    remaining = await _docker_output('ps', '--all', '--no-trunc', '--quiet', '--filter',
                                     f"id={identity['id']}", endpoint=endpoint)
    if remaining:
        raise ValueError('Harbor did not remove its exact fixture container')
    if await _daemon_id(endpoint) != daemon_id:
        raise ValueError('Docker daemon changed during the removal check')
    return {'schema_version': 1, 'attempt_id': identity['attempt_id'],
            'execution_id': identity['execution_id'], 'endpoint': endpoint, 'daemon_id': daemon_id,
            'container_id': identity['id'], 'image': identity['image'], 'image_id': identity['image_id'],
            'compose_project': identity['labels']['com.docker.compose.project'],
            'compose_service': identity['labels']['com.docker.compose.service'],
            'network_mode': identity['network_mode'], 'removed': True}


class FixtureAgent(BaseAgent):
    def __init__(self, *args, mode='complete', attempt_id=None, execution_id=None,
                 work_seconds=.15, assets_dir=None, endpoint=None, daemon_id=None, **kwargs):
        _validate_case(mode, attempt_id, execution_id, work_seconds)
        if not assets_dir or not daemon_id or kwargs.get('model_name') is not None:
            raise ValueError('This agent supports only disposable model-free fixture trials')
        self.mode, self.attempt_id, self.execution_id = mode, attempt_id, execution_id
        self.work_seconds = work_seconds
        self.assets_dir = Path(assets_dir)
        self.endpoint, self.daemon_id = _local_endpoint(endpoint), daemon_id
        self._container_identity_bytes = None
        super().__init__(*args, **kwargs)

    @staticmethod
    def name():
        return 'agenttime-native-fixture'

    def version(self):
        return '1'

    async def setup(self, environment):
        if await _daemon_id(self.endpoint) != self.daemon_id:
            raise ValueError('Docker daemon changed during fixture setup')
        # This pinned private API identifies only this trial's Compose service.
        result = await environment._run_docker_compose_command(['ps', '-q', 'main'])
        containers = (result.stdout or '').splitlines()
        if len(containers) != 1:
            raise ValueError('Expected exactly one owned fixture container')
        # Retain only relevant fields; never copy container environment variables.
        raw = await _docker_output('inspect', '--format',
            '{"id":{{json .Id}},"network_mode":{{json .HostConfig.NetworkMode}},'
            '"running":{{json .State.Running}},"labels":{{json .Config.Labels}},'
            '"image":{{json .Config.Image}},"image_id":{{json .Image}}}', containers[0],
            endpoint=self.endpoint)
        identity = loads_strict(raw)
        identity.update(attempt_id=self.attempt_id, execution_id=self.execution_id,
                        endpoint=self.endpoint, daemon_id=self.daemon_id)
        _validate_isolation(identity)
        if (identity['labels']['com.docker.compose.project'] !=
                _sanitize_docker_compose_project_name(environment.session_id)
                or await _daemon_id(self.endpoint) != self.daemon_id):
            raise ValueError('Fixture container owner or daemon changed during setup')
        # The subject sees only the agent/verifier mounts, never this trial-root receipt.
        # Immutable worker memory remains the authority for all later removal probes.
        self._container_identity_bytes = canonical_json(identity)
        _write_once(self.logs_dir.parent / 'container-identity.json', self._container_identity_bytes)
        _write_once(self.logs_dir / 'network-isolation.json', self._container_identity_bytes)
        await environment.upload_file(self.assets_dir / 'subject.py', '/tmp/agenttime-native-subject.py')

    async def run(self, instruction, environment, context):
        if instruction.encode() != PROMPT:
            raise ValueError('Native fixture prompt differs from its frozen bytes')
        prompt = base64.b64encode(instruction.encode()).decode()
        command = shlex.join(['python3', '/tmp/agenttime-native-subject.py', self.mode,
                              self.attempt_id, self.execution_id, prompt, str(self.work_seconds)])
        result = await environment.exec(command, timeout_sec=None)
        if result.return_code != 0:
            raise RuntimeError(f'Native fixture exited with code {result.return_code}')


def _phases(result):
    phases = {}
    for name in ('environment_setup', 'agent_setup', 'agent_execution', 'verifier'):
        interval = getattr(result, name)
        phases[name] = interval.model_dump(mode='json') if interval else None
    return phases


async def run_case(root, mode, assets, attempt_id, execution_id, work_seconds=.15, *, before_release=None):
    """Run one excluded, model-free Trial with supplied durable worker identities."""
    _validate_case(mode, attempt_id, execution_id, work_seconds)
    if version('harbor') != '0.23.0':
        raise ValueError('Harbor qualification requires the exact pinned version')
    root, assets = Path(root).resolve(), Path(assets).resolve()
    endpoint = await _resolve_docker_endpoint()
    with _frozen_docker_environment(endpoint):
        daemon_id = await _daemon_id(endpoint)
        task = root / f'task-{mode}'
        (task / 'tests').mkdir(parents=True)
        (task / 'environment').mkdir()
        (task / 'instruction.md').write_bytes(PROMPT)
        # Omitting agent.timeout_sec resolves to None in the pinned task schema.
        (task / 'task.toml').write_text(
            'schema_version = "1.4"\n[environment]\n'
            f'docker_image = "{IMAGE}"\ncpus = 1\nmemory_mb = 256\n'
            '[verifier]\ntimeout_sec = 30\n')
        shutil.copyfile(assets / 'test.sh', task / 'tests' / 'test.sh')
        shutil.copyfile(assets / 'docker-compose.yaml', task / 'environment' / 'docker-compose.yaml')
        config = TrialConfig(task=TaskConfig(path=task), trial_name=f'at11-{mode}-{attempt_id}',
                            trials_dir=root / 'trials',
                            agent=AgentConfig(import_path='agenttime.harbor_fixture:FixtureAgent',
                                              kwargs={'mode': mode, 'attempt_id': attempt_id,
                                                      'execution_id': execution_id, 'work_seconds': work_seconds,
                                                      'assets_dir': str(assets), 'endpoint': endpoint,
                                                      'daemon_id': daemon_id}),
                            environment=EnvironmentConfig(type='docker', delete=True))
        frozen_inputs = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            assets / 'subject.py', assets / 'test.sh', assets / 'docker-compose.yaml',
            task / 'instruction.md', task / 'task.toml', task / 'tests' / 'test.sh',
            task / 'environment' / 'docker-compose.yaml')}
        release_error = None

        async def guard_release(event):
            nonlocal release_error
            try:
                if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != _LOADED_MODULE_SHA256:
                    raise IntegrityError('Native boundary module changed before prompt release')
                for path, digest in frozen_inputs.items():
                    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                        raise IntegrityError('Frozen native input changed before prompt release')
                if before_release is not None:
                    before_release()
            except (IntegrityError, OSError) as error:
                release_error = IntegrityError(str(error))
                raise release_error from error

        trial = await Trial.create(config)
        trial.add_hook(TrialEvent.AGENT_START, guard_release)
        resolved_timeout = trial._agent_timeout_sec
        if resolved_timeout is not None:
            raise ValueError('Unexpected native agent timeout in the fixture')
        result = await trial.run()  # One Trial directly; no Job retry or concurrency wrapper.
        # Harbor has completed collection/redaction/finalization. Seal evidence afterwards.
        folder = config.trials_dir / config.trial_name
        agent_logs = folder / 'agent'
        observed_bytes = trial.agent._container_identity_bytes
        if observed_bytes is None:
            raise RuntimeError('Fixture setup did not observe a trusted container identity')
        isolation = loads_strict(observed_bytes)
        identity_error = None
        try:
            if any(isolation.get(key) != expected for key, expected in (
                    ('endpoint', endpoint), ('daemon_id', daemon_id),
                    ('attempt_id', attempt_id), ('execution_id', execution_id))):
                raise IntegrityError('Observed container identity disagrees with its Trial')
            for path in (folder / 'container-identity.json', agent_logs / 'network-isolation.json'):
                if canonical_json(_read_json(path)) != observed_bytes:
                    raise IntegrityError('Retained container identity differs from its trusted observation')
        except (OSError, ValueError, TypeError, RecursionError) as error:
            identity_error = IntegrityError(f'Native container identity evidence is invalid: {error}')
        # Never probe an ID supplied by the subject. Retain removal evidence even
        # when the subject has damaged or replaced its visible identity receipt.
        try:
            stop_proof = await _verify_container_removed(isolation)
            _write_once(folder / 'stop-proof.json', canonical_json(stop_proof))
        except (OSError, ValueError, RuntimeError) as error:
            if identity_error is not None:
                raise identity_error from error
            raise
        if identity_error is not None:
            raise identity_error
    if release_error is not None:
        raise release_error
    event_bytes = (agent_logs / 'native-events.jsonl').read_bytes()
    try:
        events = [loads_strict(line) for line in event_bytes.splitlines()]
        witnesses = (agent_logs / 'release-witness.txt').read_text().splitlines()
    except (ValueError, TypeError, RecursionError) as error:
        raise IntegrityError(f'Native source evidence is malformed: {error}') from error
    if witnesses != [attempt_id]:
        raise IntegrityError('Independent native invocation witness is not exactly one release')
    measurement = project_events(events)
    if measurement['timing_status'] == 'invalid':
        raise IntegrityError(f"Native source history is contradictory: {measurement['reason']}")
    if any(e['attempt_id'] != attempt_id or e['execution_id'] != execution_id for e in events):
        raise IntegrityError('Native event identity disagrees with the trial')
    prompt_sha256 = hashlib.sha256(PROMPT).hexdigest()
    releases = [event for event in events if event['kind'] == 'prompt_released']
    if len(releases) != 1 or releases[0]['payload'] != {'prompt_sha256': prompt_sha256, 'fixture_mode': mode}:
        raise IntegrityError('Native prompt release disagrees with the frozen fixture')
    interval = result.agent_execution
    harbor_seconds = ((interval.finished_at - interval.started_at).total_seconds()
                      if interval and interval.started_at and interval.finished_at else None)
    rewards = result.verifier_result.rewards if result.verifier_result else None
    score = (rewards or {}).get('reward')
    report = {'attempt_id': attempt_id, 'execution_id': execution_id, 'prompt_sha256': prompt_sha256,
              'trial_name': config.trial_name, 'invocations': len(witnesses),
              'timing_status': measurement['timing_status'], 'runtime_seconds': measurement['runtime_seconds'],
              'timing_reason': measurement['reason'], 'harbor_agent_seconds': harbor_seconds,
              'harbor_phases': _phases(result), 'effective_agent_timeout_seconds': resolved_timeout,
              'exception_type': result.exception_info.exception_type if result.exception_info else None,
              'score': score, 'quality_status': 'available' if score is not None else 'unavailable',
              'late_write_observed': ((agent_logs / 'working-answer.txt').read_bytes() == b'late overwrite'
                                      if (agent_logs / 'working-answer.txt').is_file() else False),
              'event_sha256_after_harbor_finalization': hashlib.sha256(event_bytes).hexdigest(),
              'container_id': isolation['id'], 'network_mode': isolation['network_mode'],
              'docker_endpoint': endpoint, 'docker_daemon_id': daemon_id,
              'network_enforcement': 'docker_compose_network_none',
              'container_removal_verified': True, 'admission': 'excluded_fixture'}
    _write_once(root / f'{mode}-report.json', canonical_json(report))
    return report


async def _case(root, mode, assets):
    return await run_case(root, mode, assets, str(uuid4()), str(uuid4()))


def run_qualification(root):
    if version('harbor') != '0.23.0':
        raise ValueError('Harbor qualification requires the exact pinned version')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    sources = root / 'sources'
    sources.mkdir()
    module_path = Path(__file__)
    if hashlib.sha256(module_path.read_bytes()).hexdigest() != _LOADED_MODULE_SHA256:
        raise ValueError('Probe module changed since import; qualification refused')
    inputs = (module_path, ASSETS / 'subject.py', ASSETS / 'test.sh', ASSETS / 'docker-compose.yaml')
    hashes = {}
    for path in inputs:
        data = path.read_bytes()
        hashes[path.name] = hashlib.sha256(data).hexdigest()
        _write_once(sources / path.name, data)
    if hashes[module_path.name] != _LOADED_MODULE_SHA256:
        raise ValueError('Probe module changed while freezing inputs')

    async def execute():
        return {mode: await _case(root, mode, sources) for mode in ('complete', 'crash')}

    cases = asyncio.run(execute())
    if hashlib.sha256(module_path.read_bytes()).hexdigest() != _LOADED_MODULE_SHA256:
        raise ValueError('Probe module changed during qualification')
    for name, digest in hashes.items():
        if hashlib.sha256((sources / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen qualification input changed')
    result = {'schema_version': 1, 'harbor_version': version('harbor'), 'image': IMAGE,
              'study_launch_ready': False, 'postgres_worker_integration_qualified': False,
              'scope': 'standalone_model_free_harbor_boundary_probe', 'cases': cases,
              'source_hashes': hashes}
    _write_once(root / 'qualification.json', canonical_json(result))
    return result
