"""Run one GPQA/HLE question cell of the harness swap through OpenRouter, from duration_following/:

    python -m harness_swap.run_question_cell codex CELL --prompt P.txt --out DIR --image IMAGE --env-file E \\
        --codex-bin codex-x86_64-unknown-linux-musl --models-cache models_cache.json [--model astra]
    python -m harness_swap.run_question_cell claude-code CELL --prompt P.txt --out DIR --image IMAGE --env-file E \\
        --static DIR_WITH_CLAUDE_BINARY [--model fable] [--log-port 18801]

codex: Codex CLI 0.153.4 with Fable 5.1 (Astra for the provider check), set up as the main runs' GPT-5.6 Sol clock route
(agenttime.harness) with the model and provider changed; E holds OPENROUTER_API_KEY. claude-code: Claude Code 2.1.259
with GPT-6 Astra (Fable for the provider check), the main runs' closed-book clock flags, the endpoint and model changed
and the prompt piped from a file; E holds ANTHROPIC_AUTH_TOKEN (the OpenRouter key); --log-port routes the calls
unchanged through openrouter_proxy.py for its timing ledger, as in all of Astra's runs. The prompt (task text, two
newlines, the duration sentence) must match prompt_sha256 in cells.csv. The run is timed on the host with a monotonic
clock around `docker exec`; at max(2 x request, 30 min) the CLI gets SIGTERM and the container is killed 15 s later.
IMAGE is the main runs' GPQA/HLE task image (user 1000:1000, /workspace, /usr/local/bin/python3). Writes timing.json,
events.jsonl (the CLI's JSON stream) and stderr.log into DIR.
"""
import argparse
import csv
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from string import Template

from agenttime import benchmarks, clock_mcp, harness

HERE = Path(__file__).resolve().parent
# Codex model catalog: the main runs' clock-route entry for this model with these fields changed. OpenRouter does not
# accept OpenAI's lite framing, and it maps `verbosity` onto Anthropic's effort: Sol's default (low) would have run
# Fable at low effort.
CATALOG = {'fable': ('gpt-5.6-sol', {'slug': 'anthropic/claude-fable-5.1', 'display_name': 'Fable 5.1 via OpenRouter',
                                     'use_responses_lite': False, 'support_verbosity': False},
                     'fable-clock-model-catalog.json'),
           'astra': ('gpt-6-astra', {'slug': 'openai/gpt-6-astra', 'display_name': 'GPT-6 Astra (OpenRouter)',
                                     'use_responses_lite': False}, 'astra-or-clock-model-catalog.json')}
CLAUDE_MODELS = {'astra': 'openai/gpt-6-astra', 'fable': 'anthropic/claude-fable-5.1'}
STATIC = '/opt/agenttime-cc'           # in the container: claude, clock_tool.py, clock-mcp.json, settings (read-only)
CELL = '/opt/agenttime-cc-cell'        # in the container: prompt.txt, system.txt (read-only)
HOME = '/opt/agenttime-cc-home'        # clean home
LOGS = '/logs/agent'


def sh(args):
    return subprocess.run(args, capture_output=True, text=True)


def backstop_s(requested_s):
    return max(2 * requested_s, 1800)


def load_cell(name, prompt_path, cells_csv=HERE / 'cells.csv'):
    cell = next(c for c in csv.DictReader(open(cells_csv)) if c['cell'] == name)
    prompt = Path(prompt_path).read_bytes()
    if cell['block'] != 'questions' or hashlib.sha256(prompt).hexdigest() != cell['prompt_sha256']:
        sys.exit('%s: not a question cell, or the prompt differs from the one that ran' % name)
    assert backstop_s(float(cell['requested_s'])) == float(cell['backstop_s'])
    return cell, prompt.decode('utf-8')


def start_proxy(port, ledger, client, cache_hint=False):
    proxy = subprocess.Popen([sys.executable, str(HERE / 'openrouter_proxy.py')], stdin=subprocess.DEVNULL,
                             env=dict(os.environ, PROXY_PORT=str(port), PROXY_LEDGER=str(ledger), PROXY_CLIENT=client,
                                      PROXY_CACHE_HINT='1' if cache_hint else '0'))
    for _ in range(75):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=1).close()
            return proxy
        except OSError:
            time.sleep(0.2)
    proxy.kill()
    sys.exit('proxy did not start')


def timed_exec(container, argv, backstop, kill_pattern, out):
    """Run argv in the container, timed from outside; ending 'own', or 'cutoff' at the backstop."""
    with open(out / 'events.jsonl', 'w') as events, open(out / 'stderr.log', 'w') as errlog:
        t0 = time.monotonic_ns()
        proc = subprocess.Popen(['docker', 'exec', '-i', container, *argv], stdin=subprocess.DEVNULL,
                                stdout=events, stderr=errlog)
        ending = 'own'
        try:
            proc.wait(timeout=backstop)
        except subprocess.TimeoutExpired:
            ending = 'cutoff'
            sh(['docker', 'exec', container, 'pkill', '-TERM', '-f', kill_pattern])
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                sh(['docker', 'kill', container])
                proc.wait()
        t1 = time.monotonic_ns()
    return {'elapsed_s': (t1 - t0) / 1e9, 'exit_code': proc.returncode, 'ending': ending}


