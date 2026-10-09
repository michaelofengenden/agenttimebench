"""Small durable orchestration layer for the approved fifty-task natural pilot.

The transport owns provider access, inert container allocation, resource/image
verification and host-side release gates. This module never accepts credentials,
constructs a model command, or retries a remote mutation. NaturalLedger supplies
serialized permanent admission. Local intent files precede every remote action.
A restart may reconcile existing work; it cannot stage or release it again.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Protocol
from uuid import uuid4

from .evidence import canonical_json
from .jsonio import loads_strict
from .natural_inputs import SELECTED_SLOTS
from .natural_ledger import NaturalLedger, document_sha256, validate_identity, validate_manifest

STATE_ROOTS = ('home', 'config', 'tmp', 'work', 'xdg', 'capture')
ASSISTANT_TOOLS = tuple('mcp__assistantbench__' + name for name in
    ('observe', 'noop', 'scroll', 'fill', 'select_option', 'click', 'press', 'go_back', 'goto', 'send_msg_to_user'))
AUTH_FILENAMES = {'.credentials.json', 'credentials.json', 'auth.json', 'tokens.json'}
_SOURCE = Path(__file__).resolve()
_LOADED_SHA = hashlib.sha256(_SOURCE.read_bytes()).hexdigest()


class ControllerError(ValueError):
    """Stable metadata code; never copy a transport exception into evidence."""


@dataclass(frozen=True)
class ArchiveCopy:
    receipt_bytes: bytes
    credential_scan_verified: bool


class Transport(Protocol):
    """Privileged adapter boundary; all methods must bind the exact identity.

    allocate creates only an inert, independently image/resource-checked container.
    prepare installs pristine worker state and never releases subject input.
    release_barrier opens already staged gates outside every subject namespace;
    its result is not native-onset evidence and it must never retry automatically.
    copy_archive copies STATE_ROOTS to the fresh independent destination, scans
    actual authentication bytes privately and returns the original receipt bytes.
    observe and stop_proof are read-only; they must not start, continue, fork,
    stop or replace a subject. Provider staging and stopping remain explicit.
    """
    def allocate(self, intent: dict) -> dict: ...
    def prepare(self, spec: dict, input_bytes: bytes) -> dict: ...
    def copy_archive(self, identity: dict, phase: str, destination: Path) -> ArchiveCopy: ...
    def release_barrier(self, barrier_id: str, members: list[dict]) -> None: ...
    def observe(self, intent: dict, identity: dict | None) -> dict | None: ...
    def stop_proof(self, identity: dict) -> dict: ...


def native_json(value):
    """Worker receipt encoding, distinct from the ledger's compact JSON hash."""
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ControllerError('invalid_digest')
    return value


def _read(path):
    path = Path(path)
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ControllerError('unsafe_controller_path')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ControllerError('unsafe_controller_file')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            return stream.read()
    finally:
        os.close(descriptor)


def _json(path):
    return loads_strict(_read(path))


def _sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _new(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    _sync_directory(Path(path).parent)


def _receipt(path, value):
    data = canonical_json(value)
    if Path(path).exists():
        if _read(path) != data:
            raise ControllerError('immutable_receipt_changed')
    else:
        _new(path, data)


def _replace(path, value):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '-' + str(uuid4()))
    _new(temporary, canonical_json(value))
    os.replace(temporary, path); _sync_directory(path.parent)


def _time(value):
    if not isinstance(value, str): raise ControllerError('invalid_observation_clock')
    try: parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError: raise ControllerError('invalid_observation_clock') from None
    if parsed.utcoffset() is None: raise ControllerError('invalid_observation_clock')
    return parsed


