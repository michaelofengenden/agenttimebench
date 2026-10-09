"""Durable, model-free Harbor qualification. This cannot launch study agents."""
import asyncio
import base64
from datetime import datetime
import hashlib
from importlib.metadata import version
import math
import os
from pathlib import Path, PurePosixPath
import socket
import stat
import tomllib
from uuid import uuid4

import psutil

from . import fixture, harbor_fixture
from .evidence import EvidenceStore, IntegrityError, canonical_json
from .jsonio import loads_strict
from .measurement import project_events

EXECUTOR = 'harbor-fixture-v1'
MODES = {'complete', 'crash', 'grade_failure', 'wrong_answer'}
ASSETS = Path(__file__).resolve().parents[2] / 'qualification' / 'harbor'
PROMPT = b'Submit the answer 42. This is a disposable model-free fixture.\n'
IMAGE = 'python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea'
_LOADED_SOURCE = fixture.source_digest()
_LIMITATIONS = ['model_free_fixture', 'local_evidence_store', 'same_worker_clock_only',
                'no_session_continuation', 'no_replacement_qualification', 'no_study_launch',
                'installed_distribution_contents_not_attested', 'same_pins_required_for_reconciliation']


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _pins():
    lock = Path(__file__).resolve().parents[2] / 'uv.lock'
    runtime = dict(fixture.runtime_identity(), harbor=version('harbor'))
    if runtime['harbor'] != '0.23.0':
        raise ValueError('The native fixture requires Harbor 0.23.0')
    return {'source_sha256': fixture.source_digest(), 'dependency_lock_sha256': _sha(lock.read_bytes()),
            'runtime': runtime, 'asset_sha256': {name: _sha((ASSETS/name).read_bytes())
                         for name in ('subject.py', 'test.sh', 'docker-compose.yaml')}}


def build_manifest(root, tasks, agent_id='harbor-fixture-agent-v1'):
    if not isinstance(tasks, list) or not tasks or not isinstance(agent_id, str) or not agent_id:
        raise ValueError('An agent identity and disposable fixture tasks are required')
    normalized = []
    for task in tasks:
        if (type(task) is not dict or set(task) - {'id', 'mode', 'work_seconds'}
                or type(task.get('id')) is not str or not task['id']):
            raise ValueError('Only disposable Harbor fixture task fields are supported')
        mode, work = task.get('mode', 'complete'), task.get('work_seconds', .15)
        if (type(mode) is not str or mode not in MODES or type(work) not in (int,float) or not math.isfinite(work)
                or not 0 <= work <= 5):
            raise ValueError('Invalid fixture mode or bounded artificial delay')
        normalized.append({'id':task['id'], 'mode':mode, 'work_seconds':work})
    if len({t['id'] for t in normalized}) != len(normalized):
        raise ValueError('Fixture task IDs must be unique')
    return {'schema_version':1, 'executor':EXECUTOR, 'agent_id':agent_id, 'arm':'natural',
            'tasks':normalized, 'root':str(Path(root).resolve()), **_pins(),
            'prompt_sha256':_sha(PROMPT), 'image':IMAGE, 'duration_request':None,
            'experiment_time_cap_seconds':None, 'admission':'excluded_fixture',
            'continuation_supported':False, 'replacement_supported':False,
            'clock_qualification':'fixture_native_single_execution', 'study_launch_ready':False}


def validate_manifest(manifest, *, loaded=False):
    try:
        expected = build_manifest(manifest['root'], manifest['tasks'], manifest['agent_id'])
        if canonical_json(expected) != canonical_json(manifest):
            raise IntegrityError('Native fixture manifest differs from the current code, inputs or runtime')
        if loaded and (manifest['source_sha256'] != _LOADED_SOURCE
                       or manifest['source_sha256'] != fixture._LOADED_SOURCE_SHA256
                       or _sha(Path(harbor_fixture.__file__).read_bytes()) != harbor_fixture._LOADED_MODULE_SHA256):
            raise IntegrityError('Native fixture source changed since worker import')
    except (KeyError, TypeError, OSError, ValueError) as error:
        if isinstance(error, IntegrityError):
            raise
        raise IntegrityError(f'Invalid frozen native fixture manifest: {error}') from error


