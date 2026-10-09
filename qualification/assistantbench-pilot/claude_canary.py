"""Fixed synthetic Claude/MCP qualification. Default is preparation, never a model call.

The explicit run command accepts a subscription token only on stdin. The caller
owns Docker isolation, independent archive acknowledgement and final container
stop proof. No selected task, scorer, gold answer or credential file is loaded.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid

import agenttime.assistantbench_bridge as bridge_module
from agenttime import native_session
from agenttime.assistantbench_bridge import NATIVE_ACTIONS

CLI_SHA256 = '1e08503dbdf3c2cb0d706d32f3408277388d1c76ef108673e8fe42c1b322925b'
CLI_VERSION = '2.1.280'
MODEL = 'claude-opus-5-5'
PROMPT = ('This is a synthetic browser infrastructure check. Use only the AssistantBench browser tools. '
          'Observe the local page, enter blue in the Synthetic value field, click Store value, '
          'verify that the page says Observed: blue, and submit exactly Synthetic answer: blue. '
          'through send_msg_to_user. Then finish. Do not navigate away from the local page.')
URL = 'http://127.0.0.1:8765/synthetic.html'
TOOLS = tuple('mcp__assistantbench__' + name for name in ('observe', *NATIVE_ACTIONS))


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def write_new(path, data):
    data = data if isinstance(data, bytes) else encoded(data)
    with Path(path).open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())
    Path(path).chmod(0o600)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_pins():
    module_root = Path(bridge_module.__file__).parent
    paths = [Path(__file__), Path(__file__).with_name('synthetic.html'),
             module_root / 'assistantbench_bridge.py', module_root / 'jsonio.py', module_root / 'evidence.py',
             module_root / 'native_session.py']
    return {p.name: sha(p) for p in paths}


def prepare(root):
    root = Path(root).absolute(); root.mkdir(parents=True, mode=0o700, exist_ok=False)
    for name in ('home', 'config', 'tmp', 'xdg', 'work', 'capture', 'browser-home', 'browser-tmp'):
        (root / name).mkdir(mode=0o700)
    session_id = str(uuid.uuid4())
    write_new(root / 'goal.txt', PROMPT.encode())
    write_new(root / 'input.json', (json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': PROMPT}]}}, sort_keys=True) + '\n').encode())
    write_new(root / 'settings.json', {'disableAllHooks': True, 'disableClaudeAiConnectors': True,
        'autoMemoryEnabled': False, 'includeGitInstructions': False, 'attribution': {'commit': '', 'pr': ''}})
    # env -i prevents the OAuth token inherited by Claude reaching Python/browser.
    module_root = str(Path(bridge_module.__file__).parent.parent)
    browser_env = ['PATH=/usr/local/bin:/usr/bin:/bin', 'HOME=' + str(root / 'browser-home'),
        'TMPDIR=' + str(root / 'browser-tmp'), 'PYTHONPATH=' + module_root,
        'PYTHONDONTWRITEBYTECODE=1', 'PLAYWRIGHT_BROWSERS_PATH=/opt/playwright', 'LANG=C.UTF-8']
    mcp = {'mcpServers': {'assistantbench': {'command': '/usr/bin/env',
        'args': ['-i', *browser_env, sys.executable, '-m', 'agenttime.assistantbench_bridge',
                 '--goal-file', str(root / 'goal.txt'), '--state-dir', str(root / 'browser-state'),
                 '--attempt-id', session_id, '--qualification-start-url', URL]}}}
    write_new(root / 'mcp.json', mcp)
    contract = {'schema_version': 'agenttime.synthetic-assistantbench-canary.v1',
        'session_id': session_id, 'purpose': 'synthetic_only', 'study_inputs': 0,
        'model': MODEL + '[1m]', 'effort': 'max', 'cli_version': CLI_VERSION,
        'cli_sha256': CLI_SHA256, 'tools': list(TOOLS), 'source_pins': source_pins(),
        'files': {name: sha(root / name) for name in ('goal.txt', 'input.json', 'settings.json', 'mcp.json')}}
    write_new(root / 'contract.json', contract)
    return {'prepared': True, 'model_calls': 0, 'study_attempts': 0,
        'contract_sha256': sha(root / 'contract.json'), 'session_id': session_id}


def verify(root):
    root = Path(root).absolute(); contract_bytes = (root / 'contract.json').read_bytes()
    contract = json.loads(contract_bytes)
    if contract.get('schema_version') != 'agenttime.synthetic-assistantbench-canary.v1':
        raise ValueError('Synthetic contract required')
    if (contract.get('model') != MODEL + '[1m]' or contract.get('effort') != 'max'
            or contract.get('cli_sha256') != CLI_SHA256 or contract.get('tools') != list(TOOLS)
            or contract.get('source_pins') != source_pins()):
        raise ValueError('Synthetic source, model or tool pin mismatch')
    if set(contract.get('files', {})) != {'goal.txt', 'input.json', 'settings.json', 'mcp.json'}:
        raise ValueError('Synthetic input manifest mismatch')
    contents = {name: (root / name).read_bytes() for name in contract['files']}
    if any(hashlib.sha256(contents[name]).hexdigest() != digest for name, digest in contract['files'].items()):
        raise ValueError('Synthetic input changed after preparation')
    input_value = json.loads(contents['input.json'])
    if input_value != {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': PROMPT}]}}:
        raise ValueError('Only the fixed synthetic prompt is permitted')
    if str(uuid.UUID(contract['session_id'])) != contract['session_id']:
        raise ValueError('Invalid synthetic session ID')
    return contract, contents['input.json'], hashlib.sha256(contract_bytes).hexdigest()


def configuration(root, binary, token):
    root = Path(root).absolute(); contract, _, _ = verify(root)
    return _configuration(root, binary, token, contract)


def _configuration(root, binary, token, contract):
    if not isinstance(token, str) or not 12 <= len(token) <= 32768 or any(c in token for c in '\r\n\0'):
        raise ValueError('Invalid stdin-only subscription token')
    command = [str(binary), '-p', '--model', MODEL + '[1m]', '--effort', 'max',
        '--session-id', contract['session_id'], '--tools', '', '--allowedTools', ','.join(TOOLS),
        '--permission-mode', 'dontAsk', '--strict-mcp-config', '--mcp-config', str(root / 'mcp.json'),
        '--setting-sources', '', '--settings', str(root / 'settings.json'), '--disable-slash-commands',
        '--output-format', 'stream-json', '--input-format', 'stream-json', '--verbose',
        '--debug-file', str(root / 'capture/debug.log')]
    env = {'HOME': str(root / 'home'), 'CLAUDE_CONFIG_DIR': str(root / 'config'),
        'TMPDIR': str(root / 'tmp') + '/', 'XDG_RUNTIME_DIR': str(root / 'xdg'),
        'PATH': '/usr/local/bin:/usr/bin:/bin', 'USER': 'agenttime', 'LOGNAME': 'agenttime',
        'SHELL': '/bin/sh', 'LANG': 'C.UTF-8', 'TERM': 'dumb', 'CLAUDE_CODE_OAUTH_TOKEN': token,
        'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1',
        'CLAUDE_CODE_DISABLE_ATTACHMENTS': '1', 'CLAUDE_CODE_DISABLE_CLAUDE_MDS': '1',
        'CLAUDE_CODE_MAX_RETRIES': '0', 'CLAUDE_CODE_NO_MODEL_FALLBACK': '1',
        'CLAUDE_CODE_DISABLE_NONSTREAMING_FALLBACK': '1', 'CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS': '0',
        'ENABLE_TOOL_SEARCH': 'false', 'OTEL_LOG_RAW_API_BODIES': 'file:' + str(root / 'capture/raw-bodies')}
    return command, env


def mcp_startup_verified(servers):
    """Accept the pinned CLI's observed dynamic-source annotation only."""
    expected = {'name': 'assistantbench', 'status': 'connected'}
    return servers == [expected] or servers == [{**expected, 'source': 'dynamic'}]


