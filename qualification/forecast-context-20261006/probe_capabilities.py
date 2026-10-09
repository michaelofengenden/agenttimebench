"""Disposable qualification only. Every model request goes to a local fake API."""
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import tempfile
import zlib

OUT = Path(__file__).resolve().parent
CLAUDE = '/Users/michaelofengenden/miniforge3/bin/claude'
SETTINGS = {'disableAllHooks': True, 'disableClaudeAiConnectors': True,
            'autoMemoryEnabled': False, 'includeGitInstructions': False,
            'attribution': {'commit': '', 'pr': ''}}


def png():
    def chunk(kind, payload):
        data = kind + payload
        return struct.pack('!I', len(payload)) + data + struct.pack('!I', zlib.crc32(data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!IIBBBBB', 200, 200, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\x00' + b'\x00\x80\xff' * 200) * 200))
            + chunk(b'IEND', b''))


def run(name, *, failure=False, bare=False):
    case = Path(tempfile.mkdtemp(prefix=name + '-', dir=OUT))
    root = Path(tempfile.mkdtemp(prefix='afq-', dir='/private/tmp'))
    dirs = {n: root / n for n in ['home', 'config', 'tmp', 'work', 'xdg']}
    for p in dirs.values():
        p.mkdir(mode=0o700)
    image = png()
    user = {'type': 'user', 'message': {'role': 'user', 'content': [
        {'type': 'text', 'text': 'This is a synthetic qualification fixture, not a study task. '
         'How long would it take you to describe the attached image? Return minutes = N.'},
        {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                   'data': base64.b64encode(image).decode()}}]}}
    (case / 'input.json').write_text(json.dumps(user, indent=2))
    proc = None
    stdout = stderr = ''
    with (case / 'server.stderr').open('w') as serr:
        cmd = ['/usr/bin/python3', str(OUT / 'fake_anthropic.py'), str(case / 'requests'),
               '--thinking', '--delay', '0.3']
        if failure:
            cmd += ['--fail-first', '1']
        server = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=serr, text=True)
        try:
            port = server.stdout.readline().strip()
            if not port:
                raise RuntimeError((case / 'server.stderr').read_text())
            env = {'HOME': str(dirs['home']), 'CLAUDE_CONFIG_DIR': str(dirs['config']),
                   'TMPDIR': str(dirs['tmp']) + '/', 'XDG_RUNTIME_DIR': str(dirs['xdg']),
                   'PATH': str(Path(CLAUDE).parent) + ':/usr/bin:/bin:/usr/sbin:/sbin',
                   'USER': 'forecast-qualification', 'LOGNAME': 'forecast-qualification',
                   'SHELL': '/bin/zsh', 'LANG': 'en_US.UTF-8', 'TERM': 'dumb',
                   'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1',
                   'CLAUDE_CODE_DISABLE_ATTACHMENTS': '1', 'CLAUDE_CODE_DISABLE_CLAUDE_MDS': '1',
                   'CLAUDE_CODE_MAX_RETRIES': '0',
                   'CLAUDE_CODE_OAUTH_TOKEN': 'fake-oauth-token-for-offline-qualification',
                   'OTEL_LOG_RAW_API_BODIES': 'file:' + str(case / 'raw-bodies'),
                   'ANTHROPIC_BASE_URL': 'http://127.0.0.1:' + port}
            cmd = [CLAUDE, '-p', '--model', 'claude-opus-5-5', '--effort', 'max',
                   '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                   '--setting-sources', '', '--settings', json.dumps(SETTINGS),
                   '--disable-slash-commands', '--output-format', 'stream-json', '--verbose',
                   '--input-format', 'stream-json', '--system-prompt', '',
                   '--debug-file', str(case / 'debug.log')]
            if bare:
                cmd.append('--bare')
            proc = subprocess.Popen(cmd, cwd=dirs['work'], env=env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
            try:
                stdout, stderr = proc.communicate(json.dumps(user) + '\n', timeout=45)
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
    native = list(dirs['config'].glob('projects/*/*.jsonl'))
    for p in native:
        shutil.copy2(p, case / ('native-' + p.name))
    requests = [json.loads(p.read_text()) for p in sorted((case / 'requests').glob('*.json'))]
    messages = [r['body'] for r in requests if r['path'].startswith('/v1/messages')
                and 'count_tokens' not in r['path']]
    body = messages[-1] if messages else {}
    images = [b for m in body.get('messages', []) if isinstance(m.get('content'), list)
              for b in m['content'] if b.get('type') == 'image']
    raw = list((case / 'raw-bodies').glob('*.json'))
    debug = (case / 'debug.log').read_text() if (case / 'debug.log').exists() else ''
    events = [json.loads(s) for s in stdout.splitlines() if s.strip().startswith('{')]
    init = next((e for e in events if e.get('subtype') == 'init'), {})
    result = next((e for e in reversed(events) if e.get('type') == 'result'), {})
    report = {'variant': name, 'case': str(case), 'exit_code': proc.returncode,
              'message_requests': len(messages), 'request_paths': [r['path'] for r in requests],
              'tools': body.get('tools'), 'effort': body.get('output_config'),
              'model': body.get('model'), 'version': init.get('claude_code_version'),
              'images_received': len(images),
              'image_bytes_identical': len(images) == 1 and base64.b64decode(
                  images[0].get('source', {}).get('data', '')) == image,
              'native_transcripts_saved': len(native), 'raw_body_files': [p.name for p in raw],
              'socket_listening_lines': [s for s in debug.splitlines() if '[uds-messaging] Listening:' in s],
              'result_subtype': result.get('subtype'), 'result_is_error': result.get('is_error'),
              'result_errors': result.get('errors'), 'stderr_tail': stderr[-1200:],
              'environment_note_present': '# Environment' in json.dumps(body),
              'token_budget_reminder_present': '<total_tokens>' in json.dumps(body)}
    (case / 'report.json').write_text(json.dumps(report, indent=2))
    shutil.rmtree(root)
    return report


if __name__ == '__main__':
    os.umask(0o077)
    results = []
    for name, kwargs in [('image_archive_capture', {}), ('retry_zero', {'failure': True}),
                         ('bare_subscription', {'bare': True})]:
        result = run(name, **kwargs)
        results.append(result)
        print(json.dumps(result), flush=True)
    (OUT / 'capabilities-report.json').write_text(json.dumps({
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'offline': True,
        'binary_sha256': hashlib.sha256(Path(CLAUDE).resolve().read_bytes()).hexdigest(),
        'variants': results}, indent=2))
