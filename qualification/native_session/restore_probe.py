"""Qualify continuation or a fork of one archived, fixed synthetic Claude session.

This is not a study runner. Run only inside a fresh, dedicated Linux container
at /tmp/at-native. Authentication is accepted on stdin, never in arguments or
files. The original closed-book canary bundle must be copied read-only, with its
historical source and archive receipts intact. inspect/prepare make no model calls.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import types
import uuid

HISTORICAL_SOURCE_SHA256 = '5a49c9a87cc763fc898849c304fa5fa9e13757a2e9e5da0f70c5670e2fa4b377'
RUNTIME_ROOT = '/tmp/at-native'
RUNTIME_CWD = RUNTIME_ROOT + '/work'
EXPECTED_MARKER = 'SYNTHETIC_NATIVE_OK'
# Do not give the answer in the new prompt: this tests inherited native context.
PROBE_TEXT = 'Return only the exact synthetic marker requested in the previous user message, without using any tools.'
PROJECT_DIRECTORY = 'config/projects/-tmp-at-native-work'
SCHEMA = 'agenttime.synthetic-native-restore.v1'


class ProbeError(ValueError):
    """Stable error codes only; never retain provider or authentication text."""


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False) + '\n').encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _regular(path):
    path = Path(path).absolute()
    if path.is_symlink() or path.resolve() != path or not path.is_file():
        raise ProbeError('unsafe_metadata_path')
    return path


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProbeError('duplicate_metadata_key')
        result[key] = value
    return result


def _loads(data):
    def reject(value):
        raise ProbeError('nonfinite_metadata')
    try:
        return json.loads(data, object_pairs_hook=_pairs, parse_constant=reject)
    except (ValueError, TypeError) as exc:
        if isinstance(exc, ProbeError):
            raise
        raise ProbeError('malformed_metadata') from None


def read_json(path):
    return _loads(_regular(path).read_bytes())


def read_rows(path):
    rows = [_loads(line) for line in _regular(path).read_bytes().splitlines() if line.strip()]
    if any(not isinstance(row, dict) for row in rows):
        raise ProbeError('malformed_native_rows')
    return rows


def write_new(path, data):
    with Path(path).open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def tree_digest(root):
    """Hash all bytes/modes and literal link targets without following links.

    The raw original capture may contain a historical absolute debug link. Only
    the separately verified sealed archive is restored; raw links stay evidence.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise ProbeError('unsafe_source_root')
    root = root.resolve()
    entries = {}
    for path in (root, *sorted(root.rglob('*'))):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            entries[relative] = {'link': os.readlink(path)}
        elif stat.S_ISDIR(info.st_mode):
            entries[relative] = {'directory_mode': stat.S_IMODE(info.st_mode)}
        elif stat.S_ISREG(info.st_mode):
            entries[relative] = {'mode': stat.S_IMODE(info.st_mode), 'bytes': info.st_size, 'sha256': file_sha(path)}
        else:
            raise ProbeError('unsupported_source_object')
    return sha(json_bytes(entries))


def load_historical(source, expected_sha):
    path = _regular(Path(source).resolve() / 'runtime/agenttime/native_session.py')
    data = path.read_bytes()
    if expected_sha != HISTORICAL_SOURCE_SHA256 or sha(data) != HISTORICAL_SOURCE_SHA256:
        raise ProbeError('historical_source_pin_mismatch')
    # Execute only the allowlisted reviewed source bytes; never import from an
    # arbitrary archived module or create __pycache__ in the immutable source.
    module = types.ModuleType('agenttime_approved_native_restore_runtime')
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module


def transcript_relative(session_id):
    return PROJECT_DIRECTORY + '/' + session_id + '.jsonl'


