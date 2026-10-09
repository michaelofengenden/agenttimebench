"""Disposable continuation/fork qualification of sealed production canaries.

Only the four fixed synthetic inputs are accepted. inspect/prepare are model-free.
run accepts authentication on stdin and must run in a fresh, isolated Linux
container at /tmp/at-native with the reviewed production runtime. Nothing here
admits study results or reconstructs missing native stores/browser snapshots.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import types
import uuid

from agenttime import native_session as ns
from agenttime import natural_worker as worker

WORKER_SHA256 = 'c79949341ed2d2e6482f3d4353487eafb584e677de5114c4718b8b1244b222ae'
NATIVE_SESSION_SHA256 = 'a4321ba27a5a6372362d280096fee04a5a8c98a891c219568165de5b51274382'
SCHEMA = 'agenttime.production-synthetic-restore.v1'
RUNTIME_ROOT = '/tmp/at-native'
RUNTIME_CWD = RUNTIME_ROOT + '/work'
ASSISTANT_INPUT = ('This is a synthetic browser infrastructure check. Navigate to https://example.com/, '
    'observe its main heading, submit that heading with send_msg_to_user, then finish. Do not use other websites.')
BROWSER_LIMITS = ['JavaScript heap', 'sessionStorage', 'history', 'form state', 'downloads', 'live processes']


class ProbeError(ValueError):
    """Stable metadata-only errors, without provider text or task contents."""


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _regular(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ProbeError('unsafe_metadata_path')
    return path


def _loads(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: raise ProbeError('duplicate_metadata_key')
            out[key] = value
        return out
    try:
        value = json.loads(data, object_pairs_hook=pairs)
        json_bytes(value)
        return value
    except (ValueError, TypeError, UnicodeError):
        raise ProbeError('malformed_metadata') from None


def read_json(path):
    return _loads(_regular(path).read_bytes())


def read_rows(path):
    data = _regular(path).read_bytes()
    if not data or not data.endswith(b'\n'): raise ProbeError('incomplete_native_file')
    rows = [_loads(line) for line in data.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows): raise ProbeError('malformed_native_rows')
    return rows


def tree_digest(root):
    root = Path(root).absolute()
    if root.is_symlink() or root.resolve() != root or not root.is_dir(): raise ProbeError('unsafe_source_root')
    entries = {}
    for path in (root, *sorted(root.rglob('*'))):
        info = path.lstat(); relative = path.relative_to(root).as_posix()
        if stat.S_ISLNK(info.st_mode): entries[relative] = {'link': os.readlink(path)}
        elif stat.S_ISDIR(info.st_mode): entries[relative] = {'directory_mode': stat.S_IMODE(info.st_mode)}
        elif stat.S_ISREG(info.st_mode):
            entries[relative] = {'mode': stat.S_IMODE(info.st_mode), 'bytes': info.st_size, 'sha256': ns._file_sha(path)}
        else: raise ProbeError('unsupported_source_object')
    return sha(json_bytes(entries))


def _check_code(approved_runtime_worker_sha):
    if (not _digest(approved_runtime_worker_sha) or ns._source_pin() != NATIVE_SESSION_SHA256
            or worker._LOADED_SOURCE_SHA256 != approved_runtime_worker_sha
            or ns._file_sha(worker._SOURCE_PATH) != approved_runtime_worker_sha):
        raise ProbeError('production_runtime_pin_mismatch')


def _bridge(pin):
    path = worker._BRIDGE_PATH
    data = _regular(path).read_bytes()
    if not _digest(pin) or sha(data) != pin: raise ProbeError('approved_bridge_pin_mismatch')
    module = types.ModuleType('agenttime._approved_restore_browser_bridge')
    module.__file__ = str(path); module.__package__ = 'agenttime'
    exec(compile(data,str(path),'exec'),module.__dict__)
    if ns._file_sha(path) != pin: raise ProbeError('approved_bridge_pin_mismatch')
    return module


def _synthetic_input(family):
    if family == 'assistant':
        return {'type': 'user', 'message': {'role': 'user', 'content': [{'type':'text', 'text':ASSISTANT_INPUT}]}}
    case = {'gpqa':'closed_book', 'hle':'image', 'browsecomp':'web_subagent'}.get(family)
    if case is None: raise ProbeError('synthetic_family_required')
    return ns.qualification_contract('synthetic-restore-source', '00000000-0000-4000-8000-000000000001', case=case)['input']


def _browser_source(state, spec, inventory, approved_bridge_sha):
    if spec['family_id'] != 'assistant': return None
    path = state / 'capture/browser-state/browser-state.json'
    receipt_path = path.with_name('browser-state-receipt.json')
    if not path.exists() and not receipt_path.exists(): return None
    data = _regular(path).read_bytes(); receipt = read_json(receipt_path)
    snapshot = _loads(data)
    if (receipt.get('schema_version') != 1 or receipt.get('attempt_id') != spec['attempt_id']
            or receipt.get('sha256') != sha(data) or receipt.get('bytes') != len(data)
            or receipt.get('persisted') != ['cookies','localStorage','open_page_urls','active_page_index']
            or receipt.get('full_session_restore_qualified') is not False
            or snapshot.get('goal_sha256') != sha(ASSISTANT_INPUT.encode())
            or inventory.get('capture/browser-state/browser-state.json', {}).get('sha256') != sha(data)):
        raise ProbeError('browser_snapshot_unverified')
    _bridge(approved_bridge_sha)._validate_snapshot(snapshot)
    return {'path':'capture/browser-state/browser-state.json', 'sha256':sha(data), 'bytes':len(data)}


def verify_source(source, expected_tree_sha=None, *, approved_bridge_sha=None,
                  approved_runtime_worker_sha=WORKER_SHA256, approved_source_worker_sha=WORKER_SHA256):
    """Validate independent retained evidence without returning message contents."""
    _check_code(approved_runtime_worker_sha); source = Path(source).absolute(); before = tree_digest(source)
    if expected_tree_sha is not None and before != expected_tree_sha: raise ProbeError('source_tree_changed')
    state = source / 'final-state'
    spec = read_json(source / 'spec.json'); original_input = _regular(source / 'input.json').read_bytes()
    family = spec.get('family_id')
    if original_input != json_bytes(_synthetic_input(family)): raise ProbeError('synthetic_input_required')
    pins = spec.get('runtime_pins', {})
    if (not _digest(approved_source_worker_sha) or pins.get('worker_sha256') != approved_source_worker_sha
            or pins.get('native_session_sha256') != NATIVE_SESSION_SHA256
            or pins.get('cli_sha256') != ns.CLI_SHA256 or pins.get('cli_version') != ns.CLI_VERSION
            or not _digest(pins.get('python_sha256')) or not re.fullmatch('sha256:[0-9a-f]{64}', pins.get('image_sha256',''))
            or (spec.get('model'), spec.get('effort'), spec.get('route')) != (ns.CLI_MODEL, 'max', 'subscription')):
        raise ProbeError('production_runtime_pin_mismatch')
    if family == 'assistant':
        if not _digest(approved_bridge_sha): raise ProbeError('approved_bridge_pin_required')
        if pins.get('bridge_sha256') != approved_bridge_sha: raise ProbeError('approved_bridge_pin_mismatch')
    elif pins.get('bridge_sha256') is not None: raise ProbeError('unexpected_bridge_pin')
    sid = spec.get('session_id'); identity = read_json(source / 'identity.json')
    if (identity != spec.get('identity') or identity.get('session_id') != sid
            or spec.get('input_sha256') != sha(original_input)
            or _regular(state / 'capture/prepared-input.json').read_bytes() != original_input
            or _regular(state / 'capture/delivered-input.json').read_bytes() != original_input
            or read_json(state / 'capture/prepared-spec.json') != spec):
        raise ProbeError('source_identity_or_input_mismatch')
    report = read_json(source / 'worker-report.json'); final = read_json(source / 'final-report.json')
    qualified = read_json(source / 'qualification-result.json'); ack = read_json(source / 'final-archive-ack.json')
    stop = read_json(source / 'external-stop.json'); execution = report.get('execution', {})
    if (report.get('schema_version') != 'agenttime.natural-worker-report.v1' or report.get('state') != 'captured'
            or any(report.get(k) is not True for k in ('archive_verified','native_sessions_verified','timing_admissible','included_subscription_allowance_verified'))
            or report.get('issues') != [] or report.get('session_id') != sid or report.get('attempt_id') != spec['attempt_id']
            or report.get('identity_sha256') != worker.document_sha256(identity)
            or report.get('spec_sha256') != sha(json_bytes(spec)) or report.get('input_sha256') != sha(original_input)
            or report.get('requested_route') != 'subscription' or report.get('study_admitted') is not False
            or execution.get('session_id') != sid or execution.get('root_exit_code') != 0
            or execution.get('timing_valid') is not True or execution.get('issues') != [] or execution.get('cancelled') is not False):
        raise ProbeError('production_report_unverified')
    times = [execution.get(k) for k in ('prompt_released_monotonic_ns','result_monotonic_ns','root_exit_monotonic_ns','owned_work_drained_monotonic_ns')]
    if any(type(t) is not int for t in times) or times != sorted(times) or execution.get('native_terminal_monotonic_ns') != times[-1]:
        raise ProbeError('source_drain_unverified')
    if (stop.get('identity') != identity or stop.get('stop_verified') is not True
            or stop.get('container_running') is not False or stop.get('external_verification') is not True
            or final.get('stop_evidence') != {'identity_sha256':worker.document_sha256(identity), 'stop_verified':True,
                'owned_work_drained':True, 'proof_sha256':worker.document_sha256(stop)}):
        raise ProbeError('external_stop_unverified')
    if (final.get('purpose') != 'synthetic_qualification' or final.get('study_admitted') is not False
            or final.get('outcome') != 'completed' or final.get('timing_status') != 'valid'
            or final.get('attempt_id') != spec['attempt_id'] or final.get('identity_sha256') != worker.document_sha256(identity)
            or final.get('input_sha256') != sha(original_input) or final.get('contract_sha256') != spec['preparation_contract_sha256']
            or qualified.get('study_attempts') != 0 or qualified.get('attempt_id') != spec['attempt_id']
            or qualified.get('task_id') != spec['task_id'] or qualified.get('issues') != []
            or any(qualified.get(k) is not True for k in ('complete','qualified','independent_archive_verified','stop_verified'))):
        raise ProbeError('synthetic_qualification_unverified')
    try:
        inventory = ns.inventory(state)
        native = worker.validate_native_stores(state, spec, original_input, runtime_cwd=RUNTIME_CWD)
    except ns.NativeSessionError as exc: raise ProbeError(str(exc)) from None
    receipt = report.get('archive', {})
    if (receipt.get('schema_version') != 'agenttime.native-local-archive.v1' or receipt.get('verified') is not True
            or receipt.get('inventory') != inventory or receipt.get('inventory_sha256') != sha(json_bytes(inventory))
            or ack.get('verified') is not True or ack.get('manifest_sha256') != sha(json_bytes(receipt))):
        raise ProbeError('independent_archive_unverified')
    native_hash = worker.document_sha256(native)
    if (native != report.get('native_session_evidence') or ack.get('native_session_evidence_sha256') != native_hash
            or final.get('native_session_evidence_sha256') != native_hash): raise ProbeError('native_archive_evidence_mismatch')
    tools = spec['tool_policy']['native_tools'] + spec['tool_policy']['mcp_tools']
    raw = state / 'capture/raw-bodies'; first_record = read_rows(raw / 'index.jsonl')[0]
    name = first_record.get('request_file')
    if not isinstance(name,str) or Path(name).name != name: raise ProbeError('unsafe_transport_reference')
    first_body = read_json(raw / name); messages = first_body.get('messages',[])
    if not messages or not ns.initial_message_matches(messages[0],_loads(original_input)['message'],Path(RUNTIME_ROOT),sid):
        raise ProbeError('source_transport_input_mismatch')
    # The archive lives elsewhere; validate the original image companion using
    # its pinned runtime path before the generic checker sees this exact message.
    transport = ns.inspect_transport_records(raw, {'session_id':sid,
        'tools':tools, 'case':'web_subagent' if family == 'browsecomp' else 'natural',
        'input':{'message':messages[0]}})
    if transport.get('issues') or report.get('transport', {}).get('issues') != []: raise ProbeError('source_transport_unverified')
    browser = _browser_source(state, spec, inventory, approved_bridge_sha)
    if tree_digest(source) != before: raise ProbeError('source_tree_changed')
    return {'schema_version':SCHEMA, 'source_location':str(source), 'source_tree_sha256':before,
        'family_id':family, 'session_id':sid, 'attempt_id':spec['attempt_id'], 'spec':spec,
        'archive_inventory':inventory, 'archive_inventory_sha256':sha(json_bytes(inventory)),
        'native_session_evidence':native, 'native_sessions_verified':True, 'child_count':len(native['children']),
        'browser_state_available':browser is not None, 'browser_snapshot':browser,
        'approved_bridge_sha256':approved_bridge_sha if family == 'assistant' else None,
        'approved_runtime_worker_sha256':approved_runtime_worker_sha, 'source_worker_sha256':approved_source_worker_sha, 'study_ready':False}


def public_metadata(value):
    private = {'spec','archive_inventory','restored_inventory','native_session_evidence','source_spec','target_native_environment'}
    return {k:v for k,v in value.items() if k not in private}


def probe_input(family):
    if family not in ('gpqa','hle','browsecomp','assistant'): raise ProbeError('synthetic_family_required')
    text = ('Observe the restored browser without navigating or changing it, then submit exactly the final answer '
        'you submitted in the previous interaction using send_msg_to_user. Finish after submission.' if family == 'assistant'
        else 'Return exactly your previous final answer, without using any tools.')
    return {'type':'user', 'message':{'role':'user', 'content':[{'type':'text','text':text}]}}


def _session(operation, original, supplied):
    if operation == 'continue':
        if supplied not in (None,original): raise ProbeError('continuation_must_keep_session')
        return original
    try: valid = operation == 'fork' and str(uuid.UUID(supplied)) == supplied and supplied != original
    except (ValueError, TypeError, AttributeError): valid = False
    if not valid: raise ProbeError('fresh_fork_session_required')
    return supplied


def _target_environment():
    return ('# Environment\nYou have been invoked in the following environment: \n'
        ' - Primary working directory: ' + RUNTIME_CWD + '\n - Is a git repository: false\n'
        ' - Platform: linux\n - Shell: bash\n - OS Version: ' + os.uname().sysname + ' ' + os.uname().release)


def _copy_state(source, destination, inventory):
    directories = []
    for relative, entry in sorted(inventory.items(), key=lambda pair:(len(Path(pair[0]).parts),pair[0])):
        src = source / relative; dst = destination / relative
        if entry['type'] == 'directory':
            dst.mkdir(mode=0o700); dst.chmod(0o700); directories.append((dst,entry['mode']))
        elif entry['type'] == 'file':
            shutil.copy2(_regular(src),dst)
            with dst.open('rb') as stream: os.fsync(stream.fileno())
        elif entry['type'] == 'link': dst.symlink_to(os.path.relpath(destination / entry['target'],dst.parent))
        else: raise ProbeError('unsupported_source_object')
    for directory, mode in reversed(directories): directory.chmod(mode)


def _manifest(info, operation, sid, qualification_id):
    return {'schema_version':SCHEMA, 'operation':operation, 'qualification_id':qualification_id,
        'source_location':info['source_location'], 'source_tree_sha256':info['source_tree_sha256'],
        'source_spec':info['spec'], 'helper_sha256':ns._file_sha(__file__), 'original_session_id':info['session_id'],
        'expected_session_id':sid, 'runtime_root':RUNTIME_ROOT, 'runtime_cwd':RUNTIME_CWD,
        'target_native_environment':_target_environment(), 'restored_inventory':info['archive_inventory'],
        'archive_inventory_sha256':info['archive_inventory_sha256'], 'native_session_evidence':info['native_session_evidence'],
        'browser_snapshot':info['browser_snapshot'], 'approved_bridge_sha256':info['approved_bridge_sha256'],
        'approved_runtime_worker_sha256':info['approved_runtime_worker_sha256'], 'source_worker_sha256':info['source_worker_sha256'],
        'probe_input_sha256':sha(json_bytes(probe_input(info['family_id']))), 'study_ready':False}


def prepare(source, destination, expected_tree_sha, operation, *, session_id=None, approved_bridge_sha=None,
            approved_runtime_worker_sha=WORKER_SHA256, approved_source_worker_sha=WORKER_SHA256):
    source = Path(source).absolute(); destination = Path(destination).absolute()
    if destination.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(destination.resolve()):
        raise ProbeError('source_destination_overlap')
    if destination.exists() or destination.is_symlink(): raise ProbeError('destination_not_fresh')
    if not _digest(expected_tree_sha): raise ProbeError('frozen_source_hash_required')
    info = verify_source(source, expected_tree_sha, approved_bridge_sha=approved_bridge_sha,
        approved_runtime_worker_sha=approved_runtime_worker_sha, approved_source_worker_sha=approved_source_worker_sha)
    sid = _session(operation,info['session_id'],session_id)
    if info['family_id'] == 'assistant' and not info['browser_state_available']: raise ProbeError('browser_snapshot_unavailable')
    destination.mkdir(mode=0o700)
    _copy_state(source / 'final-state',destination,info['archive_inventory'])
    if ns.inventory(destination) != info['archive_inventory']: raise ProbeError('restored_state_mismatch')
    if tree_digest(source) != expected_tree_sha: raise ProbeError('source_tree_changed')
    manifest = _manifest(info,operation,sid,str(uuid.uuid4()))
    ns._write_new(destination / 'restore-manifest.json',json_bytes(manifest))
    return manifest


def configuration(root,binary,manifest,token):
    root = Path(root).absolute(); spec = copy.deepcopy(manifest['source_spec'])
    spec['session_id'] = manifest['expected_session_id']; spec['identity']['session_id'] = spec['session_id']
    spec['runtime_pins']['worker_sha256'] = manifest['approved_runtime_worker_sha256']
    conf = worker.configuration(root,binary,spec,token); command = conf['command']
    at = command.index('--session-id'); del command[at:at+2]
    command += ['--resume',manifest['original_session_id']]
    if manifest['operation'] == 'fork': command += ['--fork-session','--session-id',manifest['expected_session_id']]
    if spec['family_id'] == 'assistant':
        env = worker._browser_environment(root)
        mcp = {'mcpServers':{'assistantbench':{'command':'/usr/bin/env','args':['-i',
            *[f'{key}={value}' for key,value in env.items()],str(Path(sys.executable).absolute()),
            str(Path(__file__).resolve()),'bridge','--root',str(root)]}}}
        command[command.index('--mcp-config')+1] = json.dumps(mcp)
    return conf


def compare_browser_state(expected,actual):
    # Ordering of browser cookies/origins is not semantic; every value remains
    # exact. This deliberately does not forgive expired or server-mutated state.
    def canonical_storage(value):
        value = copy.deepcopy(value)
        value['cookies'] = sorted(value['cookies'],key=lambda x:json.dumps(x,sort_keys=True))
        for origin in value['origins']:
            origin['localStorage'] = sorted(origin['localStorage'],key=lambda x:json.dumps(x,sort_keys=True))
        value['origins'] = sorted(value['origins'],key=lambda x:json.dumps(x,sort_keys=True))
        return value
    if (any(expected[k] != actual.get(k) for k in ('pages','active_page_index','profile','goal_sha256','browsergym_version'))
            or canonical_storage(expected['storage_state']) != canonical_storage(actual.get('storage_state',{}))):
        raise ProbeError('browser_restoration_mismatch')
    return {'persisted_state_matches':True, 'restored':['cookies','localStorage','open_page_urls','active_page_index'],
            'full_browser_process_restored':False, 'not_restored':BROWSER_LIMITS}


def bridge_entry(root, *, model_free=False):
    root = Path(root).absolute(); manifest = read_json(root / 'restore-manifest.json')
    if str(root) != RUNTIME_ROOT or root.resolve() != root or sys.platform != 'linux': raise ProbeError('runtime_path_mismatch')
    if manifest['helper_sha256'] != ns._file_sha(__file__): raise ProbeError('helper_pin_mismatch')
    if not model_free and not (root / 'restore-started.json').is_file(): raise ProbeError('restore_claim_required')
    pin = manifest['approved_bridge_sha256']; bridge_module = _bridge(pin)
    snapshot_info = manifest.get('browser_snapshot')
    if snapshot_info is None: raise ProbeError('browser_snapshot_unavailable')
    old_capture = 'capture' if model_free else 'original-capture'
    snapshot = root / old_capture / 'browser-state/browser-state.json'
    expected = bridge_module.load_browser_snapshot(snapshot,snapshot_info['sha256'])
    env = worker._browser_environment(root)
    os.environ.clear(); os.environ.update(env)
    backend = bridge_module.NativeBrowserBackend(ASSISTANT_INPUT,restore_snapshot=snapshot,restore_sha256=snapshot_info['sha256'])
    if model_free:
        try:
            backend.reset(lambda text: (_ for _ in ()).throw(ProbeError('unexpected_browser_submission')))
            evidence = compare_browser_state(expected,backend.snapshot())
            ns._write_new(root / 'browser-restore-probe.json',json_bytes(evidence)); return evidence
        finally: backend.close()
    bridge = bridge_module.AssistantBenchBridge(backend,root / 'capture/browser-state',manifest['qualification_id'])
    try:
        evidence = compare_browser_state(expected,backend.snapshot())
        ns._write_new(root / 'capture/browser-restored.json',json_bytes(evidence))
        bridge_module.serve_stdio(bridge)
    finally: bridge.close()


def _normalized(value):
    """Normalize only native cache tags and string/single-text representation."""
    if isinstance(value,list): return [_normalized(v) for v in value]
    if not isinstance(value,dict): return value
    result = {}
    for key,item in value.items():
        if key == 'cache_control':
            if item != {'type':'ephemeral','ttl':'1h'}: raise ProbeError('unrecognized_native_cache_policy')
        else: result[key] = _normalized(item)
    if result.get('role') in ('user','assistant','system') and isinstance(result.get('content'),str):
        result['content'] = [{'type':'text','text':result['content']}]
    return result


def _original_history(root,manifest):
    raw = Path(root) / 'original-capture/raw-bodies'
    rows = [r for r in read_rows(raw / 'index.jsonl') if r.get('query_source','sdk') == 'sdk']
    if not rows: raise ProbeError('original_request_unavailable')
    record = rows[-1]; name = record.get('request_file')
    if not isinstance(name,str) or Path(name).name != name: raise ProbeError('unsafe_transport_reference')
    body = read_json(raw / name); evidence = manifest['native_session_evidence']
    original = Path(manifest['source_location']) / 'final-state' / evidence['parent']['path']
    messages = [r for r in read_rows(original) if r.get('type') in ('user','assistant')]
    final_id = messages[-1]['message']['id']
    blocks = [b for row in messages if row['type'] == 'assistant' and row['message']['id'] == final_id for b in row['message']['content']]
    return body['messages'] + [{'role':'assistant','content':blocks}]


def _request_records(root,manifest,stream):
    raw = Path(root) / 'capture/raw-bodies'; index = _regular(raw / 'index.jsonl').read_bytes()
    sid = manifest['expected_session_id']; records = [_loads(line) for line in index.splitlines() if line.strip()]
    if records: return records,True
    # The reviewed CLI may exit before its optional index append. This fallback
    # proves one request/response identity, never a retry count or study admission.
    requests = list(raw.glob('*.request.json')); responses = list(raw.glob('*.response.json'))
    if len(requests) != 1 or len(responses) != 1: raise ProbeError('probe_transport_unverified')
    body = read_json(requests[0]); metadata = _loads(body.get('metadata',{}).get('user_id','{}'))
    assistant = [r for r in stream if r.get('type') == 'assistant' and r.get('parent_tool_use_id') is None]
    identities = {(r.get('request_id'),r.get('message',{}).get('id')) for r in assistant}
    if len(identities) != 1 or metadata.get('session_id') != sid: raise ProbeError('probe_transport_identity_mismatch')
    request_id,message_id = next(iter(identities))
    if not isinstance(request_id,str) or not request_id.startswith('req_') or not isinstance(message_id,str) or not message_id:
        raise ProbeError('probe_transport_identity_mismatch')
    # Native response filenames bind this optional empty file to its request.
    if responses[0].name != request_id + '.response.json': raise ProbeError('probe_transport_identity_mismatch')
    return [{'request_file':requests[0].name,'response_file':responses[0].name,'request_id':request_id,
             'message_id':message_id,'session_id':sid,'model':ns.MODEL,'query_source':'sdk'}],False


def inspect_probe(root,manifest):
    root = Path(root); sid = manifest['expected_session_id']; family = manifest['source_spec']['family_id']
    stream = read_rows(root / 'capture/stream.jsonl')
    inits = [r for r in stream if r.get('type') == 'system' and r.get('subtype') == 'init']
    results = [r for r in stream if r.get('type') == 'result']
    if (len(inits) != 1 or len(results) != 1 or inits[0].get('session_id') != sid or results[0].get('session_id') != sid
            or results[0].get('subtype') != 'success' or results[0].get('is_error') is not False): raise ProbeError('native_completion_unverified')
    init = inits[0]; policy = manifest['source_spec']['tool_policy']
    expected_tools = policy['native_tools'] + policy['mcp_tools']
    if (init.get('claude_code_version') != ns.CLI_VERSION or init.get('model') not in (ns.MODEL,ns.CLI_MODEL)
            or not isinstance(init.get('tools'),list)
            or sorted('Agent' if t == 'Task' else t for t in init['tools']) != sorted(expected_tools)
            or any(init.get(k) != [] for k in ('skills','plugins'))): raise ProbeError('native_probe_capability_mismatch')
    expected_mcp = [{'name':'assistantbench','status':'connected'}] if family == 'assistant' else []
    annotated_mcp = [{'name':'assistantbench','status':'connected','source':'dynamic'}] if family == 'assistant' else []
    if init.get('mcp_servers') not in (expected_mcp,annotated_mcp): raise ProbeError('native_probe_capability_mismatch')
    records,index_available = _request_records(root,manifest,stream); raw = root / 'capture/raw-bodies'
    files = {p.name for p in raw.glob('*.request.json')}
    if (len(files) != len(records) or files != {r.get('request_file') for r in records}
            or {p.name for p in raw.glob('*.response.json')} != {r.get('response_file') for r in records}):
        raise ProbeError('probe_transport_coverage_incomplete')
    allowed = ['Agent' if t == 'Task' else t for t in inits[0].get('tools',[])]; first = None; response_evidence = []
    for record in records:
        name = record.get('request_file'); response_name = record.get('response_file')
        if any(not isinstance(v,str) or Path(v).name != v for v in (name,response_name)): raise ProbeError('unsafe_transport_reference')
        body = read_json(raw / name); metadata = _loads(body.get('metadata',{}).get('user_id','{}'))
        if (body.get('model') != ns.MODEL or record.get('model') != ns.MODEL or body.get('output_config') != {'effort':'max'}
                or body.get('fallbacks') not in (None,[]) or metadata.get('session_id') != sid or record.get('session_id') != sid
                or record.get('query_source','sdk') != 'sdk' or not isinstance(record.get('request_id'),str)
                or not record['request_id'].startswith('req_') or not isinstance(body.get('tools'),list)
                or sorted(t.get('name','') for t in body['tools']) != sorted(allowed)):
            raise ProbeError('probe_transport_identity_mismatch')
        matches = [r for r in stream if r.get('type') == 'assistant' and r.get('session_id') == sid
            and r.get('request_id') == record['request_id'] and r.get('message',{}).get('id') == record.get('message_id')
            and r.get('message',{}).get('model') == ns.MODEL]
        if not matches: raise ProbeError('probe_transport_identity_mismatch')
        data = _regular(raw / response_name).read_bytes()
        if data:
            response = _loads(data)
            if response.get('type') != 'message' or response.get('id') != record.get('message_id') or response.get('model') != ns.MODEL:
                raise ProbeError('probe_transport_response_mismatch')
        else: response_evidence.append(record['message_id'])
        if first is None: first = body
    expected = _original_history(root,manifest) + [probe_input(family)['message']]
    actual = first.get('messages')
    if isinstance(actual,list) and len(actual) == len(expected)+1:
        update = {'role':'system','content':[{'type':'text','text':manifest['target_native_environment'],
                  'cache_control':{'type':'ephemeral','ttl':'1h'}}]}
        if actual[-1] != update: raise ProbeError('inherited_context_unverified')
        actual = actual[:-1]
    if _normalized(actual) != _normalized(expected): raise ProbeError('inherited_context_unverified')
    evidence = manifest['native_session_evidence']; parent = root / evidence['parent']['path']
    original_bytes = _regular(Path(manifest['source_location']) / 'final-state' / evidence['parent']['path']).read_bytes()
    current_bytes = _regular(parent).read_bytes()
    if (manifest['operation'] == 'fork' and current_bytes != original_bytes) or not current_bytes.startswith(original_bytes):
        raise ProbeError('original_parent_store_changed')
    for child in evidence['children'].values():
        if ns._file_sha(_regular(root / child['path'])) != child['sha256']: raise ProbeError('original_child_store_changed')
    target = parent.with_name(sid + '.jsonl'); rows = read_rows(target)
    native_messages = [r for r in rows if r.get('type') in ('user','assistant')]
    original_messages = [r for r in read_rows(Path(manifest['source_location']) / 'final-state' / evidence['parent']['path'])
                         if r.get('type') in ('user','assistant')]
    if len(native_messages) <= len(original_messages): raise ProbeError('native_probe_history_mismatch')
    for original,current in zip(original_messages,native_messages):
        if any(original.get(k) != current.get(k) for k in ('uuid','type','message')): raise ProbeError('native_probe_history_mismatch')
    added = native_messages[len(original_messages):]
    if added[0].get('message') != probe_input(family)['message']: raise ProbeError('native_probe_history_mismatch')
    for row in added:
        if row.get('sessionId') != sid or row.get('cwd') != RUNTIME_CWD or row.get('version') != ns.CLI_VERSION:
            raise ProbeError('native_probe_identity_mismatch')
    by_uuid = {r.get('uuid'):r for r in added}
    for row in stream:
        if row.get('type') not in ('user','assistant'): continue
        native = by_uuid.get(row.get('uuid'))
        if native is None or worker._native_message_content(native['message']) != worker._native_message_content(row.get('message',{})):
            raise ProbeError('native_probe_stream_mismatch')
        if row['type'] == 'assistant' and native.get('requestId') != row.get('request_id'): raise ProbeError('native_probe_stream_mismatch')
    if ({r.get('uuid') for r in added if r.get('type') == 'assistant'}
            != {r.get('uuid') for r in stream if r.get('type') == 'assistant'}): raise ProbeError('native_probe_stream_mismatch')
    if added[-1].get('type') != 'assistant': raise ProbeError('native_probe_final_message_missing')
    final_id = added[-1]['message'].get('id')
    final_text = ''.join(block.get('text','') for row in added if row['type'] == 'assistant'
        and row['message'].get('id') == final_id for block in row['message']['content'] if block.get('type') == 'text')
    if final_text != results[0].get('result'): raise ProbeError('native_probe_final_answer_mismatch')
    expected_paths = {evidence['parent']['path'],target.relative_to(root).as_posix(),*[v['path'] for v in evidence['children'].values()]}
    if {p.relative_to(root).as_posix() for p in (root / 'config/projects').rglob('*.jsonl')} != expected_paths:
        raise ProbeError('unexpected_native_probe_session')
    observed_tools = [name for row in stream for name in ns.event_metadata(row)['tool_names']]
    if family == 'assistant':
        required = {'mcp__assistantbench__observe','mcp__assistantbench__send_msg_to_user'}
        if set(observed_tools) != required: raise ProbeError('unexpected_browser_probe_action')
        browser = read_json(root / 'capture/browser-restored.json')
        if browser.get('persisted_state_matches') is not True or browser.get('full_browser_process_restored') is not False:
            raise ProbeError('browser_restoration_unverified')
    elif observed_tools: raise ProbeError('unexpected_probe_tool')
    old_answer = _regular(root / 'original-capture/sealed-answer.txt').read_bytes()
    if _regular(root / 'capture/sealed-answer.txt').read_bytes() != old_answer: raise ProbeError('previous_answer_not_recalled')
    return {'inherited_context_verified':True, 'native_store_verified':True, 'original_parent_preserved':True,
        'restored_child_count':len(evidence['children']), 'original_children_preserved':True,
        'child_continuation_independently_tested':False, 'native_transcript_sha256':ns._file_sha(target),
        'browser_persisted_state_verified':family == 'assistant', 'full_browser_process_restored':False,
        'transport':{'index_available':index_available,'retry_count_verified':False,'request_count':len(records),
            'native_stream_response_ids':response_evidence,'identity_verified':True}}


def _boundary(capture,manifest,input_bytes):
    # Use the reviewed lifecycle implementation, without reclassifying the
    # follow-up text as a new HLE/image study task.
    boundary = worker.NaturalBoundary.__new__(worker.NaturalBoundary)
    contract = ns.qualification_contract('synthetic-production-restore',manifest['expected_session_id'])
    ns.NativeBoundary.__init__(boundary,capture,contract)
    boundary.spec = copy.deepcopy(manifest['source_spec']); boundary.spec['session_id'] = manifest['expected_session_id']
    boundary.spec['attempt_id'] = manifest['qualification_id']; boundary.spec['input_sha256'] = sha(input_bytes)
    boundary.contract = {'session_id':manifest['expected_session_id'],'input':_loads(input_bytes),
        'tools':boundary.spec['tool_policy']['native_tools']+boundary.spec['tool_policy']['mcp_tools'],
        'case':'web_subagent' if boundary.spec['family_id'] == 'browsecomp' else 'natural'}
    boundary.submission_ns = None; boundary.clock_id = 'linux-boot:' + Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return boundary


def run(root,binary,token):
    root = Path(root).absolute()
    if str(root) != RUNTIME_ROOT or root.resolve() != root or sys.platform != 'linux': raise ProbeError('runtime_path_mismatch')
    if (root / 'restore-started.json').exists() or (root / 'restore-started.json').is_symlink(): raise ProbeError('restore_already_claimed')
    manifest = read_json(root / 'restore-manifest.json')
    info = verify_source(manifest['source_location'],manifest['source_tree_sha256'],approved_bridge_sha=manifest['approved_bridge_sha256'],
        approved_runtime_worker_sha=manifest['approved_runtime_worker_sha256'],approved_source_worker_sha=manifest['source_worker_sha256'])
    sid = _session(manifest['operation'],info['session_id'],manifest['expected_session_id'])
    if manifest != _manifest(info,manifest['operation'],sid,manifest['qualification_id']): raise ProbeError('prepared_manifest_changed')
    runtime_spec = copy.deepcopy(manifest['source_spec'])
    runtime_spec['runtime_pins']['worker_sha256'] = manifest['approved_runtime_worker_sha256']
    worker.verify_runtime(binary,runtime_spec)
    if ns.inventory(root) != manifest['restored_inventory']: raise ProbeError('restored_state_changed')
    # Freeze validated bytes before claiming; never reopen them for delivery.
    input_bytes = json_bytes(probe_input(info['family_id']))
    if sha(input_bytes) != manifest['probe_input_sha256']: raise ProbeError('probe_input_changed')
    conf = configuration(root,binary,manifest,token)
    try: ns._write_new(root / 'restore-started.json',json_bytes({'manifest_sha256':sha(json_bytes(manifest)),
        'input_sha256':sha(input_bytes),'session_id':sid,'automatic_retry':False}))
    except FileExistsError: raise ProbeError('restore_already_claimed') from None
    (root / 'capture').rename(root / 'original-capture'); (root / 'capture').mkdir(mode=0o700)
    ns._write_new(root / 'capture/delivered-input.json',input_bytes)
    boundary = _boundary(root / 'capture',manifest,input_bytes)
    report = {'schema_version':SCHEMA,'operation':manifest['operation'],'session_id':sid,
        'helper_sha256':manifest['helper_sha256'],'source_tree_sha256':info['source_tree_sha256'],
        'source_worker_sha256':manifest['source_worker_sha256'],'restoration_runtime_worker_sha256':manifest['approved_runtime_worker_sha256'],
        'configured_authentication':'stdin_to_child_environment_only','automatic_retry':False,
        'study_ready':False,'qualified':False,'native_restore_verified':False,'native_fork_verified':False,
        'archive_verified':False,'independent_archive_acknowledged':False,'source_unchanged':False,
        'inherited_context_verified':False,'included_subscription_allowance_verified':False}
    try:
        report['execution'] = ns.supervise(conf['command'],conf['environment'],root / 'work',input_bytes,boundary)
        report.update(inspect_probe(root,manifest))
        report['included_subscription_allowance_verified'] = ns.subscription_allowance_verified(boundary.usage)
    except (ProbeError,ns.NativeSessionError,OSError,ValueError) as exc:
        report['error_code'] = str(exc) if isinstance(exc,(ProbeError,ns.NativeSessionError)) else type(exc).__name__
        report['execution'] = boundary.metadata()
    try: report['source_unchanged'] = tree_digest(info['source_location']) == info['source_tree_sha256']
    except (ProbeError,OSError): report['source_unchanged'] = False
    if not report['source_unchanged']: report['error_code'] = 'source_tree_changed'
    if boundary.drain_ns is not None:
        try:
            receipt = ns.capture_archive(root,token)
            report['archive_verified'] = receipt['verified']; report['archive_inventory_sha256'] = receipt['inventory_sha256']
        except (ns.NativeSessionError,OSError): report['archive_error'] = 'preserve_original_state'
    else: report['archive_error'] = 'owned_work_unresolved_preserve_original_state'
    passed = bool(report.get('execution',{}).get('timing_valid') and report['inherited_context_verified']
        and report['included_subscription_allowance_verified'] and report['source_unchanged']
        and report['archive_verified'] and not report.get('error_code'))
    report['qualified'] = passed; report['native_restore_verified'] = passed and manifest['operation'] == 'continue'
    report['native_fork_verified'] = passed and manifest['operation'] == 'fork'
    ns._write_new(root / 'restore-report.json',json_bytes(report)); return report


def main():
    parser = argparse.ArgumentParser(description=__doc__); commands = parser.add_subparsers(dest='command',required=True)
    inspect = commands.add_parser('inspect'); inspect.add_argument('--source',type=Path,required=True); inspect.add_argument('--bridge-sha')
    prep = commands.add_parser('prepare'); prep.add_argument('--source',type=Path,required=True); prep.add_argument('--source-sha',required=True)
    prep.add_argument('--root',type=Path,required=True); prep.add_argument('--operation',choices=['continue','fork'],required=True)
    prep.add_argument('--session-id'); prep.add_argument('--bridge-sha')
    for command in (inspect,prep):
        command.add_argument('--runtime-worker-sha',default=WORKER_SHA256)
        command.add_argument('--source-worker-sha',default=WORKER_SHA256)
    execute = commands.add_parser('run'); execute.add_argument('--root',type=Path,required=True); execute.add_argument('--binary',type=Path,required=True)
    for name in ('bridge','browser-check'):
        command = commands.add_parser(name); command.add_argument('--root',type=Path,required=True)
    args = parser.parse_args(); os.umask(0o077)
    try:
        if args.command == 'inspect': result = verify_source(args.source,approved_bridge_sha=args.bridge_sha,
            approved_runtime_worker_sha=args.runtime_worker_sha,approved_source_worker_sha=args.source_worker_sha)
        elif args.command == 'prepare': result = prepare(args.source,args.root,args.source_sha,args.operation,session_id=args.session_id,
            approved_bridge_sha=args.bridge_sha,approved_runtime_worker_sha=args.runtime_worker_sha,approved_source_worker_sha=args.source_worker_sha)
        elif args.command in ('bridge','browser-check'):
            result = bridge_entry(args.root,model_free=args.command == 'browser-check')
            if args.command == 'bridge': return 0
        else:
            token = sys.stdin.readline(32770).rstrip('\r\n')
            if sys.stdin.read(1): raise ProbeError('invalid_credential_channel')
            result = run(args.root,args.binary,token)
        print(json.dumps(public_metadata(result),sort_keys=True)); return 0 if result.get('qualified',True) else 1
    except (ProbeError,ns.NativeSessionError,OSError,KeyError,TypeError,ValueError) as exc:
        code = str(exc) if isinstance(exc,(ProbeError,ns.NativeSessionError)) else type(exc).__name__
        print(json.dumps({'qualified':False,'study_ready':False,'error_code':code})); return 1


if __name__ == '__main__': raise SystemExit(main())