def run_in_container(a, name, options, argv, kill_pattern, timing):
    """Start the task container (2 CPUs, 4 GB, host network, user 1000:1000), time argv in it, remove it."""
    sh(['docker', 'rm', '-f', name])
    r = sh(['docker', 'run', '-d', '--name', name, '--network', 'host', '--user', '1000:1000', '--workdir',
            '/workspace', '--cpus', '2', '--memory', '4096m', '--env-file', a.env_file, *options,
            '--entrypoint', 'sleep', a.image, 'infinity'])
    timing.update(timed_exec(name, argv, timing['backstop_s'], kill_pattern, a.out) if r.returncode == 0
                  else {'ending': 'setup_error', 'error': r.stderr[-500:]})
    sh(['docker', 'rm', '-f', name])


# ---- Codex
def codex_catalog(models_cache, model):
    source, changes, _ = CATALOG[model]
    entry = json.loads(harness.codex_clock_catalog(models_cache, source))['models'][0]
    return json.dumps({'models': [{**entry, **changes}]}, separators=(',', ':'), sort_keys=True)


def codex_config(model, port, hle):
    dev = 'developer_instructions = """%s"""\n' % benchmarks.HLE_SYSTEM_PROMPT if hle else ''
    return Template((HERE / 'codex-config.toml').read_text()).substitute(
        model=CATALOG[model][1]['slug'], provider='openrouter_cached' if model == 'fable' else 'openrouter',
        catalog=CATALOG[model][2], port=port, developer_instructions=dev,
        disabled_features=''.join('%s = false\n' % f for f in harness.CLOCK_DISABLED_FEATURES))


def codex_argv(slug, prompt):
    return ['codex', 'exec', '--dangerously-bypass-approvals-and-sandbox', '--skip-git-repo-check', '--model', slug,
            '--json', '--enable', 'unified_exec', '-c', 'model_reasoning_effort=max', '--strict-config',
            '--ignore-rules', '--', prompt]


def run_codex(a, cell, prompt, timing):
    home = a.out / 'codex-home'
    home.mkdir(exist_ok=True)
    (home / 'config.toml').write_text(codex_config(a.model, a.port, cell['family'] == 'humanitys-last-exam'))
    (home / CATALOG[a.model][2]).write_text(codex_catalog(json.load(open(a.models_cache)), a.model))
    timing['model'] = slug = CATALOG[a.model][1]['slug']
    proxy = start_proxy(a.port, a.out / 'proxy-ledger.jsonl', 'codex', cache_hint=a.model == 'fable')
    sh(['chown', '-R', '1000:1000', str(home)])
    run_in_container(a, 'swap-codex-' + a.cell.lower(), [
        '-e', 'CODEX_HOME=/home/agent/.codex', '-e', 'HOME=/home/agent',
        '-v', '%s:/usr/local/bin/codex:ro' % a.codex_bin.resolve(), '-v', '%s:/home/agent/.codex' % home],
        codex_argv(slug, prompt), 'codex exec', timing)
    if timing['ending'] == 'own' and timing['exit_code'] != 0:
        timing['ending'] = 'error'
    proxy.terminate()


# ---- Claude Code
def claude_env(model, base_url):
    return {'ANTHROPIC_BASE_URL': base_url, 'ANTHROPIC_API_KEY': '',
            'ANTHROPIC_MODEL': model, 'ANTHROPIC_DEFAULT_SONNET_MODEL': model,
            'ANTHROPIC_DEFAULT_OPUS_MODEL': model, 'ANTHROPIC_DEFAULT_HAIKU_MODEL': model,
            'CLAUDE_CODE_SUBAGENT_MODEL': model, 'FORCE_AUTO_BACKGROUND_TASKS': '1',
            'ENABLE_BACKGROUND_TASKS': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'IS_SANDBOX': '1', 'CLAUDE_CONFIG_DIR': LOGS + '/sessions',
            'CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS': '0'}


def claude_command(session, model):
    tool = harness.CLAUDE_CLOCK_TOOL
    return ('export CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1; '
            'export HOME=%s; ' % HOME
            + 'cat %s/prompt.txt | ' % CELL
            + '%s/claude --verbose --output-format=stream-json ' % STATIC
            + '--session-id %s --model %s --permission-prompts none ' % (session, model)
            + '--settings %s/closed-book-settings.json ' % STATIC
            + '--tools %s --allowedTools %s --restricted --strict-mcp-config ' % (tool, tool)
            + '--mcp-config %s/clock-mcp.json ' % STATIC
            + "--setting-sources '' --disable-slash-commands --no-chrome "
            + '--system-prompt-file %s/system.txt ' % CELL
            + '--effort max --permission-mode=dontAsk '
            + '--print 2>&1 | tee %s/claude-code.txt' % LOGS)