def probe_input():
    return {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': PROBE_TEXT}]}}


def _text(message):
    content = message.get('content')
    if not isinstance(content, list) or any(not isinstance(block, dict) for block in content):
        raise ProbeError('unsupported_native_content')
    if any(block.get('type') not in ('text', 'thinking') for block in content):
        raise ProbeError('unexpected_native_tool_or_content')
    texts = [block.get('text') for block in content if block.get('type') == 'text']
    if any(not isinstance(text, str) for text in texts):
        raise ProbeError('unsupported_native_content')
    return ''.join(texts)


def _native_messages(path, allowed_sessions):
    messages = []
    for row in read_rows(path):
        if row.get('type') not in ('user', 'assistant'):
            continue
        if row.get('sessionId') not in allowed_sessions or row.get('cwd') != RUNTIME_CWD:
            raise ProbeError('native_transcript_identity_mismatch')
        message = row.get('message')
        if not isinstance(message, dict) or message.get('role') != row['type']:
            raise ProbeError('malformed_native_message')
        if row['type'] == 'assistant' and message.get('model') != 'claude-opus-5-5':
            raise ProbeError('native_transcript_model_mismatch')
        _text(message)
        messages.append(row)
    return messages


def _stream_identity(capture, session_id, ns):
    rows = read_rows(Path(capture) / 'stream.jsonl')
    inits = [row for row in rows if row.get('type') == 'system' and row.get('subtype') == 'init']
    results = [row for row in rows if row.get('type') == 'result']
    if len(inits) != 1 or len(results) != 1:
        raise ProbeError('native_completion_unverified')
    init, result = inits[0], results[0]
    if (init.get('session_id') != session_id or result.get('session_id') != session_id
            or init.get('claude_code_version') != ns.CLI_VERSION or init.get('model') not in (ns.MODEL, ns.CLI_MODEL)):
        raise ProbeError('native_identity_mismatch')
    if any(init.get(key) != [] for key in ('tools', 'mcp_servers', 'skills', 'plugins')):
        raise ProbeError('native_capability_mismatch')
    for row in rows:
        if ns.event_metadata(row)['tool_names']:
            raise ProbeError('unexpected_native_tool_or_content')
        message = row.get('message')
        if isinstance(message, dict) and message.get('model') not in (None, ns.MODEL):
            raise ProbeError('native_transcript_model_mismatch')
    if result.get('subtype') != 'success' or result.get('is_error') is not False or result.get('result') != EXPECTED_MARKER:
        raise ProbeError('marker_result_unverified')
    if _regular(Path(capture) / 'sealed-answer.txt').read_bytes() != EXPECTED_MARKER.encode():
        raise ProbeError('marker_result_unverified')


def verify_source(source, expected_tree_sha=None):
    source = Path(source)
    before = tree_digest(source)
    source = source.resolve()
    if expected_tree_sha is not None and before != expected_tree_sha:
        raise ProbeError('source_tree_changed')
    state = source / 'final-state'
    contract = read_json(state / 'contract.json')
    ns = load_historical(source, contract.get('source_sha256') if isinstance(contract, dict) else None)
    try:
        ns.validate_contract(contract)
        if contract['case'] != 'closed_book' or contract['transport'] != 'subscription':
            raise ProbeError('only_original_closed_book_subscription_supported')
        report = read_json(state / 'report.json')
        execution = report.get('execution', {})
        if (any(report.get(key) is not True for key in ('qualified', 'archive_verified', 'included_subscription_allowance_verified'))
                or report.get('qualification_id') != contract['qualification_id'] or report.get('session_id') != contract['session_id']
                or report.get('requested_route') != 'subscription' or execution.get('timing_valid') is not True
                or execution.get('session_id') != contract['session_id'] or execution.get('issues') != []
                or execution.get('cancelled') is not False or execution.get('root_exit_code') != 0
                or any(type(execution.get(k)) is not int for k in ('prompt_released_monotonic_ns', 'result_monotonic_ns',
                    'root_exit_monotonic_ns', 'owned_work_drained_monotonic_ns', 'native_terminal_monotonic_ns'))):
            raise ProbeError('source_qualification_unverified')
        stop = read_json(source / 'stop-proof.json')
        owned = read_json(source / 'owned-container.json')
        if (stop.get('stopped') is not True or stop.get('scope') != 'synthetic_qualification_only'
                or not stop.get('container_id') or stop.get('container_id') != owned.get('container_id')
                or owned.get('purpose') != 'synthetic_qualification_only'):
            raise ProbeError('original_stop_unverified')
        archive = state / 'archive'
        if archive.is_symlink() or archive.resolve() != archive:
            raise ProbeError('unsafe_archive_root')
        receipt = read_json(state / 'archive-receipt.json')
        inventory = ns.inventory(archive)
        inventory_hash = sha(json_bytes(inventory))
        if (receipt.get('verified') is not True or receipt.get('schema_version') != 'agenttime.native-local-archive.v1'
                or receipt.get('inventory') != inventory or receipt.get('inventory_sha256') != inventory_hash):
            raise ProbeError('archive_inventory_mismatch')
        ack = read_json(source / 'independent-archive-ack.json')
        if (ack.get('verified') is not True or ack.get('inventory_sha256') != inventory_hash
                or not isinstance(ack.get('location'), str) or not ack['location'].strip()):
            raise ProbeError('independent_archive_unverified')
        transport = ns.inspect_transport(archive / 'capture/raw-bodies', contract)
        if transport['issues'] or transport['request_count'] != 1:
            raise ProbeError('source_transport_unverified')
        _stream_identity(archive / 'capture', contract['session_id'], ns)
        transcript = archive / transcript_relative(contract['session_id'])
        rows = _native_messages(transcript, {contract['session_id']})
        users = [row for row in rows if row['type'] == 'user']
        assistants = [row for row in rows if row['type'] == 'assistant']
        if (len(users) != 1 or users[0]['message'] != contract['input']['message']
                or ''.join(_text(row['message']) for row in assistants) != EXPECTED_MARKER):
            raise ProbeError('source_native_context_unverified')
        native_files = sorted(p.relative_to(archive).as_posix() for p in (archive / 'config/projects').rglob('*.jsonl'))
        if native_files != [transcript_relative(contract['session_id'])]:
            raise ProbeError('extra_native_session_in_source')
    except ns.NativeSessionError as exc:
        raise ProbeError(str(exc)) from None
    if tree_digest(source) != before:
        raise ProbeError('source_tree_changed')
    return {'source_location': str(source), 'source_tree_sha256': before,
            'source_sha256': contract['source_sha256'], 'session_id': contract['session_id'],
            'archive_inventory': inventory, 'archive_inventory_sha256': inventory_hash,
            'original_transcript_sha256': file_sha(transcript), 'original_transcript_bytes': transcript.stat().st_size,
            'cli_sha256': ns.CLI_SHA256, 'cli_version': ns.CLI_VERSION, 'study_ready': False}


def _session(operation, original, supplied):
    if operation == 'continue':
        if supplied is not None and supplied != original:
            raise ProbeError('continuation_must_keep_session')
        return original
    if operation != 'fork':
        raise ProbeError('unsupported_restore_operation')
    try:
        valid = str(uuid.UUID(supplied)) == supplied and supplied != original
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ProbeError('fresh_fork_session_required')
    return supplied


def _target_environment():
    # The pinned CLI appends this native update when moving between Linux hosts.
    # Freeze actual host facts before onset; never learn allowed instructions
    # from the subsequent model request itself.
    return ('# Environment\nYou have been invoked in the following environment: \n'
        ' - Primary working directory: ' + RUNTIME_CWD + '\n - Is a git repository: false\n'
        ' - Platform: linux\n - Shell: bash\n - OS Version: ' + os.uname().sysname + ' ' + os.uname().release)


def _manifest(info, operation, session_id, qualification_id):
    return {'schema_version': SCHEMA, 'operation': operation, 'qualification_id': qualification_id,
            'source_location': info['source_location'], 'source_tree_sha256': info['source_tree_sha256'],
            'historical_source_sha256': info['source_sha256'], 'helper_sha256': file_sha(__file__),
            'original_session_id': info['session_id'], 'expected_session_id': session_id,
            'runtime_root': RUNTIME_ROOT, 'runtime_cwd': RUNTIME_CWD,
            'target_native_environment': _target_environment(),
            'cli_sha256': info['cli_sha256'], 'cli_version': info['cli_version'],
            'restored_inventory': info['archive_inventory'], 'archive_inventory_sha256': info['archive_inventory_sha256'],
            'original_transcript_sha256': info['original_transcript_sha256'],
            'original_transcript_bytes': info['original_transcript_bytes'],
            'probe_input_sha256': sha(json_bytes(probe_input())), 'study_ready': False}


def prepare(source, destination, expected_tree_sha, operation, *, session_id=None):
    source = Path(source).resolve()
    destination = Path(destination).absolute()
    resolved = destination.resolve()
    if resolved.is_relative_to(source) or source.is_relative_to(resolved):
        raise ProbeError('source_destination_overlap')
    if destination.exists() or destination.is_symlink():
        raise ProbeError('destination_not_fresh')
    if not isinstance(expected_tree_sha, str) or len(expected_tree_sha) != 64:
        raise ProbeError('frozen_source_hash_required')
    info = verify_source(source, expected_tree_sha)
    expected_session = _session(operation, info['session_id'], session_id)
    qualification_id = 'synthetic-' + operation + '-' + uuid.uuid4().hex[:16]
    ns = load_historical(source, info['source_sha256'])
    ns.qualification_contract(qualification_id, expected_session)
    destination.mkdir(mode=0o700)
    for name in ns.STATE_ROOTS:
        shutil.copytree(source / 'final-state/archive' / name, destination / name, symlinks=True, copy_function=shutil.copy2)
    if tree_digest(source) != expected_tree_sha:
        raise ProbeError('source_tree_changed')
    try:
        if ns.inventory(destination) != info['archive_inventory']:
            raise ProbeError('restored_state_mismatch')
    except ns.NativeSessionError as exc:
        raise ProbeError(str(exc)) from None
    manifest = _manifest(info, operation, expected_session, qualification_id)
    write_new(destination / 'restore-manifest.json', json_bytes(manifest))
    return manifest


def configuration(root, binary, manifest, ns, token):
    contract = ns.qualification_contract(manifest['qualification_id'], manifest['expected_session_id'])
    conf = ns.configuration(root, binary, contract, token)
    command = conf['command']
    index = command.index('--session-id')
    del command[index:index + 2]
    command += ['--resume', manifest['original_session_id']]
    if manifest['operation'] == 'fork':
        command += ['--fork-session', '--session-id', manifest['expected_session_id']]
    return conf


def _empty_index_transport(root, manifest, contract, ns):
    """Read independent native artifacts for this one fixed synthetic restore.

    The pinned CLI can exit before its optional index append completes. This
    does not recover the index or prove a retry count. It is not a study-runtime
    admission rule and cannot admit missing requests, extra responses or tools.
    """
    root = Path(root); raw = root / 'capture/raw-bodies'; sid = contract['session_id']
    try:
        if contract['case'] != 'closed_book' or contract['tools'] != []:
            return None
        if _regular(raw / 'index.jsonl').read_bytes() != b'':
            return None
        requests = list(raw.glob('*.request.json')); responses = list(raw.glob('*.response.json'))
        if (len(requests) != 1 or len(responses) != 1
                or {p.name for p in raw.iterdir()} != {'index.jsonl', requests[0].name, responses[0].name}
                or _regular(responses[0]).read_bytes() != b''):
            return None
        body = read_json(requests[0])
        if (body.get('model') != ns.MODEL or body.get('output_config') != {'effort': 'max'}
                or body.get('tools') != [] or body.get('fallbacks') not in (None, [])
                or not isinstance(body.get('messages'), list) or not body['messages']
                or body['messages'][0] != contract['input']['message']):
            return None
        metadata = _loads(body.get('metadata', {}).get('user_id'))
        if (not isinstance(metadata, dict) or set(metadata) != {'device_id', 'account_uuid', 'session_id'}
                or metadata['session_id'] != sid or not isinstance(metadata['account_uuid'], str)
                or not isinstance(metadata['device_id'], str) or len(metadata['device_id']) != 64
                or any(c not in '0123456789abcdef' for c in metadata['device_id'])):
            return None
        # Check the previous message against the immutable original transcript,
        # including continuation where only its original byte prefix is retained.
        original = _regular(root / transcript_relative(manifest['original_session_id'])).read_bytes()
        original = original[:manifest['original_transcript_bytes']]
        if sha(original) != manifest['original_transcript_sha256'] or not original.endswith(b'\n'):
            return None
        inherited = [_loads(line) for line in original.splitlines() if line.strip()]
        previous = [row['message'].get('id') for row in inherited if row.get('type') == 'assistant']
        if (not previous or not isinstance(previous[-1], str) or not previous[-1]
                or body.get('diagnostics') != {'previous_message_id': previous[-1]}):
            return None
        native_path = root / transcript_relative(sid)
        if not _regular(native_path).read_bytes().endswith(b'\n'):
            return None
        native = _native_messages(native_path, {sid, manifest['original_session_id']})
        users = [index for index, row in enumerate(native) if row['type'] == 'user']
        if len(users) != 2 or native[users[-1]]['message'] != probe_input()['message']:
            return None
        current = native[users[-1] + 1:]
        if (not current or any(row['type'] != 'assistant' or row['sessionId'] != sid
                or row.get('isSidechain') is not False or row.get('version') != ns.CLI_VERSION
                or row['message'].get('type') != 'message' for row in current)):
            return None
        request_ids = {row.get('requestId') for row in current}
        message_ids = {row['message'].get('id') for row in current}
        if len(request_ids) != 1 or len(message_ids) != 1:
            return None
        request_id = next(iter(request_ids)); message_id = next(iter(message_ids))
        if (not isinstance(request_id, str) or not request_id.startswith('req_')
                or not request_id.removeprefix('req_') or any(not (c.isascii() and (c.isalnum() or c == '_')) for c in request_id)
                or responses[0].name != request_id + '.response.json'
                or not isinstance(message_id, str) or not message_id):
            return None
        stream = _regular(root / 'capture/stream.jsonl').read_bytes()
        if not stream.endswith(b'\n'):
            return None
        rows = [_loads(line) for line in stream.splitlines() if line.strip()]
        assistants = [row for row in rows if row.get('type') == 'assistant']
        results = [row for row in rows if row.get('type') == 'result']
        if (not assistants or len(results) != 1 or results[0].get('session_id') != sid
                or results[0].get('subtype') != 'success' or results[0].get('is_error') is not False
                or any(row.get('session_id') != sid or row.get('message', {}).get('id') != message_id
                    or row['message'].get('type') != 'message' or row['message'].get('model') != ns.MODEL for row in assistants)):
            return None
        return {'request_count': 1, 'request_count_evidence': 'one_original_request_file',
            'session_ids': [sid], 'request_ids': [request_id], 'request_file': requests[0].name,
            'native_stream_response_ids': [message_id], 'issues': [], 'index_available': False,
            'retry_count_verified': False, 'retry_count': None,
            'evidence_type': 'request_metadata_and_original_native_transcript',
            'descendant_capabilities_qualified': False}
    except (ProbeError, OSError, TypeError, KeyError, AttributeError):
        return None


def _probe_transport(raw, contract, ns, manifest=None):
    """Keep historical checks; support only an empty optional final response file.

    Evidence comes from the complete original stream, never a rewritten response
    or a fabricated replacement file. Other historical transport issues remain.
    """
    raw = Path(raw)
    transport = ns.inspect_transport(raw, contract)
    transport = {**transport, 'native_stream_response_ids': []}
    if manifest is not None and set(transport['issues']) == {'transport_coverage_incomplete', 'transport_session_mismatch'}:
        fallback = _empty_index_transport(raw.parent.parent, manifest, contract, ns)
        if fallback is not None:
            return {**fallback, 'historical_transport_issues': transport['issues']}
    if 'transport_response_unavailable' not in transport['issues']:
        return transport
    try:
        index = read_rows(raw / 'index.jsonl')
        if len(index) != 1:
            return transport
        record = index[0]; name = record.get('response_file'); sid = contract['session_id']
        if (not isinstance(name, str) or Path(name).name != name
                or record.get('query_source', 'sdk') != 'sdk' or record.get('session_id') != sid
                or not isinstance(record.get('message_id'), str) or not record['message_id']
                or _regular(raw / name).read_bytes() != b''):
            return transport
        stream = _regular(raw.parent / 'stream.jsonl').read_bytes()
        if not stream.endswith(b'\n'):
            return transport
        rows = [_loads(line) for line in stream.splitlines() if line.strip()]
        if any(not isinstance(row, dict) for row in rows):
            return transport
        results = [row for row in rows if row.get('type') == 'result']
        if (len(results) != 1 or results[0].get('session_id') != sid
                or results[0].get('subtype') != 'success' or results[0].get('is_error') is not False):
            return transport
        matched = any(row.get('type') == 'assistant' and row.get('session_id') == sid
            and isinstance(row.get('message'), dict) and row['message'].get('type') == 'message'
            and row['message'].get('id') == record['message_id']
            and row['message'].get('model') == ns.MODEL for row in rows)
        if matched:
            transport['issues'] = [code for code in transport['issues'] if code != 'transport_response_unavailable']
            transport['native_stream_response_ids'] = [record['message_id']]
    except (ProbeError, OSError, TypeError):
        pass  # Preserve the historical rejection rather than weaken it.
    return transport


def _environment_text(message):
    """Accept only the observed native text/string representation and cache tag."""
    if (not isinstance(message, dict) or set(message) != {'role', 'content', 'output_config'}
            or message['role'] != 'system' or message['output_config'] != {'effort': 'max'}):
        raise ProbeError('inherited_context_unverified')
    content = message['content']
    if isinstance(content, str):
        return content
    if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict)
            or set(content[0]) not in ({'type', 'text'}, {'type', 'text', 'cache_control'})
            or content[0]['type'] != 'text' or not isinstance(content[0]['text'], str)
            or ('cache_control' in content[0] and content[0]['cache_control'] != {'type': 'ephemeral', 'ttl': '1h'})):
        raise ProbeError('inherited_context_unverified')
    return content[0]['text']