class CanaryBoundary(native_session.NativeBoundary):
    """Synthetic-only evidence using the qualified native process supervisor."""

    def __init__(self, root, contract, watchdog_seconds):
        if (contract.get('purpose') != 'synthetic_only' or contract.get('study_inputs') != 0
                or contract.get('tools') != list(TOOLS) or contract.get('model') != MODEL + '[1m]'
                or contract.get('effort') != 'max'):
            raise ValueError('A fixed synthetic-only browser contract is required')
        if type(watchdog_seconds) not in (int, float) or not 0 < watchdog_seconds < float('inf'):
            raise ValueError('The synthetic watchdog must be positive and finite')
        self.root = Path(root); self.capture = self.root / 'capture'
        self.contract = {'session_id': contract['session_id'], 'tools': list(TOOLS),
            'input': {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': PROMPT}]}}}
        self.input_sha256 = contract['files']['input.json']
        self.issues = []; self.usage = []; self.initialized = False
        self.release_ns = self.result_ns = self.root_exit_ns = self.drain_ns = self.terminal_ns = None
        self.exit_code = None; self.answer_sha256 = None; self.cancelled = False
        self._sequence = 0; self._last_event_ns = None; self.init_evidence = None
        self.watchdog_seconds = watchdog_seconds; self.synthetic_watchdog_expired = False
        self._watchdog_stop = threading.Event(); self._watchdog_thread = None

    def _event(self, kind, observed_ns, **details):
        super()._event(kind, observed_ns, **details)
        if kind == 'root_process_started':
            write_new(self.root / 'process.json', {'pid': details['pid'], 'pgid': details['pid'],
                'owner_pid': details['owner_pid'], 'linux_child_subreaper_enabled': True,
                'synthetic_test_watchdog_seconds': self.watchdog_seconds, 'not_a_study_duration': True})

    def release(self, observed_ns):
        if self.release_ns is not None: self.fail('duplicate_prompt_release')
        self.release_ns = observed_ns
        self._event('prompt_released', observed_ns, input_sha256=self.input_sha256)
        def watchdog():
            if self._watchdog_stop.wait(self.watchdog_seconds) or self.drain_ns is not None: return
            self.synthetic_watchdog_expired = True; self.cancelled = True
            if 'synthetic_watchdog_expired' not in self.issues: self.issues.append('synthetic_watchdog_expired')
            requested_at = time.monotonic()
            # Signal only this dedicated supervisor's observed descendants,
            # including adopted setsid children. Never signal the entry itself.
            while not self._watchdog_stop.is_set() and self.drain_ns is None:
                native_session._signal_owned(native_session._owned_processes(),
                    signal.SIGKILL if time.monotonic() - requested_at >= 5 else signal.SIGTERM)
                self._watchdog_stop.wait(.02)
        self._watchdog_thread = threading.Thread(target=watchdog, name='synthetic-only-watchdog', daemon=True)
        self._watchdog_thread.start()

    def stop_watchdog(self):
        self._watchdog_stop.set()
        if self._watchdog_thread is not None: self._watchdog_thread.join()

    def observe(self, event, observed_ns):
        if not isinstance(event, dict): self.fail('malformed_native_event')
        if event.get('type') != 'system' or event.get('subtype') != 'init':
            return super().observe(event, observed_ns)
        self._event('native_event', observed_ns, metadata=native_session.event_metadata(event))
        if self.initialized: self.fail('duplicate_native_init')
        if (event.get('session_id') != self.contract['session_id'] or event.get('model') not in (MODEL, MODEL + '[1m]')
                or event.get('claude_code_version') != CLI_VERSION): self.fail('native_identity_mismatch')
        tools = event.get('tools')
        if not isinstance(tools, list) or any(not isinstance(t, str) for t in tools) or sorted(tools) != sorted(TOOLS):
            self.fail('native_capability_mismatch')
        if not mcp_startup_verified(event.get('mcp_servers')): self.fail('mcp_startup_unverified')
        if any(event.get(key) != [] for key in ('skills', 'plugins')): self.fail('unexpected_extension')
        self.initialized = True
        self.init_evidence = {key: event[key] for key in ('model', 'claude_code_version', 'tools', 'mcp_servers', 'skills', 'plugins')}

    def drain(self, observed_ns, *, census_empty, waitpid_echild):
        super().drain(observed_ns, census_empty=census_empty, waitpid_echild=waitpid_echild)
        if self.drain_ns is not None:
            write_new(self.capture / 'process-drain.json', {'session_id': self.contract['session_id'],
                'method': 'linux_child_subreaper_waitpid_and_owned_census', 'census_empty': census_empty,
                'waitpid_echild': waitpid_echild, 'root_exit_monotonic_ns': self.root_exit_ns,
                'root_exit_code': self.exit_code, 'owned_work_drained_monotonic_ns': self.drain_ns})


def supervise_process(root, contract, command, environment, input_bytes, watchdog_seconds):
    """Disposable canary only; adoption, reaping and census must all complete."""
    if hashlib.sha256(input_bytes).hexdigest() != contract['files']['input.json']:
        raise ValueError('The verified synthetic input bytes changed')
    boundary = CanaryBoundary(root, contract, watchdog_seconds)
    try:
        native_session.supervise(command, environment, Path(root) / 'work', input_bytes, boundary)
    except (ValueError, OSError, subprocess.SubprocessError):
        if 'synthetic_supervision_failed' not in boundary.issues: boundary.issues.append('synthetic_supervision_failed')
    finally:
        boundary.stop_watchdog()
    return {'execution': boundary.metadata(), 'cli_exit_code': boundary.exit_code,
        'owned_processes_drained': boundary.drain_ns is not None,
        'synthetic_watchdog_expired': boundary.synthetic_watchdog_expired}


def inspect_capture(root):
    """Return metadata only. Keep the original stream and transport bodies private."""
    root = Path(root); contract, frozen_input, _ = verify(root); issues = []; seen = set(); init_count = 0; result_count = 0
    def issue(code):
        if code not in issues: issues.append(code)
    stream_bytes = (root / 'capture/stream.jsonl').read_bytes()
    rows = [json.loads(line) for line in stream_bytes.splitlines() if line.strip()]
    results = [row for row in rows if row.get('type') == 'result']
    complete_stream = (stream_bytes.endswith(b'\n') and len(results) == 1
        and results[0].get('session_id') == contract['session_id']
        and results[0].get('subtype') == 'success' and results[0].get('is_error') is False)
    native_ids = {row['message']['id'] for row in rows
        if complete_stream and row.get('type') == 'assistant' and row.get('session_id') == contract['session_id']
        and isinstance(row.get('message'), dict) and row['message'].get('type') == 'message'
        and row['message'].get('model') == MODEL and isinstance(row['message'].get('id'), str)}
    native_fallback = []
    usage = []
    for row in rows:
        if row.get('type') == 'system' and row.get('subtype') == 'init':
            init_count += 1
            if (row.get('model') not in (MODEL, MODEL + '[1m]') or row.get('claude_code_version') != CLI_VERSION
                    or sorted(row.get('tools', [])) != sorted(TOOLS) or row.get('session_id') != contract['session_id']
                    or row.get('skills') or row.get('plugins')): issue('native_startup_mismatch')
            servers = row.get('mcp_servers', [])
            if not mcp_startup_verified(servers): issue('mcp_startup_unverified')
        if row.get('type') == 'result':
            result_count += 1
            if row.get('is_error') or row.get('subtype') != 'success': issue('native_result_unsuccessful')
        message = row.get('message', {})
        if isinstance(message, dict):
            if message.get('model') not in (None, MODEL): issue('response_model_mismatch')
            for block in message.get('content', []) if isinstance(message.get('content'), list) else []:
                if isinstance(block, dict) and block.get('type') == 'tool_use':
                    name = block.get('name'); seen.add(name)
                    if name not in TOOLS: issue('unapproved_tool')
        if isinstance(row.get('rate_limit_info'), dict):
            info = row['rate_limit_info']; usage.append({k: info[k] for k in (
                'status', 'overageStatus', 'isUsingOverage', 'hasExtraUsage') if k in info})
    if init_count != 1 or result_count != 1: issue('native_boundary_missing_or_repeated')
    for name in ('observe', 'fill', 'click', 'send_msg_to_user'):
        if 'mcp__assistantbench__' + name not in seen: issue('required_browser_path_unobserved')
    raw = root / 'capture/raw-bodies'; request_count = 0
    try:
        records = [json.loads(line) for line in (raw / 'index.jsonl').read_text().splitlines() if line.strip()]
        referenced = set()
        for record in records:
            name = record.get('request_file')
            if not isinstance(name, str) or Path(name).name != name or name in referenced:
                issue('transport_reference_invalid'); continue
            referenced.add(name); request_count += 1
            body = json.loads((raw / name).read_text())
            if body.get('model') != MODEL or record.get('model') != MODEL: issue('transport_model_mismatch')
            if body.get('output_config', {}).get('effort') != 'max': issue('transport_effort_mismatch')
            if body.get('fallbacks') not in (None, []): issue('transport_fallback_configured')
            names = {tool.get('name') for tool in body.get('tools', []) if isinstance(tool, dict)}
            if names != set(TOOLS): issue('transport_tool_policy_mismatch')
            if not record.get('request_id'): issue('transport_acceptance_missing')
            if record.get('session_id') != contract['session_id']: issue('transport_session_mismatch')
            if request_count == 1:
                messages = body.get('messages', [])
                if not messages or messages[0] != json.loads(frozen_input)['message']:
                    issue('transport_input_mismatch')
            response_name = record.get('response_file')
            if not isinstance(response_name, str) or Path(response_name).name != response_name:
                issue('transport_response_mismatch')
            else:
                try:
                    response = json.loads((raw / response_name).read_text())
                except (OSError, ValueError):
                    message_id = record.get('message_id')
                    if (record.get('query_source', 'sdk') == 'sdk'
                            and record.get('session_id') == contract['session_id'] and message_id in native_ids):
                        native_fallback.append(message_id)
                    else: issue('transport_response_mismatch')
                else:
                    if (response.get('type') != 'message' or response.get('model') != MODEL
                            or not response.get('id') or response.get('id') != record.get('message_id')):
                        issue('transport_response_mismatch')
        if referenced != {p.name for p in raw.glob('*.request.json')}:
            issue('transport_unindexed_request')
        if request_count == 0: issue('transport_missing')
    except (OSError, ValueError, TypeError): issue('transport_unavailable')
    answer = root / 'browser-state/final-answer.txt'
    if not answer.is_file() or answer.read_bytes() != b'Synthetic answer: blue.': issue('native_submission_unconfirmed')
    try:
        snapshot_path = root / 'browser-state/browser-state.json'
        snapshot = json.loads(snapshot_path.read_text())
        receipt = json.loads((root / 'browser-state/browser-state-receipt.json').read_text())
        origins = snapshot.get('storage_state', {}).get('origins', [])
        changed = any(origin.get('origin') == 'http://127.0.0.1:8765' and
            {'name': 'bridge-probe', 'value': 'blue'} in origin.get('localStorage', []) for origin in origins)
        if receipt.get('sha256') != sha(snapshot_path) or not changed:
            issue('browser_state_integrity_or_action_unverified')
    except (OSError, ValueError, TypeError): issue('browser_state_integrity_or_action_unverified')
    originals = list((root / 'config').glob('projects/**/' + contract['session_id'] + '.jsonl'))
    if not originals: issue('original_native_transcript_missing')
    allowance = bool(usage and all(info.get('isUsingOverage') is False and info.get('hasExtraUsage') in (None, False)
        and info.get('overageStatus') == 'rejected' for info in usage) and any(info.get('status') == 'allowed' for info in usage))
    if not allowance: issue('included_subscription_allowance_unverified')
    return {'issues': issues, 'observed_tools': sorted(seen), 'native_session_files': len(originals),
        'transport_request_count': request_count, 'native_stream_response_ids': native_fallback,
        'included_subscription_allowance_verified': allowance}


def run(root, binary, token, acknowledgement, watchdog_seconds=300):
    """Only the root operator may invoke this explicit disposable model canary."""
    if sys.platform != 'linux': raise ValueError('The synthetic canary requires the qualified Linux image')
    root = Path(root).absolute(); binary = Path(binary)
    contract, input_bytes, contract_sha = verify(root)
    if sha(binary) != CLI_SHA256: raise ValueError('Pinned Linux Claude binary required')
    ack = json.loads(Path(acknowledgement).read_text())
    if (ack.get('contract_sha256') != contract_sha or ack.get('archive_verified') is not True
            or not isinstance(ack.get('archive_location'), str) or not ack['archive_location']):
        raise ValueError('Independent pre-run archive acknowledgement required')
    command, env = _configuration(root, binary, token, contract)
    write_new(root / 'run-claimed.json', {'session_id': contract['session_id'], 'purpose': 'synthetic_only',
        'at_monotonic_ns': time.monotonic_ns()})
    html = Path(__file__).with_name('synthetic.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/synthetic.html': self.send_error(404); return
            self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html))); self.end_headers(); self.wfile.write(html)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 8765), Handler)
    serving = threading.Thread(target=server.serve_forever, daemon=True); serving.start()
    try:
        write_new(root / 'capture/delivered-input.json', input_bytes)
        supervision = supervise_process(root, contract, command, env, input_bytes, watchdog_seconds)
        drained = supervision['owned_processes_drained']; code = supervision['cli_exit_code']
        # Private native stores stay intact. A credential finding prevents a public receipt.
        for path in root.rglob('*'):
            if path.is_file() and not path.is_symlink() and token.encode() in path.read_bytes():
                raise ValueError('Credential appeared in capture; retain private container for review')
        report = inspect_capture(root)
        for issue in supervision['execution']['issues']:
            if issue not in report['issues']: report['issues'].append(issue)
        if supervision['synthetic_watchdog_expired'] and 'synthetic_watchdog_expired' not in report['issues']:
            report['issues'].append('synthetic_watchdog_expired')
        if code != 0: report['issues'].append('cli_exit_failure')
        if not drained: report['issues'].append('owned_process_drain_unproven')
        report.update({'schema_version': 1, 'qualification': 'synthetic_claude_mcp_only',
            'study_attempts': 0, 'study_ready': False, 'full_browser_restore_qualified': False,
            'synthetic_tool_path_passed': not report['issues'], 'cli_exit_code': code,
            'process_group_drained': drained, 'owned_processes_drained': drained,
            'drain_method': 'linux_child_subreaper_waitpid_and_owned_census',
            'execution': supervision['execution'], 'source_pins': source_pins(),
            'requires_independent_final_archive_and_container_stop_proof': True})
        write_new(root / 'result.json', report)
        return report
    finally:
        server.shutdown(); server.server_close(); serving.join()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'run'), nargs='?', default='prepare')
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--acknowledgement', type=Path)
    parser.add_argument('--synthetic-watchdog-seconds', type=int, default=300)
    args = parser.parse_args(); os.umask(0o077)
    if args.command == 'prepare': result = prepare(args.root)
    else:
        if args.binary is None or args.acknowledgement is None: parser.error('run requires binary and acknowledgement')
        if args.synthetic_watchdog_seconds <= 0: parser.error('synthetic watchdog must be positive')
        token = sys.stdin.readline(32770).rstrip('\n')
        result = run(args.root, args.binary, token, args.acknowledgement, args.synthetic_watchdog_seconds)
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
