"""Disposable local request-capture probe. Uses a fake token and local fake API only."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

OUT = Path(__file__).resolve().parent
CLAUDE = '/Users/michaelofengenden/miniforge3/bin/claude'
PROMPT = 'How long will it take you to complete this task:\n"Write a haiku about the sea."\nreturn minutes = <Number of minutes>'
SETTINGS = {'disableAllHooks': True, 'disableClaudeAiConnectors': True,
            'autoMemoryEnabled': False, 'includeGitInstructions': False,
            'attribution': {'commit': '', 'pr': ''}}


def run(name, extra):
    case = Path(tempfile.mkdtemp(prefix=name + '-', dir=OUT))
    root = Path(tempfile.mkdtemp(prefix='session-', dir=case))
    dirs = {n: root / n for n in ['home', 'config', 'tmp', 'work']}
    for directory in dirs.values():
        directory.mkdir()
    with (case / 'server.stderr').open('w') as serr:
        server = subprocess.Popen(['/usr/bin/python3', str(OUT / 'fake_anthropic.py'),
                                   str(case / 'requests')], stdout=subprocess.PIPE,
                                  stderr=serr, text=True)
        proc = None
        try:
            port = server.stdout.readline().strip()
            if not port:
                raise RuntimeError((case / 'server.stderr').read_text())
            env = {'HOME': str(dirs['home']), 'CLAUDE_CONFIG_DIR': str(dirs['config']),
                   'TMPDIR': str(dirs['tmp']) + '/',
                   'PATH': str(Path(CLAUDE).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                   'USER': 'forecast-qualification', 'LOGNAME': 'forecast-qualification',
                   'SHELL': '/bin/zsh', 'LANG': 'en_US.UTF-8', 'TERM': 'dumb',
                   'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1',
                   'CLAUDE_CODE_OAUTH_TOKEN': 'fake-oauth-token-for-offline-qualification',
                   'ANTHROPIC_BASE_URL': 'http://127.0.0.1:' + port, **extra}
            cmd = [CLAUDE, '-p', '--model', 'claude-opus-5-5', '--effort', 'max',
                   '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                   '--settings', json.dumps(SETTINGS), '--disable-slash-commands',
                   '--output-format', 'stream-json', '--verbose', '--system-prompt', '']
            proc = subprocess.Popen(cmd, cwd=dirs['work'], env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(PROMPT, timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                stdout, stderr = proc.communicate()
                stderr += '\nPROBE_TIMEOUT'
        finally:
            if proc is not None and proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            server.terminate()
            server.wait()
            server.stdout.close()
    (case / 'stream.jsonl').write_text(stdout)
    (case / 'stderr.txt').write_text(stderr)
    requests = [json.loads(f.read_text()) for f in sorted((case / 'requests').glob('*.json'))]
    messages = [r['body'] for r in requests if r['path'].startswith('/v1/messages')
                and 'count_tokens' not in r['path']]
    body = messages[-1] if messages else {}
    serialized = json.dumps(body)
    events = [json.loads(s) for s in stdout.splitlines() if s.strip().startswith('{')]
    init = next((e for e in events if e.get('subtype') == 'init'), {})
    for f in dirs['config'].glob('projects/*/*.jsonl'):
        shutil.copy2(f, case / ('native-' + f.name))
    report = {'variant': name, 'case': str(case), 'exit_code': proc.returncode,
              'message_requests': len(messages),
              'roles': [m['role'] for m in body.get('messages', [])],
              'environment_note_present': '# Environment' in serialized,
              'macos_present': any(s in serialized.lower() for s in ['darwin', 'macos']),
              'tools': body.get('tools'), 'effort': body.get('output_config'),
              'model': body.get('model'), 'version': init.get('claude_code_version'),
              'stderr_tail': stderr[-500:]}
    (case / 'model-context.json').write_text(json.dumps(
        {'system': body.get('system'), 'messages': body.get('messages')}, indent=2))
    shutil.rmtree(root)
    return report


reports = []
for name, env in [('baseline', {}),
                  ('attachments_disabled', {'CLAUDE_CODE_DISABLE_ATTACHMENTS': '1'})]:
    report = run(name, env)
    reports.append(report)
    print(json.dumps(report), flush=True)
(OUT / 'report.json').write_text(json.dumps({
    'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'offline': True,
    'binary_sha256': hashlib.sha256(Path(CLAUDE).resolve().read_bytes()).hexdigest(),
    'variants': reports}, indent=2))
print('PROBE_DIR', OUT, flush=True)
