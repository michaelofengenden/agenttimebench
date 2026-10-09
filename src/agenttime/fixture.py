"""Disposable autonomous execution. These fixtures never enter study results."""
import base64
import hashlib
from importlib.metadata import version
import math
import os
from pathlib import Path
import platform
import re
import stat
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

import psutil

from .evidence import EvidenceStore, IntegrityError, canonical_json
from .ledger import Ledger
from .jsonio import loads_strict
from .measurement import project_events

BEHAVIORS = {'normal', 'wrong_answer', 'grade_failure', 'artifact_missing',
             'post_terminal_write', 'crash_before_release'}
PROMPT = b'Write the answer 42 and submit the final bytes. This is a model-free fixture.'
_CHILDREN: dict[str, subprocess.Popen] = {}
_LIMITATIONS = ['model_free_fixture', 'local_evidence_store', 'no_native_harbor_qualification']
_KINDS = ['setup_started', 'prompt_released', 'native_terminal', 'capture_finished',
          'grade_finished', 'owned_work_drained', 'journal_closed']
_REPORT_FIELDS = {'schema_version', 'attempt_id', 'worker_id', 'manifest_sha256',
                  'source_sha256', 'prompt_sha256', 'timing_status', 'runtime_seconds',
                  'timing_reason', 'artifact_status', 'submission_sha256', 'quality_status',
                  'score', 'journal_sha256', 'admission', 'executor', 'limitations'}


def source_digest():
    digest = hashlib.sha256()
    for p in sorted(Path(__file__).parent.glob('*.py')):
        digest.update(p.name.encode() + b'\0' + p.read_bytes() + b'\0')
    return digest.hexdigest()


_LOADED_SOURCE_SHA256 = source_digest()


def runtime_identity():
    return {'python': platform.python_version(), 'platform': platform.platform(),
            'psycopg': version('psycopg'), 'psutil': version('psutil')}


def build_manifest(root: Path, tasks: list[dict], agent_id='fixture-agent-v1'):
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('At least one fixture task is required')
    normalized = []
    for task in tasks:
        if not isinstance(task, dict) or set(task) - {'id', 'behavior', 'setup_seconds', 'work_seconds', 'grade_seconds'}:
            raise ValueError('Unsupported fixture task fields')
        if not isinstance(task.get('id'), str) or not task['id']:
            raise ValueError('Fixture task identity is required')
        item = dict(id=task['id'], behavior=task.get('behavior', 'normal'))
        if item['behavior'] not in BEHAVIORS:
            raise ValueError('Unknown fixture behavior')
        for name, default in [('setup_seconds', 0), ('work_seconds', .03), ('grade_seconds', 0)]:
            value = task.get(name, default)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 5:
                raise ValueError('Fixture delays must be finite numbers between zero and five seconds')
            item[name] = value
        normalized.append(item)
    lock = Path(__file__).resolve().parents[2] / 'uv.lock'
    return {'schema_version': 1, 'executor': 'fixture-v1', 'agent_id': agent_id, 'arm': 'natural',
            'tasks': normalized, 'root': str(Path(root).resolve()), 'source_sha256': source_digest(),
            'dependency_lock_sha256': hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None,
            'runtime': runtime_identity(), 'prompt_sha256': hashlib.sha256(PROMPT).hexdigest(),
            'duration_request': None, 'experiment_time_cap_seconds': None,
            'admission': 'excluded_fixture', 'continuation_supported': False,
            'replacement_supported': False, 'clock_qualification': 'fixture_same_process_only',
            'native_harbor_qualified': False}