def _original_environment(root, manifest, original_user):
    """Bind the only allowed system message to bytes in the sealed source archive."""
    def pinned(relative):
        entry = manifest['restored_inventory'].get('capture/' + relative)
        data = _regular(root / 'original-capture' / relative).read_bytes()
        if (not isinstance(entry, dict) or entry.get('type') != 'file'
                or entry.get('bytes') != len(data) or entry.get('sha256') != sha(data)):
            raise ProbeError('original_context_pin_mismatch')
        return data
    index = [_loads(line) for line in pinned('raw-bodies/index.jsonl').splitlines() if line.strip()]
    if len(index) != 1 or not isinstance(index[0], dict):
        raise ProbeError('original_context_pin_mismatch')
    name = index[0].get('request_file')
    if not isinstance(name, str) or Path(name).name != name:
        raise ProbeError('original_context_pin_mismatch')
    body = _loads(pinned('raw-bodies/' + name))
    messages = body.get('messages') if isinstance(body, dict) else None
    if not isinstance(messages, list) or len(messages) not in (1, 2) or messages[0] != original_user:
        raise ProbeError('original_context_pin_mismatch')
    return _environment_text(messages[1]) if len(messages) == 2 else None


def _probe_message_matches(message):
    expected = probe_input()['message']
    if message == expected:
        return True
    # This cache marker is generated by the pinned CLI on the latest user block;
    # no text, additional key, role or other cache policy is normalized away.
    tagged = {'role': 'user', 'content': [{**expected['content'][0],
        'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}]}
    return message == tagged


def inspect_probe(root, manifest, ns):
    """Inspect real captured native files; return metadata, never conversation text."""
    root = Path(root)
    if root.is_symlink():
        raise ProbeError('unsafe_session_root')
    root = root.resolve()
    expected = manifest['expected_session_id']
    contract = ns.qualification_contract(manifest['qualification_id'], expected)
    transport = _probe_transport(root / 'capture/raw-bodies', contract, ns, manifest)
    if transport['issues'] or transport['request_count'] != 1:
        raise ProbeError('probe_transport_unverified')
    if transport.get('index_available') is False:
        request_file = transport['request_file']
    else:
        request_file = read_rows(root / 'capture/raw-bodies/index.jsonl')[0]['request_file']
    messages = read_json(root / 'capture/raw-bodies' / request_file).get('messages')
    environment = _original_environment(root, manifest, contract['input']['message'])
    expected_length = 4 if environment is not None else 3
    target_environment = None
    if isinstance(messages, list) and len(messages) == expected_length + 1:
        target_environment = manifest.get('target_native_environment')
        update = {'role': 'system', 'content': [{'type': 'text', 'text': target_environment,
            'cache_control': {'type': 'ephemeral', 'ttl': '1h'}}]}
        if not isinstance(target_environment, str) or messages[-1] != update:
            raise ProbeError('inherited_context_unverified')
        messages = messages[:-1]
    if (not isinstance(messages, list) or len(messages) != expected_length
            or messages[0] != contract['input']['message']):
        raise ProbeError('inherited_context_unverified')
    if environment is not None and _environment_text(messages[1]) != environment:
        raise ProbeError('inherited_context_unverified')
    if (not isinstance(messages[-2], dict) or messages[-2].get('role') != 'assistant'
            or _text(messages[-2]) != EXPECTED_MARKER or not _probe_message_matches(messages[-1])):
        raise ProbeError('inherited_context_unverified')
    _stream_identity(root / 'capture', expected, ns)
    original = root / transcript_relative(manifest['original_session_id'])
    parent_data = _regular(original).read_bytes()
    if manifest['operation'] == 'fork':
        if sha(parent_data) != manifest['original_transcript_sha256']:
            raise ProbeError('parent_transcript_changed')
    elif (len(parent_data) <= manifest['original_transcript_bytes']
            or sha(parent_data[:manifest['original_transcript_bytes']]) != manifest['original_transcript_sha256']):
        raise ProbeError('parent_transcript_changed')
    rows = _native_messages(root / transcript_relative(expected), {expected, manifest['original_session_id']})
    users = [row for row in rows if row['type'] == 'user']
    if len(users) != 2 or users[0]['message'] != contract['input']['message'] or users[1]['message'] != probe_input()['message']:
        raise ProbeError('native_probe_history_mismatch')
    current = rows[rows.index(users[1]):]
    if (any(row['sessionId'] != expected for row in current)
            or ''.join(_text(row['message']) for row in current if row['type'] == 'assistant') != EXPECTED_MARKER):
        raise ProbeError('native_probe_result_mismatch')
    files = {p.relative_to(root).as_posix() for p in (root / 'config/projects').rglob('*.jsonl')}
    if files != {transcript_relative(expected), transcript_relative(manifest['original_session_id'])}:
        raise ProbeError('extra_native_session_in_probe')
    return {'inherited_context_verified': True, 'observed_session_id': expected,
            'original_transcript_preserved': True, 'transport': transport,
            'native_environment_sha256': sha(environment.encode()) if environment is not None else None,
            'native_environment_evidence': 'original_archived_request',
            'native_target_environment_sha256': sha(target_environment.encode()) if target_environment else None,
            'native_transcript_sha256': file_sha(root / transcript_relative(expected))}


def run(root, binary, token):
    root = Path(root).absolute()
    if (root / 'restore-started.json').exists() or (root / 'restore-started.json').is_symlink():
        raise ProbeError('restore_already_claimed')
    if str(root) != RUNTIME_ROOT or root.is_symlink() or root.resolve() != root or sys.platform != 'linux':
        raise ProbeError('runtime_path_mismatch')
    manifest = read_json(root / 'restore-manifest.json')
    info = verify_source(manifest['source_location'], manifest['source_tree_sha256'])
    ns = load_historical(info['source_location'], info['source_sha256'])
    expected = _session(manifest['operation'], info['session_id'], manifest['expected_session_id'])
    if manifest != _manifest(info, manifest['operation'], expected, manifest['qualification_id']):
        raise ProbeError('prepared_manifest_changed')
    try:
        ns.verify_binary(binary)
        if ns.inventory(root) != manifest['restored_inventory']:
            raise ProbeError('restored_state_changed')
        conf = configuration(root, binary, manifest, ns, token)
    except ns.NativeSessionError as exc:
        raise ProbeError(str(exc)) from None
    input_bytes = json_bytes(probe_input())
    contract = ns.qualification_contract(manifest['qualification_id'], expected)
    # Always claim before native onset. Any interrupted claim permanently forbids replay.
    try:
        write_new(root / 'restore-started.json', json_bytes({'manifest_sha256': sha(json_bytes(manifest)),
            'input_sha256': sha(input_bytes), 'session_id': expected, 'operation': manifest['operation']}))
    except FileExistsError:
        raise ProbeError('restore_already_claimed') from None
    (root / 'capture').rename(root / 'original-capture')
    (root / 'capture').mkdir(mode=0o700)

    class ProbeBoundary(ns.NativeBoundary):
        def release(self, observed_ns):
            if self.release_ns is not None:
                self.fail('duplicate_prompt_release')
            self.release_ns = observed_ns
            self._event('prompt_released', observed_ns, input_sha256=sha(input_bytes))

    boundary = ProbeBoundary(root / 'capture', contract)
    report = {'schema_version': SCHEMA, 'operation': manifest['operation'], 'qualification_id': manifest['qualification_id'],
              'original_session_id': manifest['original_session_id'], 'expected_session_id': expected,
              'historical_source_sha256': info['source_sha256'], 'helper_sha256': manifest['helper_sha256'],
              'source_tree_sha256': info['source_tree_sha256'], 'configured_authentication': 'stdin_to_child_environment_only',
              'automatic_retry': False, 'study_ready': False, 'qualified': False,
              'native_restore_verified': False, 'native_fork_verified': False, 'inherited_context_verified': False,
              'source_unchanged': False, 'included_subscription_allowance_verified': False, 'archive_verified': False,
              'independent_archive_acknowledged': False}
    try:
        if tree_digest(info['source_location']) != info['source_tree_sha256']:
            raise ProbeError('source_tree_changed')
        write_new(root / 'capture/delivered-input.json', input_bytes)
        report['execution'] = ns.supervise(conf['command'], conf['environment'], root / 'work', input_bytes, boundary)
        evidence = inspect_probe(root, manifest, ns)
        report.update(evidence)
        report['included_subscription_allowance_verified'] = ns.subscription_allowance_verified(boundary.usage)
    except (ProbeError, ns.NativeSessionError, OSError) as exc:
        report['error_code'] = str(exc) if isinstance(exc, (ProbeError, ns.NativeSessionError)) else type(exc).__name__
        report['execution'] = boundary.metadata()
    try:
        report['source_unchanged'] = tree_digest(info['source_location']) == info['source_tree_sha256']
    except (ProbeError, OSError):
        report['source_unchanged'] = False
    if not report['source_unchanged']:
        report['error_code'] = 'source_tree_changed'
    if boundary.drain_ns is not None:
        try:
            archive = ns.capture_archive(root, token)
            report['archive_verified'] = archive['verified'] is True
            report['archive_inventory_sha256'] = archive['inventory_sha256']
        except (ns.NativeSessionError, OSError):
            report['archive_error'] = 'capture_held_preserve_original_state'
    else:
        report['archive_error'] = 'owned_work_unresolved_preserve_original_state'
    passed = bool(report.get('execution', {}).get('timing_valid') and report['inherited_context_verified']
                  and report['included_subscription_allowance_verified'] and report['source_unchanged']
                  and report['archive_verified'] and not report.get('error_code'))
    report['qualified'] = passed
    report['native_restore_verified'] = passed and manifest['operation'] == 'continue'
    report['native_fork_verified'] = passed and manifest['operation'] == 'fork'
    write_new(root / 'restore-report.json', json_bytes(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    inspect = commands.add_parser('inspect'); inspect.add_argument('--source', type=Path, required=True)
    prepare_parser = commands.add_parser('prepare')
    prepare_parser.add_argument('--source', type=Path, required=True)
    prepare_parser.add_argument('--source-sha', required=True)
    prepare_parser.add_argument('--root', type=Path, required=True)
    prepare_parser.add_argument('--operation', choices=['continue', 'fork'], required=True)
    prepare_parser.add_argument('--session-id')
    run_parser = commands.add_parser('run')
    run_parser.add_argument('--root', type=Path, required=True)
    run_parser.add_argument('--binary', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == 'inspect':
            result = verify_source(args.source)
            result = {key: value for key, value in result.items() if key != 'archive_inventory'}
        elif args.command == 'prepare':
            result = prepare(args.source, args.root, args.source_sha, args.operation, session_id=args.session_id)
            result = {key: value for key, value in result.items() if key != 'restored_inventory'}
        else:
            token = sys.stdin.readline(32770).rstrip('\r\n')
            if sys.stdin.read(1):
                raise ProbeError('invalid_credential_channel')
            result = run(args.root, args.binary, token)
        print(json.dumps(result, sort_keys=True))
        return 0 if result.get('qualified', True) else 1
    except (ProbeError, OSError, KeyError, TypeError) as exc:
        code = str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        print(json.dumps({'error_code': code, 'qualified': False, 'study_ready': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