def scan_archive(root):
    """Independently inspect all copied state, refusing extra roots/unsafe links."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir(): raise ControllerError('unsafe_archive_root')
    root = root.resolve()
    if {p.name for p in root.iterdir()} != set(STATE_ROOTS):
        raise ControllerError('archive_state_roots_changed')
    result = {}
    for name in STATE_ROOTS:
        directory = root / name
        if directory.is_symlink() or not directory.is_dir(): raise ControllerError('unsafe_archive_root')
        for path in (directory, *sorted(directory.rglob('*'))):
            relative = path.relative_to(root).as_posix(); info = path.lstat()
            if path.name.lower() in AUTH_FILENAMES: raise ControllerError('authentication_store_in_archive')
            if stat.S_ISLNK(info.st_mode):
                target = path.resolve()
                if not target.is_relative_to(root) or not target.exists(): raise ControllerError('unsafe_archive_link')
                result[relative] = {'type': 'link', 'target': target.relative_to(root).as_posix()}
            elif stat.S_ISDIR(info.st_mode):
                result[relative] = {'type': 'directory', 'mode': stat.S_IMODE(info.st_mode)}
            elif stat.S_ISREG(info.st_mode):
                result[relative] = {'type': 'file', 'mode': stat.S_IMODE(info.st_mode),
                                    'bytes': info.st_size, 'sha256': _file_sha(path)}
            else: raise ControllerError('unsupported_archive_object')
    return result


def _native_archive_evidence(report, session_id, inventory):
    """Bind worker-validated native bytes to the independently scanned inventory.

    The pinned worker validates original message contents and observed children.
    Here every reported store must exist with the same hash in our separate copy.
    A copied directory alone is neither native-session nor restoration evidence.
    """
    evidence = report.get('native_session_evidence')
    keys = {'original_native_stores_verified', 'restoration_proven', 'stream_sha256', 'session_id', 'parent', 'children'}
    if (report.get('native_sessions_verified') is not True or type(evidence) is not dict or set(evidence) != keys
            or evidence['original_native_stores_verified'] is not True or evidence['restoration_proven'] is not False
            or evidence['session_id'] != session_id or type(evidence['children']) is not dict):
        raise ControllerError('native_session_evidence_unverified')

    def bind_file(path, digest):
        entry = inventory.get(path)
        if (type(entry) is not dict or entry.get('type') != 'file' or type(entry.get('bytes')) is not int
                or entry['bytes'] <= 0 or entry.get('sha256') != _digest(digest)):
            raise ControllerError('native_session_archive_binding_mismatch')

    def bind_store(store, expected_path=None):
        if (type(store) is not dict or set(store) != {'path', 'sha256', 'message_count'}
                or type(store['path']) is not str or type(store['message_count']) is not int or store['message_count'] < 2
                or (expected_path is not None and store['path'] != expected_path)):
            raise ControllerError('native_session_store_binding_mismatch')
        bind_file(store['path'], store['sha256'])
        return store['path']

    bind_file('capture/stream.jsonl', evidence['stream_sha256'])
    parent = bind_store(evidence['parent'])
    if re.fullmatch(r'config/projects/[A-Za-z0-9-]+/' + re.escape(session_id) + r'\.jsonl', parent) is None:
        raise ControllerError('native_session_parent_path_mismatch')
    paths = {parent}
    for child_id, store in evidence['children'].items():
        if type(child_id) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,96}', child_id) is None:
            raise ControllerError('native_session_child_identity_mismatch')
        paths.add(bind_store(store, parent[:-6] + '/subagents/agent-' + child_id + '.jsonl'))
    archived = {path for path in inventory if path.startswith('config/projects/') and path.endswith('.jsonl')}
    if archived != paths:
        raise ControllerError('native_session_archive_store_set_mismatch')
    return document_sha256(evidence)


def _template(task, template, manifest):
    keys = {'schema_version', 'executor', 'arm', 'task_id', 'family_id', 'preparation_contract_sha256', 'input_sha256',
            'model', 'effort', 'route', 'tool_policy', 'resources', 'runtime_pins'}
    if type(template) is not dict or set(template) != keys: raise ControllerError('worker_template_shape')
    family = task['id'].split('-')[0]; closed = family in ('gpqa', 'hle')
    expected = {'schema_version': 'agenttime.natural-worker.v1', 'executor': 'claude-natural-v1', 'arm': 'natural',
                'task_id': task['id'], 'family_id': family, 'preparation_contract_sha256': task['contract_sha256'],
                'input_sha256': task['input_sha256'], **{k: manifest['agent'][k] for k in ('model', 'effort', 'route')}}
    if any(template[k] != value for k, value in expected.items()): raise ControllerError('worker_template_binding')
    policy = {'native_tools': ['Agent', 'WebSearch', 'WebFetch'] if family == 'browsecomp' else [],
              'mcp_tools': list(ASSISTANT_TOOLS) if family == 'assistant' else [], 'native_subagents': family == 'browsecomp'}
    resources = {'allocation_scope': 'no_task_compute' if closed else 'natural_subject',
                 'cpus': None if closed else 4, 'memory_gib': None if closed else 16, 'gpu_count': 0}
    if template['tool_policy'] != policy or template['resources'] != resources:
        raise ControllerError('worker_policy_changed')
    pins = template['runtime_pins']
    if type(pins) is not dict or set(pins) != {'cli_version', 'cli_sha256', 'image_sha256', 'worker_sha256',
                                            'native_session_sha256', 'python_sha256', 'bridge_sha256'}:
        raise ControllerError('worker_runtime_pins_shape')
    if pins['cli_version'] != '2.1.280' or pins['image_sha256'] != 'sha256:' + task['runtime_pins']['image_sha256']:
        raise ControllerError('worker_runtime_pin_mismatch')
    for name, value in pins.items():
        if name in ('cli_version', 'image_sha256'): continue
        if name == 'bridge_sha256' and family != 'assistant':
            if value is not None: raise ControllerError('unexpected_bridge_pin')
        elif value != task['runtime_pins'].get(name, manifest['runtime_pins'].get(name)):
            raise ControllerError('worker_runtime_pin_mismatch')
    if document_sha256(template) != task['runtime_pins'].get('spec_template_sha256'):
        raise ControllerError('worker_template_changed')


class NaturalController:
    @staticmethod
    def freeze(root, campaign_id, manifest, tasks, runtime_sources, qualification_proof):
        """Save already-compiled inputs; source data compilation happens upstream.

        tasks maps every selected slot to contract_bytes, input_bytes and a worker
        template without attempt/session/container identity. All bytes and source
        paths are pinned before admission; no task or model is run here.
        """
        validate_manifest(manifest)
        if (manifest['capacity'] != 50 or {t['id'] for t in manifest['tasks']} != set(SELECTED_SLOTS)
                or len(manifest['tasks']) != 50 or set(tasks) != set(SELECTED_SLOTS)
                or {k: manifest['agent'][k] for k in ('model', 'effort', 'route')} !=
                   {'model': 'claude-opus-5-5[1m]', 'effort': 'max', 'route': 'subscription'}):
            raise ControllerError('outside_approved_fifty_task_pilot')
        if not isinstance(campaign_id, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', campaign_id):
            raise ControllerError('invalid_campaign_id')
        if type(qualification_proof) is not bytes or _sha(qualification_proof) != manifest['qualification_proof_sha256']:
            raise ControllerError('qualification_proof_pin_mismatch')
        if not {'cli_sha256', 'worker_sha256', 'controller_sha256'}.issubset(runtime_sources):
            raise ControllerError('runtime_source_files_required')
        sources = {}
        for key, path in runtime_sources.items():
            source = Path(path).resolve(strict=True)
            if key not in manifest['runtime_pins'] or _file_sha(source) != manifest['runtime_pins'][key]:
                raise ControllerError('runtime_source_pin_mismatch')
            sources[key] = str(source)
        if sources['controller_sha256'] != str(_SOURCE) or manifest['runtime_pins']['controller_sha256'] != _LOADED_SHA:
            raise ControllerError('controller_source_pin_mismatch')
        for task in manifest['tasks']:
            item = tasks[task['id']]
            if (set(item) != {'contract_bytes', 'input_bytes', 'template'} or type(item['input_bytes']) is not bytes
                    or type(item['contract_bytes']) is not bytes or _sha(item['input_bytes']) != task['input_sha256']
                    or _sha(item['contract_bytes']) != task['contract_sha256']):
                raise ControllerError('compiled_input_pin_mismatch')
            _template(task, item['template'], manifest)
        root = Path(root).absolute()
        if root.exists() or root.is_symlink(): raise ControllerError('controller_store_already_exists')
        root.mkdir(mode=0o700)
        root = root.resolve()
        for name in ('inputs', 'contracts', 'templates', 'attempts', 'observations', 'grade-queue'):
            (root / name).mkdir(mode=0o700)
        _new(root / 'manifest.json', canonical_json(manifest))
        _new(root / 'runtime-sources.json', canonical_json(sources))
        _new(root / 'qualification-proof.bin', qualification_proof)
        _new(root / 'binding.json', canonical_json({'campaign_id': campaign_id, 'manifest_sha256': document_sha256(manifest),
            'runtime_sources_sha256': document_sha256(sources)}))
        for task in manifest['tasks']:
            item = tasks[task['id']]
            for name, key, data in (('inputs', 'input_sha256', item['input_bytes']), ('contracts', 'contract_sha256', item['contract_bytes'])):
                path = root / name / (task[key] + '.json')
                if path.exists():
                    if _read(path) != data: raise ControllerError('artifact_hash_collision')
                else: _new(path, data)
            _new(root / 'templates' / (task['id'] + '.json'), canonical_json(item['template']))
        _sync_directory(root)

    def __init__(self, root, ledger: NaturalLedger, transport: Transport, *, qualification_verifier=None, now=None):
        if Path(root).is_symlink(): raise ControllerError('unsafe_controller_root')
        self.root = Path(root).resolve(strict=True); self.ledger = ledger; self.transport = transport
        self.qualify = qualification_verifier; self.now = now or (lambda: datetime.now(timezone.utc))
        binding = _json(self.root / 'binding.json')
        self.campaign_id = binding['campaign_id']; self.manifest = _json(self.root / 'manifest.json')
        if document_sha256(self.manifest) != binding['manifest_sha256']: raise ControllerError('manifest_pin_mismatch')
        self.manifest_sha = binding['manifest_sha256']
        self.sources_sha = binding['runtime_sources_sha256']
        self.tasks = {task['id']: task for task in self.manifest['tasks']}
        ledger.create_campaign(self.campaign_id, self.manifest)
        if ledger.campaign(self.campaign_id)['manifest_sha256'] != self.manifest_sha:
            raise ControllerError('ledger_manifest_mismatch')

    @contextmanager
    def _lock(self):
        descriptor = os.open(self.root / 'controller.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN); os.close(descriptor)

    def _verify_store(self):
        try:
            if _file_sha(_SOURCE) != _LOADED_SHA or self.manifest['runtime_pins']['controller_sha256'] != _LOADED_SHA:
                raise ControllerError('loaded_controller_source_changed')
            if document_sha256(_json(self.root / 'manifest.json')) != self.manifest_sha:
                raise ControllerError('manifest_pin_mismatch')
            sources = _json(self.root / 'runtime-sources.json')
            if (document_sha256(sources) != self.sources_sha
                    or not {'cli_sha256', 'worker_sha256', 'controller_sha256'}.issubset(sources)):
                raise ControllerError('runtime_source_registry_changed')
            for key, path in sources.items():
                if _file_sha(path) != self.manifest['runtime_pins'][key]: raise ControllerError('runtime_source_changed')
            if _sha(_read(self.root / 'qualification-proof.bin')) != self.manifest['qualification_proof_sha256']:
                raise ControllerError('qualification_proof_changed')
            for task in self.tasks.values():
                for folder, key in (('inputs', 'input_sha256'), ('contracts', 'contract_sha256')):
                    if _sha(_read(self.root / folder / (task[key] + '.json'))) != task[key]:
                        raise ControllerError('compiled_input_changed')
                _template(task, _json(self.root / 'templates' / (task['id'] + '.json')), self.manifest)
        except (ControllerError, ValueError, OSError, KeyError, TypeError) as exc:
            self.ledger.pause('controller_source_or_input_integrity')
            raise ControllerError('controller_source_or_input_integrity') from None

    def _directory(self, attempt_id):
        if not re.fullmatch('[0-9a-f-]{36}', attempt_id): raise ControllerError('invalid_attempt_id')
        return self.root / 'attempts' / attempt_id

    def _observation(self, row):
        path = self.root / 'observations' / (row['task_id'] + '.json')
        return _json(path) if path.exists() else {'attempt_id': row['id'], 'native_release_verified': False,
            'stop_verified': False, 'archive': {'status': 'none'}, 'grade': {'status': 'not_started'}}

    def _publish(self, row, **values):
        observation = self._observation(row); observation.update(values)
        _replace(self.root / 'observations' / (row['task_id'] + '.json'), observation)

    def _hold(self, row, code, *, quarantine=True):
        if quarantine and row['identity'] is not None: self.ledger.quarantine(row['id'], code)
        self._publish(row, observation_invalid=True, blocked=True, issue_code=code, admission_ready=False)
        _replace(self._directory(row['id']) / 'controller-state.json', {'phase': 'held', 'code': code})

    def stage_all(self, *, max_workers=50):
        with self._lock():
            self._verify_store()
            if (self.root / 'stage-intent.json').exists() or self.ledger.attempts(self.campaign_id):
                raise ControllerError('staging_already_claimed_reconcile_only')
            _new(self.root / 'stage-intent.json', canonical_json({'campaign_id': self.campaign_id,
                'manifest_sha256': self.manifest_sha, 'automatic_retry': False}))
            rows = []
            for _ in self.tasks:
                row = self.ledger.reserve(self.campaign_id)
                if row is None: raise ControllerError('fleet_admission_incomplete')
                directory = self._directory(row['id']); directory.mkdir(mode=0o700)
                (directory / 'events').mkdir(mode=0o700)
                template = _json(self.root / 'templates' / (row['task_id'] + '.json'))
                intent = {'campaign_id': self.campaign_id, 'manifest_sha256': self.manifest_sha, 'attempt_id': row['id'],
                    'task_id': row['task_id'], 'session_id': str(uuid4()), 'dispatch_id': str(uuid4()),
                    'resources': template['resources'], 'runtime_pins': template['runtime_pins'],
                    'controller_intent_path': str(directory / 'dispatch-intent.json')}
                _new(directory / 'dispatch-intent.json', canonical_json(intent))
                rows.append(row); self._publish(row)
            with ThreadPoolExecutor(max_workers=max(1, min(50, max_workers))) as pool:
                statuses = list(pool.map(self._stage_one, rows))
            return {'selected': len(rows), 'ready': statuses.count('ready'), 'held': statuses.count('held')}

    def _stage_one(self, row):
        directory = self._directory(row['id'])
        try:
            intent = _json(directory / 'dispatch-intent.json')
            identity = self.transport.allocate(intent)
            validate_identity(identity)
            if identity['session_id'] != intent['session_id']: raise ControllerError('allocated_session_mismatch')
            _receipt(directory / 'identity.json', identity)
            if not self.ledger.claim(row['id'], identity): raise ControllerError('permanent_claim_refused')
            row = self.ledger.attempt(row['id'])
            template = _json(self.root / 'templates' / (row['task_id'] + '.json'))
            _template(self.tasks[row['task_id']], template, self.manifest)
            spec = {**template, 'attempt_id': row['id'], 'session_id': identity['session_id'], 'identity': identity}
            spec_bytes = native_json(spec); _new(directory / 'spec.json', spec_bytes)
            input_bytes = _read(self.root / 'inputs' / (row['input_sha256'] + '.json'))
            # Read once and recheck immediately before the remote preparation call.
            if _sha(input_bytes) != row['input_sha256']: raise ControllerError('compiled_input_changed')
            prepared = self.transport.prepare(spec, input_bytes)
            if (prepared.get('state') != 'prepared' or prepared.get('attempt_id') != row['id']
                    or prepared.get('session_id') != identity['session_id'] or prepared.get('spec_sha256') != _sha(spec_bytes)
                    or prepared.get('study_admitted') is not False):
                raise ControllerError('prepared_receipt_mismatch')
            _digest(prepared.get('baseline_sha256')); _digest(prepared.get('inventory_sha256'))
            _receipt(directory / 'prepared.json', {k: prepared[k] for k in ('state', 'attempt_id', 'session_id',
                'spec_sha256', 'baseline_sha256', 'inventory_sha256', 'study_admitted')})
            archive, receipt, raw = self._archive(row, 'baseline')
            bindings = {'attempt_id': row['id'], 'session_id': identity['session_id'], 'identity_sha256': row['identity_sha256'],
                'spec_sha256': _sha(spec_bytes), 'contract_sha256': row['contract_sha256'], 'input_sha256': row['input_sha256'],
                'inventory_sha256': prepared['inventory_sha256']}
            if (receipt.get('schema_version') != 'agenttime.natural-worker-baseline.v1'
                    or _sha(raw) != prepared['baseline_sha256'] or any(receipt.get(k) != v for k, v in bindings.items())
                    or receipt.get('native_state') != 'empty_store_with_predeclared_session_id'):
                raise ControllerError('baseline_receipt_mismatch')
            if (_read(archive / 'capture/prepared-spec.json') != spec_bytes
                    or _read(archive / 'capture/prepared-input.json') != input_bytes):
                raise ControllerError('baseline_payload_mismatch')
            ack = {'schema_version': 'agenttime.natural-baseline-ack.v1', 'attempt_id': row['id'],
                'identity_sha256': row['identity_sha256'], 'session_id': identity['session_id'],
                'contract_sha256': row['contract_sha256'], 'input_sha256': row['input_sha256'],
                'inventory_sha256': receipt['inventory_sha256'], 'archive_manifest_sha256': _sha(raw),
                'archive_verified': True, 'independent_archive': True, 'archive_location': str(archive)}
            _receipt(directory / 'baseline-ack.json', ack)
            self.ledger.acknowledge_baseline(row['id'], identity, ack)
            _replace(directory / 'controller-state.json', {'phase': 'ready'})
            self._publish(row, admission_ready=True, observation_invalid=False, blocked=False)
            return 'ready'
        except Exception as exc:
            # Transport errors can follow a successful remote mutation. Never retry
            # or persist their text, which may contain private output or auth data.
            row = self.ledger.attempt(row['id'])
            code = str(exc) if isinstance(exc, ControllerError) else 'preparation_outcome_unknown'
            self._hold(row, code)
            return 'held'

    def _archive(self, row, phase):
        directory = self._directory(row['id']); destination = directory / (phase + '-archive')
        receipt_file = directory / (phase + '-archive-receipt.json')
        scan_file = directory / (phase + '-independent-scan.json')
        if destination.exists():
            # Read-only collection may resume after restart only from a complete
            # prior copy receipt. A partial copy is held, never overwritten.
            if not receipt_file.exists() or not scan_file.exists(): raise ControllerError('partial_archive_copy_held')
            raw = _read(receipt_file); scan = _json(scan_file)
            if (scan.get('credential_scan_verified') is not True or scan.get('receipt_sha256') != _sha(raw)):
                raise ControllerError('credential_scan_unverified')
        else:
            copy = self.transport.copy_archive(row['identity'], phase, destination)
            if not isinstance(copy, ArchiveCopy) or copy.credential_scan_verified is not True or type(copy.receipt_bytes) is not bytes:
                raise ControllerError('credential_scan_unverified')
            raw = copy.receipt_bytes
            _new(receipt_file, raw)
            _receipt(scan_file, {'credential_scan_verified': True, 'receipt_sha256': _sha(raw)})
        receipt = loads_strict(raw); inventory = scan_archive(destination)
        if receipt.get('inventory') != inventory or receipt.get('inventory_sha256') != _sha(native_json(inventory)):
            raise ControllerError('independent_archive_inventory_mismatch')
        # Scan twice around metadata comparison to reject concurrent mutation.
        if scan_archive(destination) != inventory: raise ControllerError('independent_archive_changed')
        return destination, receipt, raw

    def release_all(self):
        with self._lock():
            self._verify_store()
            if (self.root / 'barrier-intent.json').exists(): raise ControllerError('release_already_claimed_reconcile_only')
            if not (self.root / 'stage-intent.json').exists(): raise ControllerError('fleet_not_staged')
            rows = [self.ledger.attempt(r['id']) for r in self.ledger.attempts(self.campaign_id)]
            if len(rows) != 50: raise ControllerError('fleet_not_staged')
            proof = _read(self.root / 'qualification-proof.bin')
            if self.qualify is None or self.qualify(self.manifest, proof) is not True:
                raise ControllerError('native_qualification_unverified')
            if self.ledger.gate()['paused_reason'] is not None: raise ControllerError('shared_admission_paused')
            ready = [row for row in rows if row['state'] == 'claimed' and row['baseline_ack'] is not None
                     and _json(self._directory(row['id']) / 'controller-state.json').get('phase') == 'ready']
            if not ready: raise ControllerError('no_ready_attempts')
            # Recheck every independent baseline immediately before consuming grants.
            for row in ready:
                directory = self._directory(row['id'])
                template = _json(self.root / 'templates' / (row['task_id'] + '.json'))
                expected_spec = {**template, 'attempt_id': row['id'], 'session_id': row['identity']['session_id'],
                                 'identity': row['identity']}
                if _read(directory / 'spec.json') != native_json(expected_spec):
                    self.ledger.pause('controller_source_or_input_integrity')
                    raise ControllerError('staged_spec_changed')
                _, receipt, raw = self._archive(row, 'baseline')
                ack = row['baseline_ack']
                if ack['inventory_sha256'] != receipt['inventory_sha256'] or ack['archive_manifest_sha256'] != _sha(raw):
                    raise ControllerError('baseline_ack_changed')
            barrier_id = str(uuid4())
            _new(self.root / 'barrier-intent.json', canonical_json({'barrier_id': barrier_id,
                'campaign_id': self.campaign_id, 'manifest_sha256': self.manifest_sha,
                'attempt_ids': [row['id'] for row in ready], 'automatic_retry': False}))
            members = []
            for row in ready:
                if not self.ledger.authorize_release(row['id'], row['identity']):
                    raise ControllerError('one_use_release_refused')
                directory = self._directory(row['id']); spec_bytes = _read(directory / 'spec.json')
                authorization = {'schema_version': 'agenttime.natural-release-authorization.v1',
                    'attempt_id': row['id'], 'identity_sha256': row['identity_sha256'], 'spec_sha256': _sha(spec_bytes),
                    'input_sha256': row['input_sha256'], 'baseline_ack_sha256': document_sha256(row['baseline_ack']),
                    'authorization_id': str(uuid4()), 'campaign_id': self.campaign_id, 'manifest_sha256': self.manifest_sha,
                    'ledger_authorized': True}
                _receipt(directory / 'release-authorization.json', authorization)
                members.append({'identity': row['identity'], 'baseline_ack': row['baseline_ack'], 'authorization': authorization})
            try:
                self.transport.release_barrier(barrier_id, members)
                _receipt(self.root / 'barrier-submitted.json', {'barrier_id': barrier_id, 'members': len(members)})
            except Exception:
                for row in ready: self._hold(row, 'barrier_outcome_unknown')
            return {'barrier_id': barrier_id, 'released_permissions': len(members), 'native_onset_count': 0}

    def _events(self, row, packet):
        events = packet.get('events')
        if type(events) is not list: raise ControllerError('native_events_unavailable')
        directory = self._directory(row['id']); old = sorted((directory / 'events').glob('*.json'))
        if len(events) < len(old): raise ControllerError('stale_native_event_prefix')
        clock = packet.get('clock_id')
        if events and (not isinstance(clock, str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,160}', clock)):
            raise ControllerError('native_clock_unavailable')
        if events: _receipt(directory / 'native-clock.json', {'clock_id': clock})
        common = {'sequence', 'kind', 'monotonic_ns', 'audit_utc', 'session_id', 'clock_id'}
        details = {'root_process_started': {'pid', 'owner_pid'}, 'prompt_delivery_completed': {'bytes'},
            'prompt_released': {'input_sha256', 'attempt_id'}, 'answer_sealed': {'sha256', 'native_submission_monotonic_ns'},
            'native_result': {'sealed_answer_sha256'}, 'root_process_exited': {'exit_code'}, 'owned_work_drained': set(),
            'native_terminal': {'sealed_answer_sha256'}, 'native_event': {'metadata'}, 'owned_process_reaped': {'pid', 'exit_code'},
            'execution_stop_requested': {'reason'}, 'journal_closed': set()}
        previous_ns = -1; result = {}
        for sequence, event in enumerate(events, 1):
            if (type(event) is not dict or event.get('kind') not in details or set(event) - common - details[event['kind']]
                    or not common.issubset(event) or event.get('sequence') != sequence or type(event.get('sequence')) is not int
                    or event.get('session_id') != row['identity']['session_id'] or type(event.get('monotonic_ns')) is not int
                    or event['monotonic_ns'] < 0 or event['monotonic_ns'] < previous_ns or event.get('clock_id') != clock):
                raise ControllerError('native_event_identity_or_order')
            _time(event.get('audit_utc')); previous_ns = event['monotonic_ns']
            required_details = details[event['kind']] - {'native_submission_monotonic_ns'}
            if not required_details.issubset(event): raise ControllerError('incomplete_native_event_metadata')
            for key in ('pid', 'owner_pid', 'bytes'):
                if key in event and (type(event[key]) is not int or event[key] < 0):
                    raise ControllerError('invalid_native_event_metadata')
            if 'exit_code' in event and type(event['exit_code']) is not int:
                raise ControllerError('invalid_native_event_metadata')
            if event['kind'] == 'prompt_released':
                if event.get('input_sha256') != row['input_sha256'] or event.get('attempt_id', row['id']) != row['id']:
                    raise ControllerError('native_release_input_changed')
            if event['kind'] == 'native_event':
                metadata = event.get('metadata')
                if type(metadata) is not dict or set(metadata) - {'type', 'subtype', 'session_id', 'model', 'tool_names'}:
                    raise ControllerError('native_event_not_metadata')
            if event['kind'] in ('prompt_released', 'answer_sealed', 'native_result', 'root_process_exited', 'owned_work_drained', 'native_terminal'):
                if event['kind'] in result: raise ControllerError('duplicate_native_boundary')
                result[event['kind']] = event
            _receipt(directory / 'events' / f'{sequence:08}.json', event)
        return result

    def _observe(self, row, packet):
        if type(packet) is not dict or packet.get('identity') != row['identity']:
            raise ControllerError('worker_identity_mismatch')
        worker = packet.get('worker')
        if (type(worker) is not dict or worker.get('schema_version') != 'agenttime.natural-worker-observation.v1'
                or any(worker.get(key) != expected for key, expected in (('attempt_id', row['id']),
                    ('task_id', row['task_id']), ('session_id', row['identity']['session_id'])))):
            raise ControllerError('worker_observation_mismatch')
        observed = _time(worker.get('source_observed_at')); now = self.now()
        events = self._events(row, packet)
        release = events.get('prompt_released')
        if release:
            event = {'kind': 'prompt_released', 'attempt_id': row['id'], 'identity_sha256': row['identity_sha256'],
                'input_sha256': row['input_sha256'], 'clock_id': packet['clock_id'], 'monotonic_ns': release['monotonic_ns'],
                'event_sha256': document_sha256(release)}
            if not row['release_authorized']:
                self.ledger.pause('native_release_without_controller_authorization')
                raise ControllerError('unauthorized_native_release')
            self.ledger.mark_prompt_released(row['id'], row['identity'], event)
        values = {'heartbeat_at': worker['source_observed_at'], 'observation_invalid': not 0 <= (now-observed).total_seconds() <= 30,
                  'blocked': False, 'admission_ready': not release and row['baseline_ack'] is not None}
        if release:
            values.update(native_release_verified=True, prompt_released_at=release['audit_utc'], admission_ready=False)
            current = worker.get('worker_monotonic_ns')
            if type(current) is int and current >= release['monotonic_ns']:
                values['elapsed_seconds'] = (current-release['monotonic_ns'])/1e9
        for name, key in (('native_terminal', 'native_terminal_at'), ('owned_work_drained', 'owned_work_drained_at')):
            if name in events: values[key] = events[name]['audit_utc']
        prior = self._observation(row)
        if prior.get('heartbeat_at') and observed < _time(prior['heartbeat_at']):
            values['heartbeat_at'] = prior['heartbeat_at']; values['observation_invalid'] = True
        self._publish(row, **values)
        report = packet.get('report')
        return (report, events, packet.get('clock_id')) if report is not None else None

    def _finish_if_proven(self, row, report, events, clock):
        # Stop and archival are also required for failed attempts. A missing answer
        # or inadmissible timing must not strand an otherwise drained session.
        required = ('prompt_released', 'root_process_exited', 'owned_work_drained')
        if any(name not in events for name in required): raise ControllerError('native_drain_incomplete')
        directory = self._directory(row['id']); spec_bytes = _read(directory / 'spec.json')
        authorization = _json(directory / 'release-authorization.json')
        bindings = {'schema_version': 'agenttime.natural-worker-report.v1', 'attempt_id': row['id'],
            'session_id': row['identity']['session_id'], 'identity_sha256': row['identity_sha256'],
            'spec_sha256': _sha(spec_bytes), 'input_sha256': row['input_sha256'],
            'authorization_sha256': document_sha256(authorization)}
        if (type(report) is not dict or any(report.get(k) != v for k, v in bindings.items())
                or report.get('state') not in ('captured', 'held') or report.get('archive_verified') is not True
                or type(report.get('issues')) is not list
                or any(type(code) is not str or not re.fullmatch('[a-z0-9_]{1,96}', code) for code in report['issues'])):
            raise ControllerError('worker_final_report_unverified')
        execution = report.get('execution', {})
        mapping = {'prompt_released_monotonic_ns': 'prompt_released', 'root_exit_monotonic_ns': 'root_process_exited',
            'owned_work_drained_monotonic_ns': 'owned_work_drained'}
        if (type(execution) is not dict or execution.get('session_id') != row['identity']['session_id']
                or type(execution.get('timing_valid')) is not bool or type(execution.get('cancelled')) is not bool
                or type(execution.get('issues')) is not list
                or any(type(code) is not str or not re.fullmatch('[a-z0-9_]{1,96}', code) for code in execution['issues'])
                or type(execution.get('root_exit_code')) is not int
                or execution.get('clock_id', clock) != clock or any(execution.get(k) != events[v]['monotonic_ns'] for k,v in mapping.items())
                or events['root_process_exited'].get('exit_code') != execution['root_exit_code']):
            raise ControllerError('native_drain_unverified')
        start = events['prompt_released']['monotonic_ns']; drain = events['owned_work_drained']['monotonic_ns']
        if not start <= events['root_process_exited']['monotonic_ns'] <= drain:
            raise ControllerError('native_drain_order')
        answer = events.get('answer_sealed'); terminal_event = events.get('native_terminal')
        answer_sha = _digest(answer.get('sha256')) if answer else None
        if (execution.get('sealed_answer_sha256') != answer_sha
                or any(events[name].get('sealed_answer_sha256') != answer_sha for name in ('native_result', 'native_terminal') if name in events)):
            raise ControllerError('sealed_answer_binding_mismatch')
        completed = report['state'] == 'captured'
        if completed:
            if (answer is None or terminal_event is None or report['issues'] != [] or execution['issues'] != []
                    or report.get('included_subscription_allowance_verified') is not True or report.get('timing_admissible') is not True
                    or report.get('native_sessions_verified') is not True
                    or execution['timing_valid'] is not True or execution['cancelled'] or execution['root_exit_code'] != 0):
                raise ControllerError('native_completion_unverified')
            terminal = terminal_event['monotonic_ns']
            result = events.get('native_result', answer)['monotonic_ns']
            if (execution.get('result_monotonic_ns') != result or execution.get('native_terminal_monotonic_ns') != terminal
                    or not start <= answer['monotonic_ns'] <= result <= drain or terminal != drain
                    or execution.get('runtime_seconds') != (terminal-start)/1e9):
                raise ControllerError('native_runtime_mismatch')
        elif report.get('timing_admissible') is not False:
            raise ControllerError('failed_timing_must_remain_unadmitted')
        worker_report_sha = document_sha256(report)
        _receipt(directory / 'worker-final-report.json', report)
        archive, receipt, raw = self._archive(row, 'final')
        if (receipt.get('schema_version') != 'agenttime.native-local-archive.v1' or receipt.get('verified') is not True
                or report.get('archive') != receipt
                or (answer_sha is not None and _file_sha(archive / 'capture/sealed-answer.txt') != answer_sha)
                or _file_sha(archive / 'capture/delivered-input.json') != row['input_sha256']
                or _read(archive / 'capture/prepared-spec.json') != spec_bytes):
            raise ControllerError('final_archive_binding_mismatch')
        native_evidence_sha = _native_archive_evidence(report, row['identity']['session_id'], receipt['inventory']) if completed else None
        _receipt(directory / 'final-archive-ack.json', {'schema_version': 'agenttime.natural-final-archive-ack.v1',
            'attempt_id': row['id'], 'identity_sha256': row['identity_sha256'], 'input_sha256': row['input_sha256'],
            'worker_report_sha256': worker_report_sha, 'archive_manifest_sha256': _sha(raw),
            'native_sessions_verified': completed, 'native_session_evidence_sha256': native_evidence_sha,
            'archive_inventory_sha256': receipt['inventory_sha256'], 'independent_archive_verified': True})
        self._publish(row, archive={'status': 'acknowledged', 'restore_status': 'unchecked'})
        stop = self.transport.stop_proof(row['identity'])
        if (type(stop) is not dict or stop.get('identity') != row['identity'] or stop.get('stop_verified') is not True
                or stop.get('container_running') is not False or stop.get('external_verification') is not True):
            return  # A local archive or native drain never releases the container claim.
        _time(stop.get('observed_at'))
        safe_stop = {key: stop[key] for key in ('identity', 'stop_verified', 'container_running', 'external_verification', 'observed_at')}
        stop_path = directory / 'external-stop.json'
        if stop_path.exists():
            previous = _json(stop_path)
            if {key: value for key, value in previous.items() if key != 'observed_at'} != {
                    key: value for key, value in safe_stop.items() if key != 'observed_at'}:
                raise ControllerError('external_stop_changed')
            _time(previous.get('observed_at')); safe_stop = previous
        _receipt(stop_path, safe_stop)
        final = {'attempt_id': row['id'], 'identity_sha256': row['identity_sha256'],
            'contract_sha256': row['contract_sha256'], 'input_sha256': row['input_sha256'],
            'outcome': 'completed' if completed else 'unknown_failure', 'timing_status': 'valid' if completed else 'invalid',
            'runtime_seconds': (terminal-start)/1e9 if completed else None, 'sealed_answer_sha256': answer_sha,
            'native_sessions_verified': completed, 'native_session_evidence_sha256': native_evidence_sha,
            'worker_report_sha256': worker_report_sha, 'failure_reason_codes': [] if completed else sorted(set(report['issues'] + execution['issues'])),
            'native_boundaries': {key: events[kind]['audit_utc'] if kind in events else None for kind, key in
                (('prompt_released', 'prompt_released_at'), ('root_process_exited', 'root_process_exited_at'),
                 ('owned_work_drained', 'owned_work_drained_at'), ('native_terminal', 'native_terminal_at'))},
            'native_clock_id': clock, 'archive_manifest_sha256': _sha(raw), 'archive_inventory_sha256': receipt['inventory_sha256'],
            'independent_archive_verified': True, 'stop_evidence': {'identity_sha256': row['identity_sha256'],
                'stop_verified': True, 'owned_work_drained': True, 'proof_sha256': document_sha256(safe_stop)}}
        _receipt(directory / 'final-report.json', final)
        self.ledger.finish(row['id'], row['identity'], final)
        self._repair_finished(self.ledger.attempt(row['id']))

    def _repair_finished(self, row):
        """Project durable final evidence after any interrupted publication."""
        directory = self._directory(row['id']); final = row['report']
        if _json(directory / 'final-report.json') != final:
            raise ControllerError('final_report_changed')
        worker_report = _json(directory / 'worker-final-report.json')
        stop = _json(directory / 'external-stop.json'); ack = _json(directory / 'final-archive-ack.json')
        if (document_sha256(worker_report) != final['worker_report_sha256']
                or document_sha256(stop) != final['stop_evidence']['proof_sha256']
                or ack.get('attempt_id') != row['id'] or ack.get('identity_sha256') != row['identity_sha256']
                or ack.get('worker_report_sha256') != final['worker_report_sha256']
                or ack.get('archive_manifest_sha256') != final['archive_manifest_sha256']
                or ack.get('archive_inventory_sha256') != final['archive_inventory_sha256']
                or ack.get('independent_archive_verified') is not True):
            raise ControllerError('final_receipt_binding_mismatch')
        boundaries = final['native_boundaries']
        for value in boundaries.values():
            if value is not None: _time(value)
        completed = final['outcome'] == 'completed'
        if completed:
            # A restart may project a prior verified acknowledgement, never infer
            # native completeness from a finished ledger row or archive directory.
            raw = _read(directory / 'final-archive-receipt.json'); receipt = loads_strict(raw)
            if (_sha(raw) != final['archive_manifest_sha256'] or worker_report.get('archive') != receipt
                    or receipt.get('inventory_sha256') != final['archive_inventory_sha256']
                    or _sha(native_json(receipt.get('inventory'))) != final['archive_inventory_sha256']):
                raise ControllerError('final_native_archive_receipt_mismatch')
            native_evidence_sha = _native_archive_evidence(worker_report, row['identity']['session_id'], receipt['inventory'])
            if (ack.get('native_sessions_verified') is not True or final.get('native_sessions_verified') is not True
                    or ack.get('native_session_evidence_sha256') != native_evidence_sha
                    or final.get('native_session_evidence_sha256') != native_evidence_sha):
                raise ControllerError('final_native_session_evidence_mismatch')
            self._queue_grade(row, final, directory / 'final-archive')
        prior = self._observation(row)
        grade = {'status': 'queued' if completed else 'unavailable'}
        if completed and prior.get('grade', {}).get('status') in ('queued', 'running', 'available', 'failed', 'unavailable'):
            grade = {key: value for key, value in prior['grade'].items() if key in ('status', 'value', 'scale')}
        restore = prior.get('archive', {}).get('restore_status', 'unchecked')
        if restore not in ('unchecked', 'verified', 'failed'): restore = 'unchecked'
        self._publish(row, native_release_verified=True, **{key: boundaries[key] for key in
            ('prompt_released_at', 'owned_work_drained_at', 'native_terminal_at')},
            heartbeat_at=stop['observed_at'], stop_verified=True, observation_invalid=False, admission_ready=False,
            blocked=not completed, issue_code=None if completed else 'execution_failed',
            elapsed_seconds=final['runtime_seconds'], archive={'status': 'acknowledged', 'restore_status': restore},
            timing={'status': final['timing_status'], 'runtime_seconds': final['runtime_seconds']},
            grade=grade)

    def _queue_grade(self, row, final, archive):
        _receipt(self.root / 'grade-queue' / (row['id'] + '.json'), {'schema_version': 'agenttime.natural-grade-queue.v1',
            'attempt_id': row['id'], 'task_id': row['task_id'], 'status': 'queued', 'sealed_answer_sha256': final['sealed_answer_sha256'],
            'sealed_answer_ref': str(archive / 'capture/sealed-answer.txt'),
            'contract_sha256': row['contract_sha256'], 'archive_inventory_sha256': final['archive_inventory_sha256']})

    def _reconcile_observation(self, diagnostic):
        row = self.ledger.attempt(diagnostic['id']); directory = self._directory(row['id'])
        try:
            if row['state'] == 'finished':
                self._repair_finished(row)
                return None
            if not (directory / 'dispatch-intent.json').exists():
                return None  # Reservation committed before local intent; no remote replay.
            packet = self.transport.observe(_json(directory / 'dispatch-intent.json'), row['identity'])
            if packet is None: raise ControllerError('worker_unreachable')
            if row['identity'] is None: raise ControllerError('allocation_identity_unresolved')
            final = self._observe(row, packet)
            return (row['id'], final) if final is not None else None
        except Exception as exc:
            code = str(exc) if isinstance(exc, ControllerError) else 'observation_outcome_unknown'
            self._hold(self.ledger.attempt(row['id']), code, quarantine=code not in ('worker_unreachable',))
            return None

    def _reconcile_final(self, pending):
        attempt_id, (report, events, clock) = pending
        try: self._finish_if_proven(self.ledger.attempt(attempt_id), report, events, clock)
        except Exception as exc:
            code = str(exc) if isinstance(exc, ControllerError) else 'final_collection_outcome_unknown'
            self._hold(self.ledger.attempt(attempt_id), code)

    def reconcile(self, *, max_workers=8):
        """Read/collect existing workers only. Never allocate, prepare or release."""
        if type(max_workers) is not int or not 1 <= max_workers <= 50:
            raise ControllerError('invalid_reconciliation_concurrency')
        with self._lock():
            # Publish all current native observations before any potentially large
            # archive transfer. Preserve their original timestamps, never poll time.
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                pending = [item for item in pool.map(self._reconcile_observation,
                    self.ledger.attempts(self.campaign_id)) if item is not None]
                list(pool.map(self._reconcile_final, pending))
            return self.observations()

    def observations(self):
        """Dashboard-only fields; no prompts, answers, reasoning, auth or forecasts."""
        result = {}
        allowed = {'attempt_id', 'native_release_verified', 'prompt_released_at', 'native_terminal_at', 'owned_work_drained_at',
                   'heartbeat_at', 'elapsed_seconds', 'stop_verified', 'observation_invalid', 'blocked', 'issue_code',
                   'admission_ready', 'archive', 'grade', 'timing'}
        for task_id in self.tasks:
            path = self.root / 'observations' / (task_id + '.json')
            result[task_id] = {k: v for k, v in _json(path).items() if k in allowed} if path.exists() else {}
        return result