def _write_once(path: Path, data: bytes):
    """Publish a small local control receipt atomically without replacement."""
    temporary = path.with_name('.' + path.name + '.' + str(uuid4()))
    try:
        with temporary.open('xb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.link(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IntegrityError('Control receipt must be a regular file')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            return loads_strict(source.read())
    finally:
        os.close(fd)


def _environment(dsn):
    env = {k: os.environ[k] for k in ('PATH', 'SYSTEMROOT', 'TMPDIR', 'LANG') if k in os.environ}
    env.update(AGENTTIME_DSN=dsn, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return env


class Journal:
    def __init__(self, path, attempt_id, worker_id):
        self.path, self.attempt_id, self.worker_id = path, attempt_id, worker_id
        self.clock_id = f'{socket.gethostname()}:{psutil.boot_time()}:{worker_id}'
        self.events = []
        self.file = path.open('xb')

    def emit(self, kind, payload=None):
        event = {'schema_version': 1, 'attempt_id': self.attempt_id, 'execution_id': self.worker_id,
                 'event_id': str(uuid4()), 'sequence': len(self.events) + 1, 'clock_id': self.clock_id,
                 'monotonic_ns': time.monotonic_ns(), 'recorded_at': datetime.now(timezone.utc).isoformat(),
                 'kind': kind, 'payload': payload or {}}
        self.file.write(canonical_json(event) + b'\n')
        self.file.flush()
        os.fsync(self.file.fileno())
        self.events.append(event)
        return event

    def close(self):
        self.emit('journal_closed', {'final_sequence': len(self.events) + 1})
        self.file.close()


def _make_report(campaign, attempt, events, captured, grade, journal_sha):
    measurement = project_events(events)
    manifest = campaign['manifest']
    return {'schema_version': 1, 'attempt_id': attempt['id'], 'worker_id': attempt['worker_id'],
            'manifest_sha256': campaign['manifest_sha256'], 'source_sha256': manifest['source_sha256'],
            'prompt_sha256': manifest['prompt_sha256'], 'timing_status': measurement['timing_status'],
            'runtime_seconds': measurement['runtime_seconds'], 'timing_reason': measurement['reason'],
            'artifact_status': captured['artifact_status'], 'submission_sha256': captured['submission_sha256'],
            'quality_status': grade['quality_status'], 'score': grade['score'], 'journal_sha256': journal_sha,
            'admission': 'excluded_fixture', 'executor': 'fixture-v1', 'limitations': _LIMITATIONS}


def run_worker(ledger: Ledger, attempt_id: str):
    worker_id = str(uuid4())
    if not ledger.claim(attempt_id, worker_id):
        return False
    journal = None
    child = None
    local_report = False
    try:
        attempt = ledger.attempt(attempt_id)
        campaign = ledger.campaign(attempt['campaign_id'])
        manifest = campaign['manifest']
        if manifest.get('executor') != 'fixture-v1':
            raise IntegrityError('This worker accepts fixture-v1 only')
        if (manifest['source_sha256'] != source_digest() or manifest['runtime'] != runtime_identity()
                or manifest['prompt_sha256'] != hashlib.sha256(PROMPT).hexdigest()):
            raise IntegrityError('Worker source, runtime or prompt differs from the frozen fixture manifest')
        task = next(t for t in manifest['tasks'] if t['id'] == attempt['task_id'])
        root = Path(manifest['root'])
        folder = root / 'attempts' / attempt_id
        folder.mkdir(parents=True, exist_ok=True)
        owner = {'attempt_id': attempt_id, 'worker_id': worker_id, 'pid': os.getpid(),
                 'process_created': psutil.Process().create_time(), 'hostname': socket.gethostname()}
        _write_once(folder / 'worker.json', canonical_json(owner))
        journal = Journal(folder / 'events.jsonl', attempt_id, worker_id)
        journal.emit('setup_started')
        time.sleep(task['setup_seconds'])
        if task['behavior'] == 'crash_before_release':
            os._exit(70)  # Deliberate fixture fault, never a study executor path.
        _write_once(folder / 'invocation.json', canonical_json({'worker_id': worker_id, 'prompt_sha256': manifest['prompt_sha256']}))
        journal.emit('prompt_released', {'prompt_sha256': manifest['prompt_sha256']})
        time.sleep(task['work_seconds'])
        answer = b'wrong' if task['behavior'] == 'wrong_answer' else b'42'
        (folder / 'answer.txt').write_bytes(answer)
        if task['behavior'] == 'post_terminal_write':
            child = subprocess.Popen([sys.executable, '-c',
                'import pathlib,sys; signal=sys.stdin.buffer.read(); '
                'pathlib.Path(sys.argv[1]).write_text("late overwrite") if signal==b"go" else None',
                str(folder / 'answer.txt')], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, env=_environment(ledger.dsn))
        terminal = journal.emit('native_terminal', {'submission_b64': base64.b64encode(answer).decode(),
                    'submission_sha256': hashlib.sha256(answer).hexdigest(), 'endpoint': 'fixture_byte_submission'})
        if child is not None:
            child.communicate(b'go')

        store = None
        shared_failure = None
        capture = {'artifact_status': 'unavailable', 'submission_sha256': None, 'error': None}
        try:
            store = EvidenceStore(root / 'evidence')
            sealed = _terminal_bytes(terminal)
            if task['behavior'] != 'artifact_missing':
                capture['submission_sha256'] = store.put(sealed)
                capture['artifact_status'] = 'preserved'
        except Exception as error:
            capture['error'] = f'{type(error).__name__}: {error}'
            if isinstance(error, IntegrityError):
                ledger.pause(str(error))
                shared_failure = str(error)
        journal.emit('capture_finished', capture)

        grade = {'quality_status': 'unavailable', 'score': None, 'error': None}
        try:
            time.sleep(task['grade_seconds'])
            if capture['submission_sha256'] is not None and task['behavior'] != 'grade_failure':
                grade['score'] = int(store.get(capture['submission_sha256']) == b'42')
                grade['quality_status'] = 'available'
        except Exception as error:
            grade['error'] = f'{type(error).__name__}: {error}'
            if isinstance(error, IntegrityError):
                ledger.pause(str(error))
                shared_failure = str(error)
        journal.emit('grade_finished', grade)
        if child is not None:
            child.wait()
        journal.emit('owned_work_drained')
        journal.close()

        journal_bytes = (folder / 'events.jsonl').read_bytes()
        report = _make_report(campaign, attempt, journal.events, capture, grade,
                              hashlib.sha256(journal_bytes).hexdigest())
        # Keep independently valid timing locally even if publication is unavailable.
        _write_once(folder / 'report.json', canonical_json(report))
        local_report = True
        if shared_failure is not None:
            ledger.quarantine(attempt_id, f'shared_evidence_integrity_failure: {shared_failure}; local_report_retained')
            return False
        store = store or EvidenceStore(root / 'evidence')
        store.put(journal_bytes)
        report_sha = store.put(canonical_json(report))
        _write_once(folder / 'completion.json', canonical_json({'attempt_id': attempt_id, 'worker_id': worker_id,
                                                              'report_sha256': report_sha}))
        _reconcile(ledger, campaign, attempt)
        return True
    except IntegrityError as error:
        ledger.pause(str(error))
        ledger.quarantine(attempt_id, str(error))
        return False
    except (OSError, ValueError) as error:
        reason = f'evidence_publication_failed: {error}; local_report_retained' if local_report else str(error)
        ledger.quarantine(attempt_id, reason)
        return False
    finally:
        if child is not None:
            if child.poll() is None:
                child.communicate()
            child.wait()
        if journal is not None and not journal.file.closed:
            journal.file.close()


def _reap_workers():
    for attempt_id, child in list(_CHILDREN.items()):
        if child.poll() is not None:
            child.wait()
            del _CHILDREN[attempt_id]


def _alive(folder, attempt):
    owned = _CHILDREN.get(attempt['id'])
    if owned is not None:
        if owned.poll() is None:
            return True
        owned.wait()
        del _CHILDREN[attempt['id']]
    try:
        owner = _read_json(folder / 'worker.json')
        _shape(owner, {'worker_id', 'attempt_id', 'hostname', 'pid', 'process_created'}, 'Worker owner')
        if type(owner['pid']) is not int or type(owner['process_created']) not in (int, float):
            return False
        if (owner['worker_id'] != attempt['worker_id'] or owner['attempt_id'] != attempt['id']
                or owner['hostname'] != socket.gethostname()):
            return False
        process = psutil.Process(owner['pid'])
        return process.create_time() == owner['process_created'] and process.status() != psutil.STATUS_ZOMBIE
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return False


def _shape(value, fields, label):
    if type(value) is not dict or set(value) != fields:
        raise IntegrityError(f'{label} has an invalid schema')


def _digest(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _terminal_bytes(terminal):
    payload = terminal['payload']
    _shape(payload, {'submission_b64', 'submission_sha256', 'endpoint'}, 'Native terminal')
    if (type(payload['submission_b64']) is not str or not _digest(payload['submission_sha256'])
            or payload['endpoint'] != 'fixture_byte_submission'):
        raise IntegrityError('Invalid native fixture submission')
    try:
        sealed = base64.b64decode(payload['submission_b64'], validate=True)
    except ValueError as error:
        raise IntegrityError('Invalid terminal submission encoding') from error
    if hashlib.sha256(sealed).hexdigest() != payload['submission_sha256']:
        raise IntegrityError('Terminal submission digest mismatch')
    return sealed


def _validate_report(report, events, campaign, attempt, store):
    _shape(report, _REPORT_FIELDS, 'Completion report')
    measurement = project_events(events)
    if (measurement['timing_status'] != 'valid' or len(events) != len(_KINDS)
            or [e['kind'] for e in events] != _KINDS
            or [e['sequence'] for e in events] != list(range(1, len(_KINDS) + 1))
            or any(e['attempt_id'] != attempt['id'] or e['execution_id'] != attempt['worker_id'] for e in events)):
        raise IntegrityError('Completion requires a complete ordered fixture history')
    if (events[0]['payload'] != {} or events[5]['payload'] != {}
            or events[6]['payload'] != {'final_sequence': len(_KINDS)}
            or events[1]['payload'] != {'prompt_sha256': campaign['manifest']['prompt_sha256']}):
        raise IntegrityError('Fixture boundary payload differs from its contract')
    sealed = _terminal_bytes(events[2])
    capture, grade = events[3]['payload'], events[4]['payload']
    _shape(capture, {'artifact_status', 'submission_sha256', 'error'}, 'Capture event')
    _shape(grade, {'quality_status', 'score', 'error'}, 'Grade event')
    if any(stage['error'] is not None and (type(stage['error']) is not str or not stage['error'])
           for stage in (capture, grade)):
        raise IntegrityError('Invalid fixture failure diagnostic')
    if capture['artifact_status'] == 'preserved':
        if not _digest(capture['submission_sha256']) or capture['error'] is not None:
            raise IntegrityError('Invalid preserved artifact status')
        if store.get(capture['submission_sha256']) != sealed:
            raise IntegrityError('Preserved submission differs from terminal bytes')
    elif capture['artifact_status'] != 'unavailable' or capture['submission_sha256'] is not None:
        raise IntegrityError('Invalid unavailable artifact status')
    if grade['quality_status'] == 'available':
        if (capture['artifact_status'] != 'preserved' or type(grade['score']) is not int
                or grade['score'] != int(sealed == b'42') or grade['error'] is not None):
            raise IntegrityError('Grade differs from the sealed fixture submission')
    elif grade['quality_status'] != 'unavailable' or grade['score'] is not None:
        raise IntegrityError('Invalid unavailable quality status')
    expected = _make_report(campaign, attempt, events, capture, grade, report['journal_sha256'])
    if canonical_json(report) != canonical_json(expected):
        raise IntegrityError('Completion report differs from its manifest or source events')


def _reconcile(ledger, campaign, attempt):
    root = Path(campaign['manifest']['root'])
    try:
        receipt = _read_json(root / 'attempts' / attempt['id'] / 'completion.json')
    except FileNotFoundError:
        return False  # Only an absent atomic completion receipt means pending.
    try:
        _shape(receipt, {'attempt_id', 'worker_id', 'report_sha256'}, 'Completion receipt')
        # The receipt can arrive after the caller took an unclaimed snapshot.
        current = ledger.attempt(attempt['id'])
        if (current['campaign_id'] != campaign['id'] or current['worker_id'] is None
                or receipt['attempt_id'] != current['id'] or receipt['worker_id'] != current['worker_id']
                or not _digest(receipt['report_sha256'])):
            raise IntegrityError('Completion identity mismatch')
        store = EvidenceStore(root / 'evidence')
        report = loads_strict(store.get(receipt['report_sha256']))
        _shape(report, _REPORT_FIELDS, 'Completion report')
        if not _digest(report['journal_sha256']):
            raise IntegrityError('Invalid journal digest')
        events = [loads_strict(line) for line in store.get(report['journal_sha256']).splitlines()]
        _validate_report(report, events, campaign, current, store)
    except (OSError, ValueError, TypeError, KeyError) as error:
        if isinstance(error, IntegrityError):
            raise
        raise IntegrityError(f'Published completion evidence is unavailable or invalid: {error}') from error
    ledger.finish(current['id'], current['worker_id'], report)
    return True


def status(ledger, campaign_id):
    campaign = ledger.campaign(campaign_id)
    root = Path(campaign['manifest']['root'])
    attempts = ledger.attempts(campaign_id)
    _reap_workers()
    rows = []
    for a in attempts:
        live = a['holds_capacity'] and _alive(root / 'attempts' / a['id'], a)
        rows.append({'attempt_id': a['id'], 'task_id': a['task_id'], 'state': a['state'],
                     'holds_capacity': a['holds_capacity'], 'worker_alive': live, 'reason': a['reason'], 'report': a['report']})
    return {'campaign_id': campaign_id, 'executor': campaign['manifest']['executor'], 'study_launch_ready': False,
            'manifest_sha256': campaign['manifest_sha256'], 'total': len(campaign['manifest']['tasks']),
            'finished': sum(a['state'] == 'finished' for a in rows),
            'active': sum(a['worker_alive'] for a in rows),
            'held': sum(a['holds_capacity'] and not a['worker_alive'] for a in rows),
            'queued': len(campaign['manifest']['tasks']) - len(rows),
            'paused_reason': ledger.gate()['paused_reason'], 'attempts': rows}


def _backend(campaign):
    name = campaign['manifest']['executor']
    if name == 'fixture-v1':
        return _reconcile, 'fixture'
    if name == 'harbor-fixture-v1':
        from . import harbor_worker
        harbor_worker.validate_manifest(campaign['manifest'], loaded=True)
        return harbor_worker.reconcile, 'harbor-fixture'
    raise IntegrityError('Unsupported fixture executor')


def _dispatch_environment(dsn, command):
    env = _environment(dsn)
    if command == 'harbor-fixture':
        endpoint = os.environ.get('DOCKER_HOST')
        if not endpoint:
            endpoint = subprocess.check_output(
                ['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
        from .harbor_fixture import _local_endpoint
        env.update(DOCKER_HOST=_local_endpoint(endpoint), LITELLM_LOCAL_MODEL_COST_MAP='True')
        daemon_id = subprocess.check_output(
            ['docker', 'info', '--format', '{{.ID}}'], env=env, text=True, stderr=subprocess.PIPE).strip()
        if not daemon_id:
            raise ValueError('Docker daemon identity is unavailable during dispatch preflight')
    return env


def tick(ledger, campaign_id):
    _reap_workers()
    campaign = ledger.campaign(campaign_id)
    try:
        reconcile, command = _backend(campaign)
    except IntegrityError as error:
        ledger.pause(str(error))
        raise
    root = Path(campaign['manifest']['root'])
    for attempt in ledger.attempts(campaign_id):
        if attempt['state'] == 'finished':
            continue
        folder = root / 'attempts' / attempt['id']
        try:
            if not reconcile(ledger, campaign, attempt):
                current = ledger.attempt(attempt['id'])
                if current['state'] == 'active' and current['worker_id'] is not None and not _alive(folder, current):
                    ledger.quarantine(attempt['id'], 'worker_unavailable_without_completion; reservation retained')
        except (IntegrityError, ValueError, KeyError, TypeError, OSError) as error:
            ledger.pause(f'Fixture evidence integrity failure: {error}')
            ledger.quarantine(attempt['id'], 'completion_evidence_invalid')
    if (ledger.admission_block_reason(campaign_id) is not None
            or len(ledger.attempts(campaign_id)) == len(campaign['manifest']['tasks'])):
        return status(ledger, campaign_id)
    try:
        dispatch_environment = _dispatch_environment(ledger.dsn, command)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        ledger.pause(f'Fixture dispatch preflight failed: {error}')
        return status(ledger, campaign_id)
    while (attempt := ledger.reserve(campaign_id)) is not None:
        folder = root / 'attempts' / attempt['id']
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'worker.log').open('ab') as log:
                child = subprocess.Popen([sys.executable, '-m', 'agenttime', command, 'worker', '--attempt', attempt['id']],
                    env=dispatch_environment, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                    start_new_session=True)
            _CHILDREN[attempt['id']] = child
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            ledger.quarantine(attempt['id'], f'dispatch_outcome_unknown: {error}')
            ledger.pause(f'Fixture dispatch failed: {error}')
            break
    return status(ledger, campaign_id)


def run_campaign(ledger, campaign_id):
    while True:
        result = tick(ledger, campaign_id)
        if result['finished'] == result['total']:
            return result
        if result['active'] == 0:
            if result['queued'] and ledger.admission_block_reason(campaign_id) is None:
                continue
            return result
        time.sleep(.05)