def _files(folder):
    """Freeze only this worker's inputs and finalized Harbor outputs."""
    result = {}
    for name in ('worker.json', 'invocation.json'):
        result[name] = _regular_bytes(folder/name)
    for directory in ('sources', 'harbor'):
        for path in sorted((folder/directory).rglob('*')):
            if path.is_symlink():
                raise IntegrityError('Fixture archives cannot contain symlinks')
            if path.is_file():
                result[path.relative_to(folder).as_posix()] = _regular_bytes(path)
    return result


def _regular_bytes(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IntegrityError('Fixture archive requires regular files')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            return source.read()
    finally:
        os.close(fd)


def _decode(data):
    return loads_strict(data)


def _phase_seconds(phase):
    if phase is None:
        return None
    if type(phase) is not dict or set(phase) != {'started_at','finished_at'}:
        raise IntegrityError('Invalid Harbor phase timing')
    if any(value is not None and type(value) is not str for value in phase.values()):
        raise IntegrityError('Harbor phase timestamps must be UTC strings or null')
    if phase['started_at'] is None or phase['finished_at'] is None:
        return None
    seconds = (datetime.fromisoformat(phase['finished_at'].replace('Z','+00:00')) -
               datetime.fromisoformat(phase['started_at'].replace('Z','+00:00'))).total_seconds()
    if seconds < 0 or not math.isfinite(seconds):
        raise IntegrityError('Invalid Harbor reference duration')
    return seconds


def derive_report(campaign, attempt, files, archive_sha256):
    try:
        return _derive_report(campaign, attempt, files, archive_sha256)
    except (OSError, ValueError, TypeError, KeyError, StopIteration, AttributeError, OverflowError, RecursionError) as error:
        if isinstance(error, IntegrityError):
            raise
        raise IntegrityError(f'Invalid native source evidence: {error}') from error


def _derive_report(campaign, attempt, files, archive_sha256):
    """Re-derive all admitted facts from the archived native sources, never a claimed score."""
    manifest = campaign['manifest']
    task = next(t for t in manifest['tasks'] if t['id'] == attempt['task_id'])
    owner = _decode(files['worker.json'])
    fixture._shape(owner, {'attempt_id','worker_id','pid','process_created','hostname'}, 'Native worker owner')
    if (owner['attempt_id'] != attempt['id'] or owner['worker_id'] != attempt['worker_id']
            or type(owner['pid']) is not int or type(owner['process_created']) not in (int,float)
            or type(owner['hostname']) is not str):
        raise IntegrityError('Native worker owner does not match its permanent claim')
    invocation = _decode(files['invocation.json'])
    expected_intent = {'attempt_id':attempt['id'], 'worker_id':attempt['worker_id'],
                       'manifest_sha256':campaign['manifest_sha256'], 'prompt_sha256':manifest['prompt_sha256']}
    if canonical_json(invocation) != canonical_json(expected_intent):
        raise IntegrityError('Harbor invocation is not bound to the original claim')
    for name, digest in manifest['asset_sha256'].items():
        if _sha(files['sources/'+name]) != digest:
            raise IntegrityError('Archived native fixture input differs from its frozen manifest')
    boundary = _decode(files[f"harbor/{task['mode']}-report.json"])
    trial_name = boundary['trial_name']
    if (trial_name != f"at11-{task['mode']}-{attempt['id']}"
            or boundary['attempt_id'] != attempt['id'] or boundary['execution_id'] != attempt['worker_id']):
        raise IntegrityError('Harbor report identity differs from its original claim')
    prefix = f'harbor/trials/{trial_name}/'
    result = _decode(files[prefix+'result.json'])
    config = result['config']
    kwargs = config['agent']['kwargs']
    if (result['trial_name'] != trial_name or config['trial_name'] != trial_name
            or config['agent']['import_path'] != 'agenttime.harbor_fixture:FixtureAgent'
            or config['agent']['model_name'] is not None or config['agent']['mcp_servers']
            or config['agent']['skills'] or config['agent']['resume_trajectory']
            or config['agent']['load_trajectory'] is not None
            or config['extra_instructions'] or config['extra_instruction_paths']
            or config['user_agent'] is not None or config['environment']['type'] != 'docker'
            or config['environment']['delete'] is not True or config['job_id'] is not None
            or result['agent_info']['model_info'] is not None
            or kwargs['attempt_id'] != attempt['id'] or kwargs['execution_id'] != attempt['worker_id']
            or kwargs['mode'] != task['mode'] or kwargs['work_seconds'] != task['work_seconds']):
        raise IntegrityError('Archived Harbor configuration does not match the disposable attempt')
    for key in ('override_timeout_sec','max_timeout_sec'):
        if config['agent'][key] is not None:
            raise IntegrityError('Native fixture unexpectedly acquired an agent time cap')
    task_prefix = f"harbor/task-{task['mode']}/"
    attempt_root = Path(manifest['root'])/'attempts'/attempt['id']
    if (config['task']['path'] != str(attempt_root/task_prefix)
            or config['trials_dir'] != str(attempt_root/'harbor/trials')
            or kwargs['assets_dir'] != str(attempt_root/'sources')):
        raise IntegrityError('Effective Harbor input paths differ from this attempt')
    if files[task_prefix+'instruction.md'] != PROMPT:
        raise IntegrityError('Harbor task prompt differs from the frozen prompt')
    for target,source in [('tests/test.sh','test.sh'), ('environment/docker-compose.yaml','docker-compose.yaml')]:
        if files[task_prefix+target] != files['sources/'+source]:
            raise IntegrityError('Effective Harbor task files differ from the frozen assets')
    task_config = tomllib.loads(files[task_prefix+'task.toml'].decode())
    expected_task = {'schema_version':'1.4', 'environment':{'docker_image':IMAGE,'cpus':1,'memory_mb':256},
                     'verifier':{'timeout_sec':30}}
    if canonical_json(task_config) != canonical_json(expected_task):
        raise IntegrityError('Effective Harbor task configuration differs from the fixture contract')
    agent_prefix = prefix+'agent/'
    event_bytes = files[agent_prefix+'native-events.jsonl']
    events = [_decode(line) for line in event_bytes.splitlines()]
    measurement = project_events(events)
    if not events or any(e['attempt_id'] != attempt['id'] or e['execution_id'] != attempt['worker_id'] for e in events):
        raise IntegrityError('Native source event ownership mismatch')
    if (events[0]['kind'] != 'prompt_released'
            or events[0]['payload'] != {'prompt_sha256':manifest['prompt_sha256'], 'fixture_mode':task['mode']}
            or files[agent_prefix+'release-witness.txt'] != (attempt['id']+'\n').encode()):
        raise IntegrityError('Native release witness differs from the single intended prompt')
    if task['mode'] == 'crash':
        if (len(events) != 1 or measurement['timing_status'] != 'pending'
                or result['exception_info'] is None):
            raise IntegrityError('Crash fixture has conflicting or invented native boundaries')
    elif measurement['timing_status'] != 'valid' or [e['kind'] for e in events] != [
            'prompt_released','native_terminal','owned_work_drained','journal_closed']:
        raise IntegrityError('Native completion is missing its complete source history')
    sealed = None
    if measurement['terminal'] is not None:
        payload = measurement['terminal']['payload']
        fixture._shape(payload, {'submission_b64','submission_sha256'}, 'Harbor native terminal')
        sealed = base64.b64decode(payload['submission_b64'], validate=True)
        if _sha(sealed) != payload['submission_sha256'] or files[agent_prefix+'sealed-submission.bin'] != sealed:
            raise IntegrityError('Native terminal bytes disagree with the captured submission')
    isolation = _decode(files[prefix+'container-identity.json'])
    if canonical_json(isolation) != canonical_json(_decode(files[agent_prefix+'network-isolation.json'])):
        raise IntegrityError('Subject-visible container identity differs from the controller receipt')
    stop = _decode(files[prefix+'stop-proof.json'])
    if type(stop.get('endpoint')) is not str:
        raise IntegrityError('Native stop-proof endpoint must be a string')
    if (isolation['network_mode'] != 'none' or isolation['running'] is not True
            or isolation['image'] != IMAGE or not fixture._digest(isolation['id'])
            or isolation['labels']['com.docker.compose.service'] != 'main'
            or stop['container_id'] != isolation['id'] or stop['removed'] is not True
            or not stop['daemon_id'] or not stop['endpoint'].startswith('unix://')
            or stop['daemon_id'] != isolation['daemon_id'] or stop['endpoint'] != isolation['endpoint']
            or kwargs['endpoint'] != stop['endpoint'] or kwargs['daemon_id'] != stop['daemon_id']
            or isolation['attempt_id'] != attempt['id'] or isolation['execution_id'] != attempt['worker_id']
            or stop['attempt_id'] != attempt['id'] or stop['execution_id'] != attempt['worker_id']
            or stop['compose_project'] != isolation['labels']['com.docker.compose.project']
            or stop['compose_service'] != 'main' or stop['image'] != IMAGE
            or stop['image_id'] != isolation['image_id'] or stop['network_mode'] != 'none'):
        raise IntegrityError('Exact owned-container stop proof is missing or conflicting')
    # Harbor 0.23.0 Trial creates the main environment as <trial_name>__env.
    # Fixed mode and UUID trial names contain only characters Docker preserves.
    if isolation['labels']['com.docker.compose.project'] != trial_name + '__env':
        raise IntegrityError('Native container project differs from this exact trial')
    verifier = result['verifier_result']
    if verifier is not None and (type(verifier) is not dict
            or type(verifier.get('rewards')) is not dict):
        raise IntegrityError('Malformed native verifier evidence')
    score = verifier['rewards'].get('reward') if verifier and verifier['rewards'] else None
    if score is not None:
        if sealed is None or type(score) not in (int,float) or score != int(sealed == b'42'):
            raise IntegrityError('Native grade is inconsistent with terminal-bound fixture bytes')
        reward = float(files[prefix+'verifier/reward.txt'])
        if score != reward:
            raise IntegrityError('Harbor reward differs from the retained native grader output')
    if task['mode'] in {'crash','grade_failure'} and score is not None:
        raise IntegrityError('A failed native stage cannot acquire a fabricated score')
    phases = {name:result[name] for name in ('environment_setup','agent_setup','agent_execution','verifier')}
    harbor_seconds = _phase_seconds(phases['agent_execution'])
    for phase in phases.values():
        _phase_seconds(phase)
    for field, expected in {'invocations':1, 'prompt_sha256':manifest['prompt_sha256'],
            'timing_status':measurement['timing_status'], 'runtime_seconds':measurement['runtime_seconds'],
            'timing_reason':measurement['reason'], 'harbor_agent_seconds':harbor_seconds,
            'event_sha256_after_harbor_finalization':_sha(event_bytes),
            'container_id':isolation['id'], 'container_removal_verified':True,
            'docker_endpoint':stop['endpoint'], 'docker_daemon_id':stop['daemon_id'],
            'effective_agent_timeout_seconds':None, 'score':score, 'admission':'excluded_fixture'}.items():
        if canonical_json(boundary[field]) != canonical_json(expected):
            raise IntegrityError(f'Harbor boundary report disagrees with source evidence: {field}')
    return {'schema_version':1, 'executor':EXECUTOR, 'admission':'excluded_fixture',
            'attempt_id':attempt['id'], 'worker_id':attempt['worker_id'],
            'manifest_sha256':campaign['manifest_sha256'], 'source_sha256':manifest['source_sha256'],
            'archive_sha256':archive_sha256, 'prompt_sha256':manifest['prompt_sha256'],
            'timing_status':measurement['timing_status'], 'runtime_seconds':measurement['runtime_seconds'],
            'timing_reason':measurement['reason'], 'harbor_agent_seconds':harbor_seconds, 'harbor_phases':phases,
            'quality_status':'available' if score is not None else 'unavailable', 'score':score,
            'artifact_status':'preserved' if sealed is not None else 'unavailable',
            'submission_sha256':_sha(sealed) if sealed is not None else None,
            'invocations':1, 'container_id':isolation['id'], 'container_removal_verified':True,
            'effective_agent_timeout_seconds':None,
            'late_write_observed':files.get(agent_prefix+'working-answer.txt') == b'late overwrite',
            'exception_type':result['exception_info']['exception_type'] if result['exception_info'] else None,
            'limitations':_LIMITATIONS}


def run_worker(ledger, attempt_id):
    worker_id = str(uuid4())
    if not ledger.claim(attempt_id, worker_id):
        return False
    local_report = False
    try:
        attempt = ledger.attempt(attempt_id)
        campaign = ledger.campaign(attempt['campaign_id'])
        manifest = campaign['manifest']
        validate_manifest(manifest, loaded=True)
        root = Path(manifest['root'])
        folder = root/'attempts'/attempt_id
        folder.mkdir(parents=True, exist_ok=True)
        fixture._write_once(folder/'worker.json', canonical_json({
            'attempt_id':attempt_id, 'worker_id':worker_id, 'pid':os.getpid(),
            'process_created':psutil.Process().create_time(), 'hostname':socket.gethostname()}))
        sources = folder/'sources'; sources.mkdir()
        for name, digest in manifest['asset_sha256'].items():
            data = (ASSETS/name).read_bytes()
            if _sha(data) != digest:
                raise IntegrityError('Native fixture asset changed before setup')
            fixture._write_once(sources/name, data)
        fixture._write_once(folder/'invocation.json', canonical_json({
            'attempt_id':attempt_id,'worker_id':worker_id,'manifest_sha256':campaign['manifest_sha256'],
            'prompt_sha256':manifest['prompt_sha256']}))
        task = next(t for t in manifest['tasks'] if t['id'] == attempt['task_id'])
        def before_release():
            try:
                validate_manifest(manifest, loaded=True)
                for name, digest in manifest['asset_sha256'].items():
                    if _sha((sources/name).read_bytes()) != digest:
                        raise IntegrityError('Frozen native asset changed before prompt release')
            except (IntegrityError, OSError) as error:
                ledger.pause(str(error))
                raise IntegrityError(str(error)) from error

        asyncio.run(harbor_fixture.run_case(folder/'harbor', task['mode'], sources, attempt_id, worker_id,
                             task['work_seconds'], before_release=before_release))
        validate_manifest(manifest, loaded=True)
        files = _files(folder)
        inventory = {'schema_version':1, 'attempt_id':attempt_id, 'worker_id':worker_id,
                     'files':{name:_sha(data) for name,data in files.items()}}
        inventory_bytes = canonical_json(inventory)
        report = derive_report(campaign, attempt, files, _sha(inventory_bytes))
        fixture._write_once(folder/'report.json', canonical_json(report))
        fixture._write_once(folder/'archive.json', inventory_bytes)
        local_report = True
        store = EvidenceStore(root/'evidence')
        for data in files.values():
            store.put(data)
        archive_sha = store.put(inventory_bytes)
        report_sha = store.put(canonical_json(report))
        fixture._write_once(folder/'completion.json', canonical_json({
            'attempt_id':attempt_id,'worker_id':worker_id,'archive_sha256':archive_sha,'report_sha256':report_sha}))
        reconcile(ledger, campaign, attempt)
        return True
    except IntegrityError as error:
        ledger.pause(str(error)); ledger.quarantine(attempt_id, str(error))
        return False
    except Exception as error:
        reason = 'evidence_publication_failed; local_report_retained' if local_report else 'native_worker_incomplete'
        ledger.quarantine(attempt_id, f'{reason}: {type(error).__name__}: {error}')
        return False


def reconcile(ledger, campaign, attempt):
    root = Path(campaign['manifest']['root'])
    try:
        receipt = fixture._read_json(root/'attempts'/attempt['id']/'completion.json')
    except FileNotFoundError:
        return False
    try:
        fixture._shape(receipt, {'attempt_id','worker_id','archive_sha256','report_sha256'}, 'Native completion receipt')
        current = ledger.attempt(attempt['id'])
        if (current['campaign_id'] != campaign['id'] or not current['worker_id']
                or receipt['attempt_id'] != current['id'] or receipt['worker_id'] != current['worker_id']):
            raise IntegrityError('Native completion differs from original claim')
        validate_manifest(campaign['manifest'], loaded=True)
        store = EvidenceStore(root/'evidence')
        inventory = _decode(store.get(receipt['archive_sha256']))
        fixture._shape(inventory, {'schema_version','attempt_id','worker_id','files'}, 'Native archive inventory')
        if (type(inventory['schema_version']) is not int or inventory['schema_version'] != 1
                or inventory['attempt_id'] != current['id'] or inventory['worker_id'] != current['worker_id']
                or type(inventory['files']) is not dict or not inventory['files']):
            raise IntegrityError('Native archive ownership differs from original claim')
        files = {}
        for name,digest in inventory['files'].items():
            path = PurePosixPath(name)
            if not name or '\x00' in name or path.is_absolute() or path.as_posix() != name or '..' in path.parts:
                raise IntegrityError('Native archive contains an unsafe relative path')
            files[name] = store.get(digest)  # Verify every object, including ancillary Harbor artifacts.
        expected = derive_report(campaign, current, files, receipt['archive_sha256'])
        report = _decode(store.get(receipt['report_sha256']))
        if canonical_json(report) != canonical_json(expected):
            raise IntegrityError('Native completion report differs from archived source evidence')
    except (OSError,ValueError,TypeError,KeyError,StopIteration) as error:
        if isinstance(error, IntegrityError):
            raise
        raise IntegrityError(f'Native completion evidence is missing or invalid: {error}') from error
    ledger.finish(current['id'], current['worker_id'], report)
    return True