def claude_static_files():
    """The clock MCP server and the two configs the command points to, as in the main runs' clock route."""
    dumps = lambda obj: json.dumps(obj, sort_keys=True, separators=(',', ':')).encode() + b'\n'
    return {'clock_tool.py': Path(clock_mcp.__file__).read_bytes(),
            'clock-mcp.json': dumps(clock_mcp.mcp_config(STATIC + '/clock_tool.py')),
            'closed-book-settings.json': dumps(harness.CLAUDE_CLOCK_SETTINGS)}


def claude_ending(ending, events):
    """'refusal' for the provider's no-fallback refusal; 'error' if a run that ended on its own has no successful
    final result event (tee hides Claude Code's exit status)."""
    lines = events.read_text(errors='replace').splitlines()
    if any('model_refusal_no_fallback' in line for line in lines):
        return 'refusal'
    try:
        result = [e for e in (json.loads(line) for line in lines if line.startswith('{')) if e.get('type') == 'result']
    except ValueError:
        result = []
    return 'error' if ending == 'own' and (not result or result[-1].get('is_error')) else ending


def run_claude(a, cell, prompt, timing):
    model, session = CLAUDE_MODELS[a.model], str(uuid.uuid4())
    for name, data in claude_static_files().items():
        (a.static / name).write_bytes(data)
    proxy, base_url = None, 'https://openrouter.ai/api'
    if a.log_port:
        proxy = start_proxy(a.log_port, a.out / 'msg-proxy-ledger.jsonl', 'claude-code')
        base_url = 'http://127.0.0.1:%d' % a.log_port
    for d in ('home', 'logs/sessions'):
        (a.out / d).mkdir(parents=True, exist_ok=True)
    (a.out / 'prompt.txt').write_bytes(prompt.encode('utf-8'))
    hle = cell['family'] == 'humanitys-last-exam'
    (a.out / 'system.txt').write_bytes(benchmarks.HLE_SYSTEM_PROMPT.encode() if hle else b'')   # GPQA: empty
    sh(['chown', '-R', '1000:1000', str(a.out / 'home'), str(a.out / 'logs')])
    (a.out / 'home').chmod(0o700)
    timing.update(model=model, session_id=session, command=claude_command(session, model))
    options = [x for k, v in claude_env(model, base_url).items() for x in ('-e', '%s=%s' % (k, v))]
    options += ['-v', '%s:%s:ro' % (a.static, STATIC), '-v', '%s/prompt.txt:%s/prompt.txt:ro' % (a.out, CELL),
                '-v', '%s/system.txt:%s/system.txt:ro' % (a.out, CELL), '-v', '%s/home:%s' % (a.out, HOME),
                '-v', '%s/logs:%s' % (a.out, LOGS)]
    run_in_container(a, 'swap-claude-' + a.cell.lower(), options, ['sh', '-c', timing['command']],
                     STATIC + '/claude', timing)
    if timing['ending'] != 'setup_error':
        timing['ending'] = claude_ending(timing['ending'], a.out / 'events.jsonl')
    if proxy:
        proxy.terminate()


def main():
    common = argparse.ArgumentParser(add_help=False)
    for flag in ('cell', '--prompt', '--out', '--image', '--env-file'):
        common.add_argument(flag, **({} if flag == 'cell' else {'required': True}))
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='harness', required=True)
    cx = sub.add_parser('codex', parents=[common])
    cx.add_argument('--codex-bin', type=Path, required=True)
    cx.add_argument('--models-cache', required=True)
    cx.add_argument('--model', choices=tuple(CATALOG), default='fable')
    cx.add_argument('--port', type=int, default=18790)
    cc = sub.add_parser('claude-code', parents=[common])
    cc.add_argument('--static', type=Path, required=True)
    cc.add_argument('--model', choices=tuple(CLAUDE_MODELS), default='astra')
    cc.add_argument('--log-port', type=int)
    a = ap.parse_args()
    cell, prompt = load_cell(a.cell, a.prompt)
    a.out = Path(a.out).resolve()
    a.out.mkdir(parents=True, exist_ok=True)
    timing = {'cell': a.cell, 'harness': a.harness, 'requested_s': float(cell['requested_s']),
              'backstop_s': float(cell['backstop_s']), 'image': a.image}
    if a.harness == 'codex':
        run_codex(a, cell, prompt, timing)
    else:
        a.static = a.static.resolve()
        run_claude(a, cell, prompt, timing)
    (a.out / 'timing.json').write_text(json.dumps(timing, indent=1) + '\n')


if __name__ == '__main__':
    main()
