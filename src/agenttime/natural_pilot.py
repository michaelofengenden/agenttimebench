"""Explicit freeze, stage, release and observation for the approved 50-task pilot.

This thin driver does not provision workers or qualify itself. The caller owns
qualification evidence and process supervision. Reopening and observing never
stage, release, replace, resume or replay a subject. Public exports contain only
the existing dashboard's per-task observation metadata.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
import copy
import fcntl
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import sys
from uuid import UUID

from .evidence import canonical_json
from .jsonio import loads_strict
from .natural_controller import ASSISTANT_TOOLS, NaturalController, _file_sha, _new, _read, _replace, native_json
from .natural_grading import LocalGrader
from .natural_inputs import SELECTED_SLOTS, compile_input
from .natural_ledger import NaturalLedger, document_sha256, validate_identity, validate_manifest
from .pilot_status import merge_observation
from .ssh_transport import HostPlan, SSHTransport

SOURCE_ROOT = Path(__file__).resolve().parents[2]
DEPLOYED_FILES = ('src/agenttime/__init__.py', 'src/agenttime/native_session.py',
    'src/agenttime/natural_worker.py', 'src/agenttime/assistantbench_bridge.py',
    'src/agenttime/jsonio.py', 'src/agenttime/evidence.py', 'qualification/native_session/remote_gate.py')
SOURCE_FILES = {name + '_sha256': 'src/agenttime/' + filename + '.py' for name, filename in (
    ('worker', 'natural_worker'), ('controller', 'natural_controller'), ('ledger', 'natural_ledger'),
    ('native_session', 'native_session'), ('bridge', 'assistantbench_bridge'), ('transport', 'ssh_transport'),
    ('inputs', 'natural_inputs'), ('grader', 'natural_grading'), ('pilot', 'natural_pilot'),
    ('status', 'pilot_status'), ('evidence', 'evidence'), ('jsonio', 'jsonio'), ('package', '__init__'))}
SOURCE_FILES['remote_gate_sha256'] = 'qualification/native_session/remote_gate.py'


class PilotError(ValueError):
    """A stable metadata code, never an underlying exception or model output."""


def _sha(value): return hashlib.sha256(value).hexdigest()
def _object(path): return loads_strict(_read(path))
def _digest(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None: raise PilotError('invalid_pin')
    return value


def _roster_projection(value):
    """Science and task identity fields only; operational status stays mutable."""
    keys=('campaign_id','target_concurrency','arm','model','effort','route','duration_request','experiment_cap_seconds')
    task_keys=('task_id','label','benchmark','candidate_id','contract_path','contract_sha256','interface','required_resources')
    result={key:value[key] for key in keys}
    if (type(result['target_concurrency']) is not int or result['target_concurrency']!=50
            or result['arm']!='natural' or result['model']!='claude-opus-5-5' or result['effort']!='max'
            or result['route']!='michael_subscription' or result['duration_request'] is not None
            or result['experiment_cap_seconds'] is not None or type(value.get('tasks')) is not list
            or [row.get('task_id') for row in value['tasks']]!=list(SELECTED_SLOTS)):
        raise PilotError('outside_approved_pilot_roster')
    if type(result['campaign_id']) is not str or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',result['campaign_id']):
        raise PilotError('invalid_campaign_id')
    result['tasks']=[{key:row[key] for key in task_keys} for row in value['tasks']]
    for row in result['tasks']:
        if any(type(row[key]) is not str or not row[key] for key in task_keys if key!='required_resources'):
            raise PilotError('invalid_roster_metadata')
        if type(row['required_resources']) is not dict: raise PilotError('invalid_roster_metadata')
        _digest(row['contract_sha256'])
    return result


def freeze_roster(public_manifest_path, immutable_roster_path):
    """Publish a new immutable roster copy, independent of live dashboard flags."""
    try:
        value=_roster_projection(_object(public_manifest_path));data=canonical_json(value)
        _new(Path(immutable_roster_path),data)
        return _sha(data)
    except PilotError: raise
    except Exception: raise PilotError('roster_freeze_failed') from None


def private_token_supplier(path):
    """Return a lazy reader. It never stores, logs or places the token in argv."""
    path = Path(path).expanduser().absolute()
    def supply():
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o077 or not 1 <= info.st_size <= 65536
                        or path.resolve() != path): raise ValueError
                with os.fdopen(descriptor, 'rb', closefd=False) as stream: value = stream.read(65537)
                token = value.decode('utf-8').strip()
                if not token or len(value) > 65536 or any(c.isspace() or ord(c) < 33 for c in token): raise ValueError
                return token
            finally: os.close(descriptor)
        except Exception: raise PilotError('subscription_token_unavailable') from None
    return supply


@dataclass(frozen=True)
class PilotPlan:
    repo_root: Path
    roster_path: Path
    manifest: dict
    tasks: dict = field(repr=False)
    runtime_sources: dict
    hosts: tuple[HostPlan, ...]
    qualification_proof: bytes = field(repr=False)

    def freeze(self, root, *, grader_pins, assistant_python=None):
        """Freeze only. No ledger admission, worker allocation or authentication."""
        try:
            if type(grader_pins) is not dict or not grader_pins: raise PilotError('grader_pins_required')
            metadata = {'schema_version': 'agenttime.natural-pilot-driver.v1',
                'repo_root': str(self.repo_root), 'roster_path': str(self.roster_path),
                'hosts': [{k: str(v) if isinstance(v, Path) else v for k, v in asdict(h).items()} for h in self.hosts],
                'grader_pins': grader_pins, 'assistant_python': str(Path(assistant_python or sys.executable).absolute())}
            manifest = copy.deepcopy(self.manifest)
            manifest['runtime_pins']['pilot_metadata_sha256'] = document_sha256(metadata)
            NaturalController.freeze(root, self.manifest['cohort_id'], manifest, self.tasks,
                self.runtime_sources, self.qualification_proof)
            _new(Path(root)/'pilot-driver.json', canonical_json(metadata))
            return document_sha256(manifest)
        except PilotError: raise
        except Exception: raise PilotError('pilot_freeze_failed') from None


def compile_pilot(repo_root, frozen_roster_path, deployment_paths, cli_path, qualification_proof, *, key_path, known_hosts_path):
    """Compile exact native materials from the pinned roster and three receipts.

    frozen_roster_path must be the dedicated copy written by freeze_roster.
    Deployment receipts supply the Linux Python/image pins. They are not proof
    of readiness; only the separately injected qualifier can authorize release.
    """
    try:
        repo = Path(repo_root).resolve(strict=True); roster_path = Path(frozen_roster_path).absolute()
        roster_raw = _read(roster_path); roster = _roster_projection(loads_strict(roster_raw))
        if roster_raw!=canonical_json(roster): raise PilotError('dedicated_frozen_roster_required')
        if type(qualification_proof) is not bytes or not qualification_proof: raise PilotError('qualification_proof_required')
        sources = {name: SOURCE_ROOT/path for name, path in SOURCE_FILES.items()}
        sources.update(cli_sha256=Path(cli_path).absolute(), roster_sha256=roster_path)
        paths = list(deployment_paths)
        if len(paths) != 3: raise PilotError('three_deployment_receipts_required')
        receipts = []; hosts = []
        expected_files = {name: _file_sha(SOURCE_ROOT/name) for name in DEPLOYED_FILES}
        expected_files['bin/claude'] = _file_sha(sources['cli_sha256'])
        expected_bundle = _sha(native_json({'schema_version': 'agenttime.runtime-files.v1', 'files': expected_files}))
        for index, path in enumerate(paths):
            path = Path(path).absolute(); item = _object(path)
            if (item.get('source_files') != expected_files or item.get('runtime_bundle_sha256') != expected_bundle):
                raise PilotError('deployment_source_mismatch')
            _digest(item.get('python_sha256'))
            hosts.append(HostPlan(**{k: item[k] for k in ('host_id','address','daemon_id','remote_root',
                'runtime_bundle_sha256','image_sha256','host_config_sha256')}, key_path=key_path, known_hosts_path=known_hosts_path))
            sources[f'deployment_{index}_sha256'] = path; receipts.append(item)
        if (any(len({r[k] for r in receipts}) != 1 for k in ('python_sha256','image_sha256','runtime_bundle_sha256'))
                or any(len({r[k] for r in receipts}) != 3 for k in ('host_id','address','daemon_id'))):
            raise PilotError('deployment_fleet_mismatch')
        pins = {name: _file_sha(path) for name,path in sources.items()}
        pins.update(python_sha256=receipts[0]['python_sha256'], image_sha256=receipts[0]['image_sha256'][7:])
        tasks = {}; rows = []
        for row in roster['tasks']:
            slot = row['task_id']; relative = Path(row['contract_path'])
            if relative.is_absolute() or '..' in relative.parts: raise PilotError('unsafe_contract_path')
            path = repo/relative; raw = _read(path)
            if _sha(raw) != _digest(row['contract_sha256']): raise PilotError('contract_pin_mismatch')
            contract = loads_strict(raw)
            if (contract.get('slot_id') != slot or contract.get('candidate_id') != row.get('candidate_id')
                    or contract.get('intended_target',{}).get('hardware_allocation')!=row['required_resources']):
                raise PilotError('contract_identity_mismatch')
            input_bytes = native_json(compile_input(contract,repo)); family = slot.split('-')[0]; closed = family in ('gpqa','hle')
            template = {'schema_version':'agenttime.natural-worker.v1','executor':'claude-natural-v1','arm':'natural',
                'task_id':slot,'family_id':family,'preparation_contract_sha256':_sha(raw),'input_sha256':_sha(input_bytes),
                'model':'claude-opus-5-5[1m]','effort':'max','route':'subscription',
                'tool_policy':{'native_tools':['Agent','WebSearch','WebFetch'] if family=='browsecomp' else [],
                    'mcp_tools':list(ASSISTANT_TOOLS) if family=='assistant' else [],'native_subagents':family=='browsecomp'},
                'resources':{'allocation_scope':'no_task_compute' if closed else 'natural_subject',
                    'cpus':None if closed else 4,'memory_gib':None if closed else 16,'gpu_count':0},
                'runtime_pins':{'cli_version':'2.1.280','image_sha256':receipts[0]['image_sha256'],
                    **{k:pins[k] for k in ('cli_sha256','worker_sha256','native_session_sha256','python_sha256')},
                    'bridge_sha256':pins['bridge_sha256'] if family=='assistant' else None}}
            rows.append({'id':slot,'contract_sha256':_sha(raw),'input_sha256':_sha(input_bytes),
                'runtime_pins':{'image_sha256':pins['image_sha256'],'spec_template_sha256':document_sha256(template)}})
            tasks[slot] = {'contract_bytes':raw,'input_bytes':input_bytes,'template':template}
        manifest = {'schema_version':1,'executor':'claude-natural-v1','cohort_id':roster['campaign_id'],
            'agent':{'id':'opus-5.5-max-michael-subscription','model':'claude-opus-5-5[1m]','effort':'max','route':'subscription'},
            'arm':'natural','capacity':50,'automatic_replacement':False,'runtime_pins':pins,
            'qualification_proof_sha256':_sha(qualification_proof),'tasks':rows}
        validate_manifest(manifest)
        return PilotPlan(repo,roster_path,manifest,tasks,sources,tuple(hosts),qualification_proof)
    except PilotError: raise
    except Exception: raise PilotError('pilot_compilation_failed') from None


def _metadata(controller):
    try:
        value = _object(controller.root/'pilot-driver.json')
        if (value.get('schema_version') != 'agenttime.natural-pilot-driver.v1'
                or document_sha256(value) != controller.manifest['runtime_pins']['pilot_metadata_sha256']): raise ValueError
        return value
    except Exception: raise PilotError('pilot_metadata_changed') from None


def open_pilot(root, repo_root, ledger: NaturalLedger, token_supplier, *, qualification_verifier=None):
    """Connect explicit dependencies. This does not stage or release any task."""
    try:
        root = Path(root).resolve(strict=True); metadata = _object(root/'pilot-driver.json'); manifest = _object(root/'manifest.json')
        if document_sha256(metadata) != manifest['runtime_pins']['pilot_metadata_sha256']: raise PilotError('pilot_metadata_changed')
        if Path(repo_root).resolve(strict=True) != Path(metadata['repo_root']): raise PilotError('pilot_repository_changed')
        hosts = tuple(HostPlan(**h) for h in metadata['hosts'])
        transport = SSHTransport(hosts,token_supplier=token_supplier)
        controller = NaturalController(root,ledger,transport)
        grader = LocalGrader(root,repo_root,metadata['grader_pins'],assistant_python=metadata['assistant_python'])
        return NaturalPilot(controller,grader,qualification_verifier=qualification_verifier)
    except PilotError: raise
    except Exception: raise PilotError('pilot_open_failed') from None


def _public_observation(raw):
    """Keep only typed dashboard metadata, including within nested objects."""
    if type(raw) is not dict: return {'observation_invalid':True,'blocked':True}
    result = {}
    for key in ('native_release_verified','stop_verified','observation_invalid','blocked','admission_ready'):
        if type(raw.get(key)) is bool: result[key]=raw[key]
    attempt = raw.get('attempt_id')
    if attempt is not None:
        try:
            if str(UUID(attempt)) != attempt: raise ValueError
            result['attempt_id']=attempt
        except (ValueError,TypeError,AttributeError): result.update(observation_invalid=True,blocked=True)
    for key in ('prompt_released_at','native_terminal_at','owned_work_drained_at','heartbeat_at'):
        value=raw.get(key)
        if value is not None:
            try:
                if type(value) is not str or datetime.fromisoformat(value.replace('Z','+00:00')).utcoffset() is None: raise ValueError
                result[key]=value
            except ValueError: result.update(observation_invalid=True,blocked=True)
    def number(value): return type(value) in (float,int) and math.isfinite(value) and value>=0
    if number(raw.get('elapsed_seconds')): result['elapsed_seconds']=raw['elapsed_seconds']
    if type(raw.get('issue_code')) is str and re.fullmatch('[a-z][a-z0-9_]{0,95}',raw['issue_code']): result['issue_code']=raw['issue_code']
    for key, statuses in (('archive',('none','local','transferring','acknowledged','failed')),
                          ('grade',('not_started','queued','running','available','failed','unavailable')),
                          ('timing',('not_started','live','pending','valid','invalid'))):
        value=raw.get(key)
        if type(value) is not dict or value.get('status') not in statuses: continue
        out={'status':value['status']}
        if key=='archive' and value.get('restore_status') in ('unchecked','verified','failed'): out['restore_status']=value['restore_status']
        if key=='grade' and value['status']=='available':
            if number(value.get('value')) and value['value']<=1 and value.get('scale')=='fraction': out.update(value=value['value'],scale='fraction')
            else: out={'status':'unavailable'}
        if key=='timing' and number(value.get('runtime_seconds')): out['runtime_seconds']=value['runtime_seconds']
        result[key]=out
    return result


class NaturalPilot:
    def __init__(self, controller, grader, *, qualification_verifier=None):
        self.controller=controller;self.grader=grader;self.qualifier=qualification_verifier
        _metadata(controller);self._grade_cache={};self._grade_projection={}
        # Controller invokes this inside its release lock, closing the gap between
        # the driver's all-fifty precheck and the controller's ready-row selection.
        controller.qualify=self._release_qualified

    @contextmanager
    def _lock(self):
        descriptor=None
        try:
            descriptor=os.open(self.controller.root/'pilot-driver.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
            fcntl.flock(descriptor,fcntl.LOCK_EX);_metadata(self.controller);yield
        except PilotError: raise
        except Exception: raise PilotError('pilot_operation_unavailable') from None
        finally:
            if descriptor is not None: fcntl.flock(descriptor,fcntl.LOCK_UN);os.close(descriptor)

    def _qualified(self, manifest, proof):
        try:
            if (_sha(proof)!=manifest['qualification_proof_sha256'] or self.qualifier is None
                    or self.qualifier(manifest,proof) is not True): raise ValueError
        except Exception: raise PilotError('qualification_unverified') from None
        return True

    def _all_ready(self):
        try:
            c=self.controller;rows=[c.ledger.attempt(r['id']) for r in c.ledger.attempts(c.campaign_id)]
            if (len(rows)!=50 or {r['task_id'] for r in rows}!=set(SELECTED_SLOTS)
                    or len({r['id'] for r in rows})!=50): raise ValueError
            for row in rows:
                if str(UUID(row['id']))!=row['id']: raise ValueError
                task=c.tasks[row['task_id']];validate_identity(row['identity'])
                if (row['campaign_id']!=c.campaign_id or row['state']!='claimed' or row['holds_capacity'] is not True
                        or row['release_authorized'] is not False or row['prompt_release'] is not None or row['report'] is not None
                        or row['identity_sha256']!=document_sha256(row['identity'])
                        or any(row[k]!=task[k] for k in ('contract_sha256','input_sha256','runtime_pins'))
                        or _object(c.root/'attempts'/row['id']/'controller-state.json')!={'phase':'ready'}): raise ValueError
                NaturalLedger._validate_ack(row,row['baseline_ack'])
            if len({r['identity']['session_id'] for r in rows})!=50 or len({r['identity']['container_id'] for r in rows})!=50: raise ValueError
        except Exception: raise PilotError('exact_fifty_ready_required') from None

    def _release_qualified(self, manifest, proof):
        self._qualified(manifest,proof);self._all_ready();return True

    def stage(self, *, max_workers=50):
        with self._lock():
            self._qualified(self.controller.manifest,_read(self.controller.root/'qualification-proof.bin'))
            try: return self.controller.stage_all(max_workers=max_workers)
            except Exception: raise PilotError('staging_failed_reconcile_only') from None

    def release(self):
        with self._lock():
            self._all_ready()
            try: return self.controller.release_all()
            except PilotError: raise
            except Exception: raise PilotError('release_failed_reconcile_only') from None

    def observe_once(self, *, public_run=None, max_workers=8):
        """Collect existing attempts and grade sealed completions. Never dispatch."""
        with self._lock():
            try: self.controller.reconcile(max_workers=max_workers)
            except Exception: raise PilotError('observation_unavailable') from None
            observations=self.controller.observations()
            for diagnostic in self.controller.ledger.attempts(self.controller.campaign_id):
                row=self.controller.ledger.attempt(diagnostic['id']);report=row.get('report') or {}
                if (row['state']!='finished' or report.get('outcome')!='completed'
                        or report.get('timing_status')!='valid'): continue
                cache_key=(row['id'],document_sha256(report),self.grader.pins_sha256)
                if cache_key in self._grade_cache:
                    observations.setdefault(row['task_id'],{})['grade']=dict(self._grade_cache[cache_key]);continue
                grade={'status':'unavailable'}
                try:
                    receipt=self.grader.grade(row['id']);family=row['task_id'].split('-')[0]
                    if (receipt.get('schema_version')!='agenttime.natural-local-grade.v1'
                            or receipt.get('attempt_id')!=row['id'] or receipt.get('task_id')!=row['task_id']
                            or receipt.get('family_id')!=family or receipt.get('scorer_pins_sha256')!=self.grader.pins_sha256
                            or receipt.get('scale')!=[0,1]): raise ValueError
                    status=receipt.get('status');score=receipt.get('score')
                    if status=='available' and family in ('gpqa','assistant'):
                        if type(score) not in (float,int) or not math.isfinite(score) or not 0<=score<=1: raise ValueError
                        grade={'status':'available','value':score,'scale':'fraction'}
                    elif status in ('queued','unavailable') and score is None: grade={'status':status}
                    self._grade_cache[cache_key]=dict(grade)
                except Exception: pass  # A scorer failure cannot turn into zero or erase completion.
                observations.setdefault(row['task_id'],{})['grade']=grade
            safe={slot:_public_observation(observations.get(slot,{})) for slot in SELECTED_SLOTS}
            self._grade_projection={slot:(obs.get('attempt_id'),obs['grade']) for slot,obs in safe.items() if 'grade' in obs}
            if public_run is not None: self._export(public_run,safe)
            return safe

    def _export(self, public_run, observations):
        try:
            metadata=_metadata(self.controller);root=Path(public_run).absolute()
            if root.resolve()!=root or root.is_symlink(): raise ValueError
            path=root/'manifest.json'
            if document_sha256(_roster_projection(_object(path)))!=self.controller.manifest['runtime_pins']['roster_sha256']: raise ValueError
            directory=root/'observations'
            if directory.is_symlink(): raise ValueError
            directory.mkdir(mode=0o700,exist_ok=True)
            for slot in SELECTED_SLOTS:
                path=directory/(slot+'.json');incoming=_public_observation(observations.get(slot,{}))
                prior=_public_observation(_object(path)) if path.exists() else {}
                value=merge_observation(prior,incoming) if prior else incoming
                _replace(path,_public_observation(value))
        except PilotError: raise
        except Exception: raise PilotError('public_export_unavailable') from None

    def export_observations(self, public_run):
        with self._lock():
            observations=self.controller.observations()
            for slot,(attempt,grade) in self._grade_projection.items():
                if observations.get(slot,{}).get('attempt_id')==attempt:
                    observations[slot]['grade']=dict(grade)
            self._export(public_run,observations)

    def observe(self, stop_event, *, public_run=None, interval_seconds=5, max_workers=8):
        if type(interval_seconds) not in (float,int) or not math.isfinite(interval_seconds) or not 0<interval_seconds<=60:
            raise PilotError('invalid_observer_interval')
        while not stop_event.is_set():
            self.observe_once(public_run=public_run,max_workers=max_workers)
            stop_event.wait(interval_seconds)
