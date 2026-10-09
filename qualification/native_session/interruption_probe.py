"""Fixed synthetic native interrupted-turn probe. Never admits a study task.

The caller owns all model access and independent archive/stop acknowledgements.
``prepare`` and the tests are offline. ``run`` consumes OAuth only from stdin,
physically stops the first CLI after its tool result is durable, waits for an
independent stop/archive ACK, and resumes the original ID with an empty input
stream. Native recovery adds its own isMeta marker; this is recorded explicitly.
One Linux subreaper and host boot span both segments and the entire interruption.
Cross-host/boot recovery, arbitrary tasks and automatic replay remain unsupported.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

from agenttime import native_session as ns

SCHEMA = 'agenttime.synthetic-native-interruption.v1'
NATIVE_SOURCE_SHA256 = 'a4321ba27a5a6372362d280096fee04a5a8c98a891c219568165de5b51274382'
TOOL_NAME = 'mcp__interruption__record_once'
TOOL_RESULT = 'SYNTHETIC_ONCE_RECORDED'
EFFECT_TEXT = 'one durable synthetic effect\n'
FINAL_MARKER = 'SYNTHETIC_INTERRUPTION_OK'
NATIVE_RESUME_MARKER = 'Continue from where you left off.'
PROMPT = ('This is a synthetic infrastructure check. Call the record_once tool exactly once. '
          'After it succeeds, do not call any tool again; return exactly SYNTHETIC_INTERRUPTION_OK.')
BOUNDARIES = ('original_released', 'checkpoint_fsynced', 'original_sigkill', 'original_drained',
              'checkpoint_archived', 'independent_recovery_ack', 'resume_released', 'resume_result', 'resume_drained')
SELF = Path(__file__).resolve()
LOADED_SHA256 = hashlib.sha256(SELF.read_bytes()).hexdigest()


class ProbeError(ValueError):
    """Stable codes only, never native content or credentials."""


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def file_sha(path):
    return ns._file_sha(path)


def write_new(path, data):
    try:
        ns._write_new(path, data if isinstance(data, bytes) else json_bytes(data))
    except FileExistsError:
        raise ProbeError('immutable_file_already_exists') from None
    Path(path).chmod(0o600)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError('duplicate_json_key')
        result[key] = value
    return result


def loads(data):
    def bad_constant(value):
        raise ProbeError('nonfinite_json')
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=bad_constant)
    except (ValueError, TypeError, UnicodeError):
        raise ProbeError('invalid_json') from None


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ProbeError('unsafe_metadata_path')
    return loads(path.read_bytes())


def read_rows(path):
    data = Path(path).read_bytes()
    if data and not data.endswith(b'\n'):
        raise ProbeError('partial_native_record')
    rows = [loads(line) for line in data.splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ProbeError('invalid_native_rows')
    return rows


def pins():
    if file_sha(SELF) != LOADED_SHA256 or ns._source_pin() != NATIVE_SOURCE_SHA256:
        raise ProbeError('source_pin_mismatch')
    return {'interruption_probe.py': LOADED_SHA256, 'native_session.py': NATIVE_SOURCE_SHA256}


def input_value():
    return {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': PROMPT}]}}


def make_contract(qualification_id, session_id):
    if not isinstance(qualification_id, str) or not re.fullmatch(r'synthetic-[a-z0-9-]{3,100}', qualification_id):
        raise ProbeError('synthetic_identity_required')
    try:
        if str(uuid.UUID(session_id)) != session_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise ProbeError('invalid_session_identity') from None
    return {'schema_version': SCHEMA, 'qualification_id': qualification_id, 'session_id': session_id,
            'purpose': 'synthetic_qualification_only', 'study_inputs': 0, 'source_pins': pins(),
            'cli_sha256': ns.CLI_SHA256, 'cli_version': ns.CLI_VERSION, 'model': ns.CLI_MODEL,
            'effort': 'max', 'tools': [TOOL_NAME], 'route': 'subscription', 'input': input_value()}


def prepare(root, qualification_id, session_id):
    root = Path(root).absolute()
    contract = make_contract(qualification_id, session_id)
    if root.exists() or root.is_symlink():
        raise ProbeError('state_already_exists')
    root.mkdir(mode=0o700)
    for name in ns.STATE_ROOTS:
        (root / name).mkdir(mode=0o700)
    for name in ('original', 'resumed'):
        (root / 'capture' / name).mkdir(mode=0o700)
    write_new(root / 'contract.json', contract)
    write_new(root / 'input.json', json_bytes(contract['input']))
    inventory = ns.inventory(root)
    baseline = {'schema_version': SCHEMA + '.baseline', 'session_id': session_id,
                'contract_sha256': sha(json_bytes(contract)), 'input_sha256': sha(json_bytes(contract['input'])),
                'inventory': inventory, 'inventory_sha256': sha(json_bytes(inventory))}
    write_new(root / 'baseline.json', baseline)
    return {'prepared': True, 'model_calls': 0, 'study_ready': False, 'session_id': session_id,
            'baseline_sha256': file_sha(root / 'baseline.json')}


def verify_prepared(root):
    root = Path(root)
    contract = read_json(root / 'contract.json')
    expected = make_contract(contract.get('qualification_id'), contract.get('session_id'))
    if contract != expected:
        raise ProbeError('fixed_synthetic_contract_required')
    # Read once and carry these exact bytes through the release boundary.
    payload = (root / 'input.json').read_bytes()
    if (root / 'input.json').is_symlink() or payload != json_bytes(expected['input']):
        raise ProbeError('prepared_input_changed')
    return contract, payload


def verify_baseline(root, contract, ack):
    baseline = read_json(Path(root) / 'baseline.json')
    expected = {'schema_version': SCHEMA + '.baseline-ack', 'session_id': contract['session_id'],
                'baseline_sha256': file_sha(Path(root) / 'baseline.json'), 'independent_archive_verified': True}
    if (not isinstance(ack, dict) or set(ack) != set(expected) | {'archive_location'}
            or any(ack.get(k) != v for k, v in expected.items())
            or not isinstance(ack.get('archive_location'), str) or not ack['archive_location'].strip()):
        raise ProbeError('baseline_ack_mismatch')
    inventory = ns.inventory(root)
    if (baseline.get('inventory') != inventory or baseline.get('inventory_sha256') != sha(json_bytes(inventory))
            or baseline.get('contract_sha256') != sha(json_bytes(contract))
            or baseline.get('input_sha256') != sha(json_bytes(contract['input']))):
        raise ProbeError('baseline_state_changed')


def claim_once(root, phase, contract):
    if phase not in ('original', 'resume'):
        raise ProbeError('unknown_phase')
    try:
        write_new(Path(root) / (phase + '-intent.json'), {'phase': phase, 'session_id': contract['session_id'],
                  'contract_sha256': sha(json_bytes(contract)), 'source_pins': pins()})
    except ProbeError as exc:
        if str(exc) == 'immutable_file_already_exists':
            raise ProbeError('segment_already_claimed') from None
        raise


def configuration(root, binary, contract, token, *, resume=False):
    if contract != make_contract(contract.get('qualification_id'), contract.get('session_id')):
        raise ProbeError('fixed_synthetic_contract_required')
    if not isinstance(token, str) or not 12 <= len(token) <= 32768 or any(c in token for c in '\r\n\0'):
        raise ProbeError('invalid_credential_channel')
    root = Path(root).absolute()
    capture = root / 'capture' / ('resumed' if resume else 'original')
    env = {'HOME': str(root / 'home'), 'CLAUDE_CONFIG_DIR': str(root / 'config'), 'TMPDIR': str(root / 'tmp') + '/',
           'XDG_RUNTIME_DIR': str(root / 'xdg'), 'PATH': str(Path(binary).parent) + ':/usr/local/bin:/usr/bin:/bin',
           'USER': 'agenttime', 'LOGNAME': 'agenttime', 'SHELL': '/bin/bash', 'LANG': 'C.UTF-8', 'TERM': 'dumb',
           'CLAUDE_CODE_OAUTH_TOKEN': token, 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
           'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_ATTACHMENTS': '1', 'CLAUDE_CODE_DISABLE_CLAUDE_MDS': '1',
           'CLAUDE_CODE_MAX_RETRIES': '0', 'CLAUDE_CODE_NO_MODEL_FALLBACK': '1',
           'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK': '1', 'CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS': '0',
           'OTEL_LOG_RAW_API_BODIES': 'file:' + str(capture / 'raw-bodies')}
    if resume:
        env['CLAUDE_CODE_RESUME_INTERRUPTED_TURN'] = '1'
    # Native MCP child has no auth, inherited configuration or network client.
    module_root = str(Path(ns.__file__).resolve().parent.parent)
    mcp = {'mcpServers': {'interruption': {'command': '/usr/bin/env', 'args': ['-i',
        'PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=' + str(root / 'home'), 'TMPDIR=' + str(root / 'tmp'),
        'PYTHONPATH=' + module_root, 'PYTHONDONTWRITEBYTECODE=1', 'LANG=C.UTF-8', sys.executable,
        str(SELF), 'tool', '--root', str(root)]}}}
    settings = {'disableAllHooks': True, 'disableClaudeAiConnectors': True, 'autoMemoryEnabled': False,
                'includeGitInstructions': False, 'attribution': {'commit': '', 'pr': ''}}
    command = [str(binary), '-p', '--model', ns.CLI_MODEL, '--effort', 'max', '--tools', '',
               '--allowedTools', TOOL_NAME, '--permission-mode', 'dontAsk', '--strict-mcp-config',
               '--mcp-config', json.dumps(mcp), '--setting-sources', '', '--settings', json.dumps(settings),
               '--disable-slash-commands', '--input-format', 'stream-json', '--output-format', 'stream-json',
               '--verbose', '--debug-file', str(capture / 'debug.log')]
    command += ['--resume' if resume else '--session-id', contract['session_id']]
    return {'command': command, 'environment': env, 'stdin_bytes': b'' if resume else json_bytes(contract['input'])}


def record_once(root):
    root = Path(root)
    verify_prepared(root)
    calls = root / 'work/tool-calls.jsonl'
    with calls.open('ab') as stream:
        stream.write(json_bytes({'operation': 'record_once', 'monotonic_ns': time.monotonic_ns()}))
        stream.flush(); os.fsync(stream.fileno())
    try:
        write_new(root / 'work/once-effect.txt', EFFECT_TEXT.encode())
    except ProbeError:
        raise ProbeError('duplicate_tool_effect_attempt') from None
    return TOOL_RESULT


def verify_effect(root):
    root = Path(root)
    if (len(read_rows(root / 'work/tool-calls.jsonl')) != 1
            or (root / 'work/once-effect.txt').read_bytes() != EFFECT_TEXT.encode()):
        raise ProbeError('tool_not_exactly_once')


def tool_main(root):
    # JSON-RPC stdio MCP server. No native shell/browser/network capabilities.
    for line in sys.stdin.buffer:
        req = None
        try:
            req = loads(line)
            if not isinstance(req, dict) or 'id' not in req:
                continue
            method = req.get('method')
            if method == 'initialize':
                result = {'protocolVersion': req.get('params', {}).get('protocolVersion', '2024-11-05'),
                          'capabilities': {'tools': {}}, 'serverInfo': {'name': 'synthetic-interruption', 'version': '1'}}
            elif method == 'tools/list':
                result = {'tools': [{'name': 'record_once', 'description': 'Record one durable synthetic effect exactly once.',
                          'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]}
            elif method == 'tools/call':
                params = req.get('params', {})
                if params.get('name') != 'record_once' or params.get('arguments', {}) != {}:
                    raise ProbeError('unexpected_tool_request')
                result = {'content': [{'type': 'text', 'text': record_once(root)}], 'isError': False}
            elif method == 'ping':
                result = {}
            else:
                raise ProbeError('unsupported_rpc_method')
            reply = {'jsonrpc': '2.0', 'id': req['id'], 'result': result}
        except (ProbeError, OSError, TypeError):
            reply = {'jsonrpc': '2.0', 'id': req.get('id') if isinstance(req, dict) else None,
                     'error': {'code': -32602, 'message': 'synthetic_tool_rejected'}}
        sys.stdout.buffer.write(json_bytes(reply)); sys.stdout.buffer.flush()


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list) or any(not isinstance(x, dict) for x in content):
        raise ProbeError('unsupported_native_content')
    return ''.join(x.get('text', '') for x in content if x.get('type') == 'text')


def check_native_rows(rows, contract, root, *, resumed):
    tasks = []; calls = []; results = []; markers = []; semantic = []; seen = set()
    for row in rows:
        if row.get('type') not in ('user', 'assistant'):
            continue
        if (row.get('sessionId') != contract['session_id'] or row.get('cwd') != str(Path(root) / 'work')
                or not isinstance(row.get('message'), dict) or row['message'].get('role') != row['type']):
            raise ProbeError('native_identity_mismatch')
        if row.get('uuid') in seen:
            raise ProbeError('repeated_native_message')
        seen.add(row.get('uuid'))
        msg = row['message']; semantic.append(row)
        content = msg.get('content'); blocks = content if isinstance(content, list) else []
        if row['type'] == 'assistant':
            if msg.get('model') != ns.MODEL:
                raise ProbeError('native_model_mismatch')
            for block in blocks:
                if block.get('type') == 'tool_use':
                    if block.get('name') != TOOL_NAME or block.get('input') != {}:
                        raise ProbeError('native_tool_escalation')
                    calls.append(block)
                elif block.get('type') not in ('text', 'thinking', 'redacted_thinking'):
                    raise ProbeError('unsupported_native_content')
        else:
            tool_results = [x for x in blocks if x.get('type') == 'tool_result']
            if tool_results:
                if len(tool_results) != len(blocks):
                    raise ProbeError('unexpected_native_user_context')
                results.extend(tool_results)
            elif _text(content) == PROMPT and not row.get('isMeta'):
                tasks.append(row)
            elif _text(content) == NATIVE_RESUME_MARKER and row.get('isMeta') is True:
                markers.append(row)
            else:
                raise ProbeError('unexpected_native_user_context')
    if len(tasks) != 1 or len(calls) != 1 or len(results) != 1:
        raise ProbeError('native_task_or_tool_replayed_or_missing')
    call, result = calls[0], results[0]
    if (not isinstance(call.get('id'), str) or result.get('tool_use_id') != call['id']
            or result.get('is_error') not in (None, False) or _text(result.get('content')) != TOOL_RESULT):
        raise ProbeError('native_tool_result_unverified')
    if resumed:
        if len(markers) != 1 or semantic[-1]['type'] != 'assistant' or _text(semantic[-1]['message']['content']).strip() != FINAL_MARKER:
            raise ProbeError('native_recovery_result_unverified')
    elif markers or semantic[-1]['type'] != 'user' or results[0] not in semantic[-1]['message']['content']:
        raise ProbeError('not_interrupted_after_tool_result')
    return {'task_prompt_count': len(tasks), 'tool_call_count': len(calls), 'tool_result_count': len(results),
            'tool_use_id': call['id'], 'native_recovery_marker_count': len(markers),
            'native_recovery_marker_is_meta': bool(markers), 'no_added_context_claim': False}


def transcript_path(root, session_id):
    project = re.sub(r'[^A-Za-z0-9]', '-', str(Path(root) / 'work'))
    return Path(root) / 'config/projects' / project / (session_id + '.jsonl')


class Timeline:
    def __init__(self, path, session_id, boot_id):
        self.path = Path(path); self.session_id = session_id; self.boot_id = boot_id
        self.boundaries = {}; self.last = None
        if self.path.exists():
            raise ProbeError('timeline_already_exists')

    def event(self, kind, monotonic_ns=None, **metadata):
        stamp = time.monotonic_ns() if monotonic_ns is None else monotonic_ns
        if type(stamp) is not int or stamp < 0 or self.last is not None and stamp < self.last:
            raise ProbeError('nonmonotonic_clock')
        if kind in BOUNDARIES:
            if kind in self.boundaries:
                raise ProbeError('duplicate_boundary')
            if list(self.boundaries) != list(BOUNDARIES[:len(self.boundaries)]) or kind != BOUNDARIES[len(self.boundaries)]:
                raise ProbeError('boundary_order_mismatch')
            self.boundaries[kind] = stamp
        self.last = stamp
        row = {'sequence_clock': 'host_boot_monotonic_ns', 'boot_id': self.boot_id, 'session_id': self.session_id,
               'kind': kind, 'monotonic_ns': stamp, 'audit_utc': datetime.now(timezone.utc).isoformat(), **metadata}
        with self.path.open('ab') as stream:
            stream.write(json_bytes(row)); stream.flush(); os.fsync(stream.fileno())

    def metrics(self):
        if tuple(self.boundaries) != BOUNDARIES:
            raise ProbeError('incomplete_timing_boundaries')
        b = self.boundaries
        return {'clock_scope': 'one_host_boot_one_live_subreaper', 'boot_id': self.boot_id,
                'boundaries_monotonic_ns': dict(b), 'cross_boot_or_host_qualified': False,
                'runtime_seconds_including_interruption': (b['resume_drained'] - b['original_released']) / 1e9,
                'interruption_seconds_including_archival_and_restart': (b['resume_released'] - b['original_sigkill']) / 1e9}


def verify_interruption_ack(receipt, ack, boot_id):
    if (receipt.get('original_stopped') is not True or receipt.get('owned_census_empty') is not True
            or receipt.get('waitpid_echild') is not True or receipt.get('original_exit_code') != -signal.SIGKILL
            or not receipt.get('process_identities')):
        raise ProbeError('original_execution_stop_unproven')
    if receipt.get('boot_id') != boot_id:
        raise ProbeError('same_boot_continuation_required')
    expected = {'schema_version': SCHEMA + '.interruption-ack', 'session_id': receipt['session_id'],
                'interruption_sha256': sha(json_bytes(receipt)), 'boot_id': boot_id,
                'owner_pid': receipt['owner_pid'], 'process_identities': receipt['process_identities'],
                'checkpoint_inventory_sha256': receipt['checkpoint_inventory_sha256'],
                'original_stopped_verified': True, 'independent_archive_verified': True}
    if (not isinstance(ack, dict) or set(ack) != set(expected) | {'archive_location'}
            or any(ack.get(key) != value for key, value in expected.items())
            or not isinstance(ack.get('archive_location'), str) or not ack['archive_location'].strip()):
        raise ProbeError('interruption_ack_mismatch')


class StreamState:
    def __init__(self, contract, *, resumed):
        self.contract = contract; self.resumed = resumed
        self.initialized = False; self.result = None; self.usage = []; self.events = 0

    def observe(self, row):
        if not isinstance(row, dict):
            raise ProbeError('malformed_native_stream')
        self.events += 1
        if row.get('type') == 'system' and row.get('subtype') == 'init':
            if self.initialized:
                raise ProbeError('duplicate_native_init')
            if (row.get('session_id') != self.contract['session_id'] or row.get('model') not in (ns.MODEL, ns.CLI_MODEL)
                    or row.get('claude_code_version') != ns.CLI_VERSION):
                raise ProbeError('native_identity_mismatch')
            servers = row.get('mcp_servers')
            if (row.get('tools') != [TOOL_NAME] or row.get('plugins') != [] or row.get('skills') != []
                    or not isinstance(servers, list) or len(servers) != 1
                    or not isinstance(servers[0], dict) or servers[0].get('name') != 'interruption'
                    or servers[0].get('status') != 'connected'):
                raise ProbeError('native_capabilities_unverified')
            self.initialized = True
        if row.get('type') == 'rate_limit_event':
            usage = ns._usage_metadata(row)
            if usage:
                self.usage.append(usage)
                if usage.get('isUsingOverage') is True or usage.get('status') == 'rejected':
                    raise ProbeError('subscription_route_unverified')
        if row.get('api_error_status') in (401, 403, 429):
            raise ProbeError('subscription_route_unverified')
        metadata = ns.event_metadata(row)
        if metadata.get('model') not in (None, ns.MODEL) or set(metadata['tool_names']) - {TOOL_NAME}:
            raise ProbeError('native_model_or_tools_changed')
        if row.get('type') == 'result':
            if not self.resumed:
                raise ProbeError('original_completed_before_interruption')
            if row.get('resume_reason') != 'interrupted_turn':
                raise ProbeError('native_resume_reason_unverified')
            if (not self.initialized or self.result is not None or row.get('session_id') != self.contract['session_id']
                    or row.get('subtype') != 'success' or row.get('is_error') is not False
                    or not isinstance(row.get('result'), str) or row['result'].strip() != FINAL_MARKER):
                raise ProbeError('native_result_unverified')
            self.result = {'session_id': row['session_id'], 'resume_reason': row['resume_reason'],
                           'result_sha256': sha(row['result'].encode())}


def identities_stopped(identities, process_table):
    return bool(identities) and all(process_table.get(int(pid), (None, None))[0] == start
               and process_table[int(pid)][1] in ('T', 't', 'Z') for pid, start in identities.items())


def process_table():
    rows = {}
    for folder in Path('/proc').iterdir():
        if not folder.name.isdigit():
            continue
        try:
            parts = (folder / 'stat').read_text().rsplit(') ', 1)[1].split()
            rows[int(folder.name)] = (int(parts[19]), parts[0])
        except (OSError, ValueError, IndexError):
            continue
    return rows


def freeze_owned():
    previous = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        identities = ns._owned_processes()
        if not identities:
            raise ProbeError('no_original_process_to_interrupt')
        ns._signal_owned(identities, signal.SIGSTOP)
        current = ns._owned_processes()
        if current == previous == identities and identities_stopped(identities, process_table()):
            return {str(pid): start for pid, start in identities.items()}
        previous = current
        time.sleep(.01)
    raise ProbeError('original_freeze_unproven')


def reap_owned(proc):
    echild = False
    while True:
        try:
            pid, status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            echild = True; break
        if not pid:
            break
        if pid == proc.pid:
            proc.returncode = os.waitstatus_to_exitcode(status)
    return echild


def kill_and_drain(proc):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ns._signal_owned(ns._owned_processes(), signal.SIGKILL)
        echild = reap_owned(proc)
        if echild and not ns._owned_processes() and proc.returncode is not None:
            return {'root_exit_code': proc.returncode, 'owned_census_empty': True, 'waitpid_echild': True}
        time.sleep(.01)
    raise ProbeError('original_stop_or_drain_unproven')


def verify_checkpoint(root, inventory_sha256):
    archive = Path(root) / 'interrupted-archive'
    if archive.is_symlink() or sha(json_bytes(ns.inventory(archive))) != inventory_sha256:
        raise ProbeError('checkpoint_archive_changed')


def _consume(stream, state, timeline, *, phase):
    while True:
        position = stream.tell(); line = stream.readline()
        if not line or not line.endswith(b'\n'):
            stream.seek(position); return
        row = loads(line)
        state.observe(row)
        timeline.event('native_event', phase=phase, metadata=ns.event_metadata(row))
        if row.get('type') == 'result':
            timeline.event('resume_result')


def launch_segment(root, binary, contract, token, payload, timeline, *, resume, deadline):
    root = Path(root); conf = configuration(root, binary, contract, token, resume=resume)
    if conf['stdin_bytes'] != (b'' if resume else payload):
        raise ProbeError('delivery_bytes_mismatch')
    ns.verify_binary(binary)
    if ns._owned_processes():
        raise ProbeError('original_execution_not_fenced')
    phase = 'resume' if resume else 'original'
    claim_once(root, phase, contract)
    capture = root / 'capture' / ('resumed' if resume else 'original')
    state = StreamState(contract, resumed=resume); proc = None; stopped = False
    try:
        with (capture / 'stream.jsonl').open('xb') as output, (capture / 'stderr.txt').open('xb') as error:
            proc = subprocess.Popen(conf['command'], cwd=root / 'work', env=conf['environment'], stdin=subprocess.PIPE,
                                    stdout=output, stderr=error, start_new_session=True)
            timeline.event(phase + '_process_started', pid=proc.pid, owner_pid=os.getpid())
            timeline.event(phase + '_released', input_bytes=len(conf['stdin_bytes']), input_sha256=sha(conf['stdin_bytes']))
            if conf['stdin_bytes']:
                proc.stdin.write(conf['stdin_bytes']); proc.stdin.flush()
            proc.stdin.close()
            timeline.event(phase + '_input_closed')
            with (capture / 'stream.jsonl').open('rb') as stream:
                while time.monotonic() < deadline:
                    _consume(stream, state, timeline, phase=phase)
                    if not resume and state.initialized:
                        path = transcript_path(root, contract['session_id'])
                        if path.exists():
                            try:
                                check_native_rows(read_rows(path), contract, root, resumed=False)
                            except ProbeError:
                                pass  # No checkpoint yet; final stream errors still fail above.
                            else:
                                identities = freeze_owned()
                                _consume(stream, state, timeline, phase=phase)
                                with path.open('rb') as source:
                                    os.fsync(source.fileno())
                                proof = check_native_rows(read_rows(path), contract, root, resumed=False)
                                verify_effect(root)
                                timeline.event('checkpoint_fsynced', transcript_sha256=file_sha(path))
                                timeline.event('original_sigkill', process_identities=identities)
                                drain = kill_and_drain(proc); stopped = True
                                if drain['root_exit_code'] != -signal.SIGKILL:
                                    raise ProbeError('original_not_physically_killed')
                                _consume(stream, state, timeline, phase=phase)
                                timeline.event('original_drained', **drain)
                                if not ns.subscription_allowance_verified(state.usage):
                                    raise ProbeError('subscription_allowance_unverified')
                                return {'native': proof, 'process_identities': identities, 'drain': drain,
                                        'usage_evidence': state.usage, 'native_stream_events': state.events}
                    echild = reap_owned(proc)
                    if proc.returncode is not None and echild and not ns._owned_processes():
                        _consume(stream, state, timeline, phase=phase); stopped = True
                        if stream.read().strip():
                            raise ProbeError('partial_native_stream')
                        if not resume:
                            raise ProbeError('original_exited_without_interruption')
                        if proc.returncode != 0 or state.result is None:
                            raise ProbeError('resume_did_not_complete_native_turn')
                        timeline.event('resume_drained', root_exit_code=proc.returncode, owned_census_empty=True, waitpid_echild=True)
                        if not ns.subscription_allowance_verified(state.usage):
                            raise ProbeError('subscription_allowance_unverified')
                        return {'result': state.result, 'usage_evidence': state.usage, 'native_stream_events': state.events,
                                'drain': {'root_exit_code': 0, 'owned_census_empty': True, 'waitpid_echild': True}}
                    time.sleep(.005)
                raise ProbeError('synthetic_qualification_watchdog')
    finally:
        if proc is not None and not stopped:
            kill_and_drain(proc)


def _wait_ack(path, deadline):
    while time.monotonic() < deadline:
        if Path(path).exists():
            return read_json(path)
        time.sleep(.1)
    raise ProbeError('independent_ack_not_received')


def inspect_transport(capture, contract):
    raw = Path(capture) / 'raw-bodies'
    index = read_rows(raw / 'index.jsonl')
    if not index:
        raise ProbeError('transport_capture_missing')
    for entry in index:
        relative = entry.get('request_file')
        if not isinstance(relative, str) or Path(relative).name != relative:
            raise ProbeError('unsafe_transport_capture_path')
        body = read_json(raw / relative)
        if (entry.get('session_id') != contract['session_id'] or body.get('model') != ns.MODEL
                or body.get('output_config', {}).get('effort') != 'max'
                or [tool.get('name') for tool in body.get('tools', [])] != [TOOL_NAME]):
            raise ProbeError('transport_model_or_tools_unverified')
        messages = body.get('messages')
        if (not isinstance(messages, list) or not messages
                or not ns.initial_message_matches(messages[0], contract['input']['message'],
                                                   str(Path(capture).parents[1]), contract['session_id'])):
            raise ProbeError('transport_task_prompt_unverified')
    return {'request_count': len(index), 'model': ns.MODEL, 'effort': 'max', 'tools': [TOOL_NAME],
            'first_segment_may_contain_request_interrupted_by_sigkill': Path(capture).name == 'original'}


def run(root, binary, baseline_ack_path, interruption_ack_path, token, *, watchdog_seconds=600):
    root = Path(root).absolute()
    if str(root) != '/runtime-root' or sys.platform != 'linux':
        raise ProbeError('dedicated_linux_runtime_root_required')
    if type(watchdog_seconds) is not int or not 30 <= watchdog_seconds <= 1800:
        raise ProbeError('invalid_synthetic_watchdog')
    contract, payload = verify_prepared(root)
    verify_baseline(root, contract, read_json(baseline_ack_path))
    ns.verify_binary(binary)
    if ns._owned_processes():
        raise ProbeError('dedicated_subreaper_required')
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise ProbeError('subreaper_unavailable')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    timeline = Timeline(root / 'timeline.jsonl', contract['session_id'], boot)
    deadline = time.monotonic() + watchdog_seconds
    report = {'schema_version': SCHEMA + '.report', 'qualification_id': contract['qualification_id'],
              'session_id': contract['session_id'], 'purpose': 'synthetic_qualification_only', 'study_ready': False,
              'qualified': False, 'source_pins': pins(), 'cli_sha256': ns.CLI_SHA256,
              'independent_final_archive_acknowledgement': False, 'external_container_stop_verified': False,
              'qualification_watchdog_seconds': watchdog_seconds,
              'scope': 'same_host_boot_completed_tool_result_before_interruption', 'cross_host_or_boot_qualified': False}
    old_handlers = {}
    def stop_probe(number, frame):
        raise ProbeError('external_probe_stop')
    for number in (signal.SIGTERM, signal.SIGINT):
        old_handlers[number] = signal.signal(number, stop_probe)
    try:
        original = launch_segment(root, binary, contract, token, payload, timeline, resume=False, deadline=deadline)
        report['original'] = original
        archive = ns.capture_archive(root, token)
        (root / 'archive').rename(root / 'interrupted-archive')
        (root / 'archive-receipt.json').rename(root / 'interrupted-archive-receipt.json')
        timeline.event('checkpoint_archived', inventory_sha256=archive['inventory_sha256'])
        # Project directory names preserve the original runtime path in archives.
        cp_transcript = root / 'interrupted-archive' / transcript_path(root, contract['session_id']).relative_to(root)
        source_bytes = cp_transcript.read_bytes()
        interrupted = {'schema_version': SCHEMA + '.interrupted', 'qualification_id': contract['qualification_id'],
            'session_id': contract['session_id'], 'boot_id': boot, 'owner_pid': os.getpid(),
            'process_identities': original['process_identities'], 'original_exit_code': original['drain']['root_exit_code'],
            'original_stopped': True, 'owned_census_empty': True, 'waitpid_echild': True,
            'failure_class': 'synthetic_external_sigkill_after_durable_tool_result',
            'checkpoint_inventory_sha256': archive['inventory_sha256'], 'checkpoint_transcript_sha256': sha(source_bytes)}
        write_new(root / 'interruption.json', interrupted)
        ack = _wait_ack(interruption_ack_path, deadline)
        verify_interruption_ack(interrupted, ack, Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        verify_checkpoint(root, archive['inventory_sha256'])
        if ns.inventory(root) != archive['inventory'] or ns._owned_processes():
            raise ProbeError('interrupted_state_changed_or_unfenced')
        verify_prepared(root)
        timeline.event('independent_recovery_ack', acknowledgement_sha256=sha(json_bytes(ack)))
        resumed = launch_segment(root, binary, contract, token, b'', timeline, resume=True, deadline=deadline)
        report['resumed'] = resumed
        verify_checkpoint(root, archive['inventory_sha256'])
        transcript = transcript_path(root, contract['session_id'])
        if not transcript.read_bytes().startswith(source_bytes):
            raise ProbeError('original_native_prefix_changed')
        report['native_transcript'] = check_native_rows(read_rows(transcript), contract, root, resumed=True)
        verify_effect(root)
        report['transport'] = {name: inspect_transport(root / 'capture' / name, contract) for name in ('original', 'resumed')}
        report['timing'] = timeline.metrics()
        report['final_archive'] = ns.capture_archive(root, token)
        report['interruption_receipt_sha256'] = sha(json_bytes(interrupted))
        report['recovery_ack_sha256'] = sha(json_bytes(ack))
        report['same_session_id'] = True
        report['resumed_input_bytes'] = 0
        report['task_prompt_replayed'] = False
        report['tool_effect_count'] = 1
        report['native_recovery_context'] = {'kind': 'isMeta', 'text': NATIVE_RESUME_MARKER,
                                            'supplied_by': 'pinned_native_cli', 'no_added_context_claim': False}
        report['qualified'] = True
    except (ProbeError, ns.NativeSessionError, OSError, ValueError, TypeError, KeyError):
        # Detailed stable error code is recovered without provider text or stack locals.
        exc = sys.exc_info()[1]
        report['error'] = str(exc) if isinstance(exc, (ProbeError, ns.NativeSessionError)) else 'probe_local_failure'
    finally:
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
    write_new(root / 'interruption-report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--root', required=True)
    prep.add_argument('--qualification-id', required=True); prep.add_argument('--session-id', required=True)
    tool = sub.add_parser('tool'); tool.add_argument('--root', required=True)
    launch = sub.add_parser('run'); launch.add_argument('--root', required=True); launch.add_argument('--binary', required=True)
    launch.add_argument('--baseline-ack', required=True); launch.add_argument('--interruption-ack', required=True)
    launch.add_argument('--watchdog-seconds', type=int, default=600)
    args = parser.parse_args()
    if args.command == 'tool':
        tool_main(args.root); return
    if args.command == 'prepare':
        result = prepare(args.root, args.qualification_id, args.session_id)
    else:
        token = sys.stdin.read().removesuffix('\n')
        result = run(args.root, args.binary, args.baseline_ack, args.interruption_ack, token,
                     watchdog_seconds=args.watchdog_seconds)
    # Metadata report only; original session bodies remain private files.
    print(json.dumps(result, sort_keys=True))
    if args.command == 'run' and not result['qualified']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
