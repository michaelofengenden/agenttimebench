"""Run one native CLI request and timestamp its stream (Codex app-server or Claude Code).

Offsets are milliseconds from dispatch: the host monotonic clock read just before the prompt
is written to the CLI. Each stdout chunk is stamped when `os.read` returns it. Nothing paces
or rewrites model output, and private reasoning is not observed. A request is cut off at
`backstop_ms`; a Codex live update is sent with turn/steer at its scheduled time.

Initial study (`tools=None`): `codex_command` / `claude_command`, no tools. Tools study
(`tools={'docker', 'image'}`), both arms: `codex_tools_command` / `claude_tools_command`; the
cli_tools arm adds the `shell` MCP tool from tool_service.py. The CLI must have no other MCP
servers configured (the run code disabled any it found). Unlike the run code, a failed
container census or cleanup is recorded in `container_cleanup` but does not fail the attempt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import time

from cli_native.score import score_attempt
from cli_native.tool_service import Container

SERVER, TOOL, CLAUDE_TOOL = 'temporal', 'shell', 'mcp__temporal__shell'
TEXT_DEVELOPER = ('Do not use tools, delegates, network, files, or shell commands. '
                  'Perform only the supplied text exercise.')
BASE_TEXT = ('Perform only the supplied text exercise. Do not delegate or access the network. '
             'Tool results and files do not count as assistant answer text.')
BASE_SVG = ('Perform only the supplied SVG drawing exercise. Create the requested actual '
            'drawings as files in the task workspace. Do not delegate or access the network. '
            'Files are the drawing deliverables; tool results do not count as assistant answer text.')
POLICY = {'no_tools': 'Do not call any tools, including tools for files, shell commands, clock reads, or waits.',
          'cli_tools': 'You may use only the temporal shell tool for commands, files in your task workspace, clock reads, and waits.'}
CLAUDE_ENV = {'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1',
              'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_MAX_RETRIES': '0',
              'CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK': '1'}
KEEP_ENV = {'HOME', 'USER', 'LOGNAME', 'PATH', 'LANG', 'LC_ALL', 'TMPDIR', 'TERM', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR'}


class JsonProcess:
    """A CLI in its own process group, read as newline-delimited JSON with arrival stamps."""

    def __init__(self, command, root, env=None):
        self.root = Path(root)
        (self.root / 'workspace').mkdir(parents=True, exist_ok=True)
        self.raw = (self.root / 'transport.jsonl').open('x')
        self.err = (self.root / 'stderr.txt').open('x')
        self.pending, self.buffer = [], b''
        self.process = subprocess.Popen(command, cwd=self.root / 'workspace', env=env, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=self.err, start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)

    def send(self, message):
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode())
        self.process.stdin.flush()

    def read(self, timeout=0.05):
        if self.pending:
            return self.pending.pop(0)
        if not self.selector.select(timeout):
            return None
        chunk = os.read(self.process.stdout.fileno(), 65536)
        stamp = time.monotonic_ns()
        if not chunk:
            raise EOFError('native stdout closed')
        self.buffer += chunk
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            if line.strip():
                text = line.decode('utf-8')
                self.raw.write(json.dumps({'received_monotonic_ns': stamp, 'raw': text}) + '\n')
                self.pending.append((stamp, json.loads(text)))
        return self.pending.pop(0) if self.pending else None

    def close(self):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.process.pid, sig)
                self.process.wait(timeout=2)
            except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
                pass
        self.selector.close()
        self.raw.close()
        self.err.close()


class CodexEvents:
    def __init__(self, condition=None, model=None):
        self.thread_id = self.turn_id = self.steer_id = self.terminal_state = self.error = None
        self.last_item, self.condition, self.model = None, condition, model

    def consume(self, event, offset):
        if self.steer_id is not None and event.get('id') == self.steer_id:
            if event.get('result', {}).get('turnId') == self.turn_id:
                # Acceptance by the CLI is not evidence that the model received the update.
                return [{'kind': 'intervention_ack', 'offset_ms': offset, 'evidence': 'harness_accepted'}]
            self.error = 'steering_rejected'
            return []
        params, method = event.get('params', {}), event.get('method')
        if method == 'thread/settings/updated' and self.condition:
            settings = params.get('threadSettings', {})
            if settings.get('model') != self.model or settings.get('effort') != 'max':
                self.error = 'native_model_or_effort_mismatch'
        if params.get('threadId') != self.thread_id:
            return []
        turn = params.get('turn', {})
        if method == 'turn/completed' and turn.get('id') == self.turn_id:
            self.terminal_state = 'completed' if turn.get('status') == 'completed' else 'failed'
            return [{'kind': 'terminal', 'offset_ms': offset, 'status': turn.get('status')}]
        if params.get('turnId') != self.turn_id:
            return []
        item = params.get('item', {})
        if method in {'item/started', 'item/completed'} and item.get('type') == 'mcpToolCall':
            if self.condition != 'cli_tools' or item.get('server') != SERVER or item.get('tool') != TOOL:
                self.error = 'unexpected_native_activity'
                return [{'kind': 'model_activity', 'offset_ms': offset}]
            return [{'kind': 'native_tool_start' if method == 'item/started' else 'native_tool_end',
                     'offset_ms': offset, 'call_id': item.get('id')}]
        if method == 'item/agentMessage/delta':
            text, item_id = params.get('delta', ''), params.get('itemId')
            if self.last_item is not None and item_id != self.last_item:
                text = '\n' + text  # distinct assistant messages start on a new line
            self.last_item = item_id
            return [{'kind': 'text', 'offset_ms': offset, 'text': text}]
        if method in {'item/reasoning/textDelta', 'item/reasoning/summaryTextDelta'}:
            return [{'kind': 'reasoning_event', 'offset_ms': offset, 'character_count': len(params.get('delta', ''))}]
        if method == 'item/started' and item.get('type') not in {'agentMessage', 'userMessage', 'reasoning'}:
            self.error = 'unexpected_native_activity'
            return [{'kind': 'model_activity', 'offset_ms': offset}] if self.condition else []
        return []


class ClaudeEvents:
    """Text deltas are concatenated as received: these runs inserted no separator between
    distinct assistant messages."""

    def __init__(self, model, condition=None):
        self.model, self.condition = model, condition
        self.terminal_state = self.error = None

    def consume(self, event, offset):
        kind = event.get('type')
        if kind == 'result':
            if event.get('permission_denials') and self.condition:
                self.error = 'permission_denied'
            self.terminal_state = 'failed' if event.get('is_error') else 'completed'
            return [{'kind': 'terminal', 'offset_ms': offset, 'status': self.terminal_state}]
        if kind == 'system' and event.get('subtype') == 'init':
            servers = event.get('mcp_servers', [])
            if self.condition is None:  # initial study: no tools at all
                if event.get('model') and event['model'] != self.model:
                    self.error = 'observed_model_mismatch'
                if event.get('tools') or servers:
                    self.error = 'tools_not_disabled'
            else:
                if event.get('model') != self.model:
                    self.error = 'observed_model_mismatch'
                if event.get('tools') != ([CLAUDE_TOOL] if self.condition == 'cli_tools' else []) or not (
                        servers == [] if self.condition == 'no_tools' else len(servers) == 1
                        and servers[0].get('name') == SERVER and servers[0].get('status') == 'connected'):
                    self.error = 'tools_inventory_mismatch'
            return [{'kind': 'native_init', 'offset_ms': offset, 'observed_model': event.get('model')}]
        if kind == 'user' and self.condition:
            return [{'kind': 'native_tool_end', 'offset_ms': offset, 'call_id': b.get('tool_use_id')}
                    for b in event.get('message', {}).get('content', [])
                    if isinstance(b, dict) and b.get('type') == 'tool_result']
        if kind != 'stream_event':
            return []
        inner = event.get('event', {})
        delta, block = inner.get('delta', {}), inner.get('content_block', {})
        if inner.get('type') == 'content_block_delta' and delta.get('type') == 'text_delta':
            return [{'kind': 'text', 'offset_ms': offset, 'text': delta.get('text', '')}]
        if inner.get('type') == 'message_start':
            observed = inner.get('message', {}).get('model')
            if observed and observed != self.model:
                self.error = 'observed_model_mismatch'
            return [{'kind': 'model_activity', 'offset_ms': offset, 'observed_model': observed}]
        if inner.get('type') == 'content_block_start' and block.get('type') == 'tool_use':
            if self.condition != 'cli_tools' or block.get('name') != CLAUDE_TOOL:
                self.error = 'tool_use_attempted'
                return [{'kind': 'model_activity', 'offset_ms': offset}]
            return [{'kind': 'native_tool_start', 'offset_ms': offset, 'call_id': block.get('id')}]
        return []


def codex_command(executable):
    """Initial study: text only."""
    return [executable, 'app-server', '--stdio', '-c', 'web_search="disabled"',
            '-c', 'features.shell_tool=false', '-c', 'features.multi_agent=false',
            '-c', 'features.apps=false', '-c', 'features.memories=false']


def claude_command(cell, executable):
    """Initial study: no tools."""
    return [executable, '-p', '--verbose', '--output-format', 'stream-json',
            '--input-format', 'stream-json', '--include-partial-messages',
            '--model', cell['model'], '--effort', cell['effort'],
            '--tools', '', '--safe-mode', '--strict-mcp-config', '--setting-sources', '',
            '--disable-slash-commands', '--no-chrome']


def _toml(value):
    if isinstance(value, dict):
        return '{' + ', '.join(json.dumps(k) + ' = ' + _toml(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(_toml(v) for v in value) + ']'
    return json.dumps(value)


def codex_tools_config(cell, servers):
    servers = {name: dict(entry) for name, entry in servers.items()}
    if cell['tool_condition'] == 'cli_tools' and SERVER in servers:
        servers[SERVER]['tools'] = {TOOL: {'approval_mode': 'approve'}}
    flags = ['shell_tool', 'unified_exec', 'multi_agent', 'apps', 'memories', 'sleep_tool',
             'hooks', 'plugins', 'browser_use', 'computer_use', 'skill_search']
    return {'features': {**dict.fromkeys(flags, False), 'skip_host_skill_discovery': True},
            'mcp_servers': servers, 'web_search': 'disabled', 'project_doc_max_bytes': 0,
            'model_reasoning_effort': 'max', 'approval_policy': 'never', 'sandbox_mode': 'read-only'}


def codex_tools_command(cell, executable, servers, codex_home):
    command = [executable, 'app-server', '--stdio']
    for name, value in codex_tools_config(cell, servers).items():
        command += ['-c', name + '=' + _toml(value)]
    # macOS: hide the user's global Codex instruction files from the CLI.
    deny = ' '.join('(literal ' + json.dumps(str(Path(codex_home) / n)) + ')'
                    for n in ('AGENTS.md', 'AGENTS.override.md'))
    return ['/usr/bin/sandbox-exec', '-p', f'(version 1) (allow default) (deny file-read* {deny})', *command]


def claude_tools_command(cell, executable, servers):
    condition = cell['tool_condition']
    settings = {'disableAllHooks': True, 'enabledPlugins': {}, 'claudeMdExcludes': ['/**'],
                'permissions': {'allow': [CLAUDE_TOOL] if condition == 'cli_tools' else []}}
    command = [executable, '-p', '--verbose', '--output-format', 'stream-json',
               '--input-format', 'stream-json', '--include-partial-messages',
               '--model', cell['model'], '--effort', 'max', '--tools', '', '--restricted',
               '--setting-sources', '', '--strict-mcp-config', '--mcp-config', json.dumps({'mcpServers': servers}),
               '--settings', json.dumps(settings), '--permission-mode', 'dontAsk',
               '--permission-prompts', 'none', '--disable-slash-commands', '--no-chrome',
               '--no-session-persistence', '--system-prompt', base_instructions(cell) + ' ' + POLICY[condition]]
    return command + (['--allowedTools', CLAUDE_TOOL] if condition == 'cli_tools' else [])


def base_instructions(cell):
    return BASE_SVG if cell.get('artifact_profile') == 'crazy_eights_svg_v1' else BASE_TEXT


def rpc(process, identifier, method, params, timeout=30):
    process.send({'id': identifier, 'method': method, 'params': params})
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        row = process.read(0.1)
        if row and row[1].get('id') == identifier:
            if 'error' in row[1]:
                raise RuntimeError('native_rpc_rejected:' + method)
            return row[1].get('result', {})
        if row and 'method' in row[1] and 'id' in row[1]:
            raise RuntimeError('unexpected_native_server_request')
    raise TimeoutError('native_setup_timeout:' + method)


def claude_control(process, identifier, request, timeout=30):
    process.send({'type': 'control_request', 'request_id': identifier, 'request': request})
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        row = process.read(.1)
        if row and row[1].get('type') == 'control_response':
            reply = row[1].get('response', {})
            if reply.get('request_id') == identifier:
                if reply.get('subtype') != 'success':
                    raise RuntimeError('native control setup rejected')
                return reply.get('response', {})
    raise TimeoutError('native control setup timeout')


def codex_start(process, cell, root, tools_servers=None):
    """initialize + thread/start; returns (thread id, thread/start result)."""
    tools = tools_servers is not None
    rpc(process, 1, 'initialize', {'clientInfo': {'name': 'agenttime_temporal_tools' if tools else 'agenttime_temporal_pilot',
                                                  'version': '1' if tools else '1.0'},
                                   'capabilities': {'experimentalApi': True}})
    process.send({'method': 'initialized'})
    params = {'model': cell['model'], 'allowProviderModelFallback': False, 'ephemeral': True,
              'cwd': str((root / 'workspace').resolve()), 'sandbox': 'read-only',
              'approvalPolicy': 'never' if tools else 'untrusted', 'environments': [], 'dynamicTools': []}
    if tools:
        params.update(baseInstructions=base_instructions(cell), developerInstructions=POLICY[cell['tool_condition']],
                      config=codex_tools_config(cell, tools_servers))
    else:
        params.update(developerInstructions=TEXT_DEVELOPER, config={
            'web_search': 'disabled', 'features.shell_tool': False,
            'features.multi_agent': False, 'features.apps': False})
    result = rpc(process, 2, 'thread/start', params)
    if result.get('model') and result['model'] != cell['model']:
        raise RuntimeError('requested_model_changed')
    if tools and (result.get('model') != cell['model'] or result.get('reasoningEffort') != 'max'
                  or result.get('instructionSources')):
        raise RuntimeError('native setup model/effort/instructions mismatch')
    if tools:
        # Dispatch only once the CLI reports exactly the temporal shell (or no server in the no-tools arm).
        expected = {SERVER} if cell['tool_condition'] == 'cli_tools' else set()
        for poll in range(300):
            rows = rpc(process, 10 + poll, 'mcpServerStatus/list', {'threadId': result['thread']['id']}).get('data')
            if {r.get('name') for r in rows} != expected:
                raise RuntimeError('native MCP roster mismatch')
            if not any(r.get('runtimeStatus') == 'starting' for r in rows):
                if any(set(r.get('tools', {})) != {TOOL} for r in rows):
                    raise RuntimeError('native MCP roster mismatch')
                break
            time.sleep(.1)
    return result['thread']['id'], result


def claude_start(process, cell):
    claude_control(process, 'setup', {'subtype': 'initialize', 'hooks': {}})
    for poll in range(300):
        status = claude_control(process, f'mcp-{poll}', {'subtype': 'mcp_status'})
        rows = status.get('mcpServers', status.get('mcp_servers', []))
        if cell['tool_condition'] == 'no_tools':
            if rows:
                raise RuntimeError('native MCP setup mismatch')
            return
        if len(rows) != 1 or rows[0].get('name') != SERVER or rows[0].get('status') not in ('connected', 'pending'):
            raise RuntimeError('native MCP setup mismatch')
        if rows[0]['status'] == 'connected':
            return
        time.sleep(.1)
    raise TimeoutError('native MCP setup timeout')


def launch(cell, root, tools):
    """Start the CLI (and, in a cli_tools arm, the task container); returns (process, servers, container)."""
    executable = shutil.which(cell['system'])
    if tools is None:
        command = codex_command(executable) if cell['system'] == 'codex' else claude_command(cell, executable)
        return JsonProcess(command, root), None, None
    servers, container = {}, None
    if cell['tool_condition'] == 'cli_tools':
        container = Container(tools['docker'], tools['image'], root)
        container.prepare()
        config = root / 'tool-service.json'
        config.write_text(json.dumps({'root': str(root), 'docker': tools['docker'],
                                      'container_id': container.container_id}) + '\n')
        # env -i keeps the CLI's credentials out of the tool service.
        servers = {SERVER: {'command': '/usr/bin/env', 'args': [
            '-i', 'PATH=/usr/bin:/bin', 'HOME=' + str(root), sys.executable, '-I', '-B',
            str(Path(__file__).with_name('tool_service.py')), '--config', str(config)]}}
    env = {k: v for k, v in os.environ.items() if k in KEEP_ENV}
    if cell['system'] == 'codex':
        servers = {name: {**s, 'enabled': True, 'enabled_tools': [TOOL], 'startup_timeout_sec': 30,
                          'tool_timeout_sec': cell['backstop_ms'] / 1000 + 60} for name, s in servers.items()}
        command = codex_tools_command(cell, executable, servers, env.get('CODEX_HOME', str(Path.home() / '.codex')))
    else:
        env.update(CLAUDE_ENV)
        command = claude_tools_command(cell, executable, servers)
    try:
        return JsonProcess(command, root, env), servers, container
    except BaseException:
        if container is not None:
            container.close()
        raise


def run_native(cell, root, emit, tools=None, observer_factory=None):
    """One request. `tools` = {'docker', 'image'} selects the tools-study harness (both arms)."""
    root = Path(root).resolve()
    is_codex = cell['system'] == 'codex'
    outcome = {'kind': 'native', 'state': 'failed', 'model_requested': cell['model']}
    if not is_codex and cell.get('intervention'):
        # In-flight receipt of a live update is not observable through Claude Code.
        return {**outcome, 'state': 'unavailable'}
    container = observer = process = origin = None
    last = 0

    def publish(event):
        nonlocal last
        last = event['offset_ms']
        emit(event)
        if event.get('observed_model'):
            outcome['observed_model'] = event['observed_model']

    try:
        root.mkdir(parents=True, exist_ok=True)
        process, servers, container = launch(cell, root, tools)
        condition = cell.get('tool_condition') if tools is not None else None
        decoder = CodexEvents(condition, cell['model']) if is_codex else ClaudeEvents(cell['model'], condition)
        if is_codex:
            decoder.thread_id, setup = codex_start(process, cell, root, servers)
            outcome['observed_effort'] = setup.get('reasoningEffort')
            if tools is not None:
                outcome['observed_model'] = setup.get('model')
        elif tools is not None:
            claude_start(process, cell)
        if observer_factory is not None:
            observer = observer_factory(container, root)
            observer.start()  # synchronous empty baseline before dispatch
        origin = time.monotonic_ns()
        (root / 'tool-control.json').write_text(json.dumps(
            {'deadline_monotonic_ns': origin + int(cell['backstop_ms'] * 1e6)}) + '\n')
        publish({'kind': 'dispatch', 'offset_ms': 0, 'monotonic_ns': origin})
        if is_codex:
            process.send({'id': 3, 'method': 'turn/start', 'params': {
                'threadId': decoder.thread_id, 'model': cell['model'], 'effort': cell['effort'],
                'input': [{'type': 'text', 'text': cell['prompt']}], 'environments': []}})
        else:
            process.send({'type': 'user', 'message': {'role': 'user', 'content': cell['prompt']}})
            if tools is None:
                process.process.stdin.close()
        intervention, sent = cell.get('intervention'), False
        while True:
            now = (time.monotonic_ns() - origin) / 1e6
            if not process.pending and now >= cell['backstop_ms']:
                publish({'kind': 'cutoff', 'offset_ms': now})
                outcome.update(state='cutoff', reason='administrative_cutoff')
                break
            if not process.pending and intervention and not sent and now >= intervention['at_ms']:
                sent = True
                if not decoder.turn_id:
                    outcome['reason'] = 'active_turn_unavailable_at_intervention'
                    break
                decoder.steer_id = 4
                process.send({'id': 4, 'method': 'turn/steer', 'params': {
                    'threadId': decoder.thread_id, 'expectedTurnId': decoder.turn_id,
                    'input': [{'type': 'text', 'text': intervention['text']}]}})
                publish({'kind': 'intervention_sent', 'offset_ms': now, 'scheduled_ms': intervention['at_ms']})
            row = process.read(0.02)
            if row is None:
                if process.process.poll() is not None:
                    raise RuntimeError('native exit without terminal')
                continue
            stamp, event = row
            offset = (stamp - origin) / 1e6
            # A read can start before the backstop and finish after it; later arrivals are not admitted.
            if offset >= cell['backstop_ms']:
                publish({'kind': 'cutoff', 'offset_ms': max(last, offset)})
                outcome.update(state='cutoff', reason='administrative_cutoff')
                break
            if is_codex:
                if event.get('id') == 3:
                    if 'error' in event:
                        raise RuntimeError('turn_start_rejected')
                    decoder.turn_id = event.get('result', {}).get('turn', {}).get('id')
                if event.get('method') == 'turn/started' and event.get('params', {}).get('threadId') == decoder.thread_id:
                    decoder.turn_id = event['params'].get('turn', {}).get('id')
                if 'method' in event and 'id' in event:
                    process.send({'id': event['id'], 'error': {'code': -32000, 'message': 'Interactive permissions disabled'}})
                    raise RuntimeError('unexpected_native_server_request')
            for observation in decoder.consume(event, offset):
                publish(observation)
            if decoder.error:
                raise RuntimeError(decoder.error)
            if decoder.terminal_state:
                outcome.update(state=decoder.terminal_state, native_terminal_ms=offset)
                break
    except Exception as exc:
        outcome.update(state='failed', reason=str(exc)[:200])
    finally:
        if observer is not None:
            observer.stop()
        if process is not None:
            process.close()
        if container is not None:
            outcome['container_cleanup'] = container.close()
        if origin is not None:
            outcome['elapsed_ms'] = max(last, (time.monotonic_ns() - origin) / 1e6)
        journal = root / 'tool-events.jsonl'
        if tools is not None:
            outcome['tool_calls'] = sum(json.loads(line)['kind'] == 'tool_start'
                                        for line in journal.read_text().splitlines()) if journal.exists() else 0
        if observer is not None and origin is not None:
            outcome['artifact_observation'] = observer.summary(origin, outcome)
    return outcome


def run_cell(cell, root, tools=None, observer_factory=None):
    """Write request.txt and events.jsonl, run the request, and save receipt.json (scored when
    the cell comes from cells.py). Returns the receipt and the events."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)  # no replay: one folder per attempt
    (root / 'request.txt').write_text(cell['prompt'], encoding='utf-8')
    events, started = [], False
    with (root / 'events.jsonl').open('x') as stream:
        def emit(event):
            nonlocal started
            events.append(event)
            stream.write(json.dumps(event, ensure_ascii=False) + '\n')
            stream.flush()
            # The attempt counts as started once the model shows any activity.
            started |= event['kind'] in {'text', 'reasoning_event', 'model_activity'} or (
                tools is not None and cell.get('tool_condition') == 'cli_tools' and event['kind'] == 'native_tool_start')
        outcome = run_native(cell, root, emit, tools, observer_factory)
    outcome['model_attempt_started'] = started
    receipt = {**outcome, 'cell_id': cell['id']}
    if 'measurement' in cell:
        receipt['score'] = score_attempt(cell, events, outcome)
    (root / 'receipt.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
    return receipt, events
