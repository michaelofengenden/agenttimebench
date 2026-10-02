"""Harness swap: the analysis on a small synthetic run table, and the exact question-run configuration."""
import csv
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

DF = Path(__file__).resolve().parents[1] / 'duration_following'
EXP = DF / 'harness_swap'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(DF))           # run_question_cell imports agenttime, as under `python -m` from DF
try:
    analyze = _load('harness_swap_analyze', EXP / 'analyze.py')
    runner = _load('harness_swap_run_question_cell', EXP / 'run_question_cell.py')
    proxy = _load('harness_swap_openrouter_proxy', EXP / 'openrouter_proxy.py')
finally:
    sys.path.remove(str(DF))
GRAY = r'{\scriptsize\color{black!55}%s}'


@pytest.fixture(scope='module')
def table(tmp_path_factory):
    """3 questions and 2 agentic tasks at 3 requests; runtimes are simple functions of the request; Astra refuses
    one question in Claude Code; the provider check repeats the first question."""
    d = tmp_path_factory.mktemp('swap')
    tasks = [('gpqa-diamond', 'q1', 'picked'), ('gpqa-diamond', 'q2', 'picked'),
             ('humanitys-last-exam', 'q3', 'random'), ('terminal-bench', 't1', ''),
             ('terminal-bench', 't2', '')]
    cells = [dict(cell='%s-%d' % (t, i), block='agentic' if s == '' else 'questions', family=f, task_id=t,
                  requested_s=r, selection=s)
             for f, t, s in tasks for i, r in enumerate((240, 600, 1500) if s == '' else (75, 300, 1200))]
    setups = {('campaign', 'claude-fable-5-1', 'claude-code'): lambda r: 600.0,
              ('swap', 'claude-fable-5-1', 'codex'): lambda r: 2.0 * r, ('campaign', 'gpt-5.6-sol', 'codex'): float,
              ('campaign', 'gpt-6-astra', 'codex'): float, ('swap', 'gpt-6-astra', 'claude-code'): lambda r: 1.1 * r,
              ('provider_check', 'claude-fable-5-1', 'claude-code'): float,
              ('provider_check', 'gpt-6-astra', 'codex'): float}
    runs = [dict(source=src, model=m, harness=h, cell=c['cell'], runtime_s=f(c['requested_s']),
                 ending='refusal' if (m, h, c['cell']) == ('gpt-6-astra', 'claude-code', 'q2-0') else 'own',
                 failed_check='subagents' if (m, c['cell']) == ('gpt-6-astra', 't1-1') else '',
                 correct=1 if c['selection'] else '',
                 output_tokens=(2000 if h == 'codex' else 1000) if c['selection'] and m == 'claude-fable-5-1' else '')
            for (src, m, h), f in setups.items() for c in cells
            if src != 'provider_check' or c['task_id'] == 'q1']
    for name, rows in (('cells.csv', cells), ('runs.csv', runs)):
        with open(d / name, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    return d


@pytest.fixture(scope='module')
def result(table):
    runs = analyze.load(table / 'runs.csv', table / 'cells.csv')
    return runs, analyze.compute(runs)


def test_definitions_and_text_numbers(result):
    num = result[1]
    q, a = num['blocks']['questions'], num['blocks']['agentic']
    # the refused Astra run in Claude Code takes its Codex partner out: both have 8 question runs
    assert (q['astra_cc']['runs'], q['astra_codex']['runs'], q['sol_codex']['runs']) == (8, 8, 9)
    assert q['astra_codex']['on_time'] == 8 and q['astra_codex']['deviation'] == pytest.approx(1)
    assert q['fable_codex']['deviation'] == pytest.approx(2) and q['fable_codex']['late'] == 9
    assert q['fable_cc']['slope'] == pytest.approx(0) and q['fable_codex']['slope'] == pytest.approx(1)
    assert (q['fable_cc']['on_time'], q['fable_cc']['early'], q['fable_cc']['late']) == (0, 3, 6)
    assert a['astra_cc']['slope_ci'] == pytest.approx([1, 1]) and a['fable_cc']['runs'] == 6
    # Astra in Claude Code runs 1.1 times its Codex runtime, late under the 5% window: 14 late, 4 on the shortest request
    assert (num['astra_cc']['cells'], num['astra_cc']['longer'], num['astra_cc_late']) == (14, 14, (14, 4))
    assert num['astra_cc']['median_factor'] == pytest.approx(1.1)
    assert num['flagged'] == {'fable_codex': (0, 6), 'astra_cc': (1, 6)}
    assert num['fable_tb']['shortest_requests'] == [4.0] and num['fable_tb']['codex'] == pytest.approx((8, 8))
    assert num['fable_tokens'] == (2.0, 9, 9) and num['correct']['fable_codex'] == (9, 9)
    assert num['provider']['fable_cc_or']['fable_cc_or']['runs'] == 3


def test_latex(result):
    runs, num = result
    tex, dev = analyze.latex(runs, num), num['blocks']['all']['fable_cc']
    assert tex['tab:duration-following, lower block'][2] == (
        r'Fable 5.1 (Claude Code) & 15 & 13\%% & 33\%% & 53\%% & %.2f$\times$ %s & 0.00 %s \\' % (
            dev['deviation'], GRAY % '[%.2f, %.2f]' % tuple(dev['deviation_ci']), GRAY % '[0.00, 0.00]'))
    assert tex['tab:harness-ablation'][4] == r'GPT-6 Astra & Codex & 8 & 8 & 0 & 0 & 1.00$\times$ & 1.00 %s \\' % (
        GRAY % '[1.00, 1.00]')
    assert tex['tab:harness-swap-cells'][2].startswith(r' & 5 & 10.0 %s & 10.0 %s & \textbf{5.0}' % (
        (GRAY % r'2.00$\times$',) * 2))
    run = {'min': 1.0, 'req': 0.8, 'ending': 'own', 'check': ''}
    assert analyze.cell_text(dict(run, ending='refusal')) == '1.0 ' + GRAY % 'refused'
    assert analyze.cell_text(dict(run, ending='error')) == r'$\geq$1.0 ' + GRAY % 'error'
    assert analyze.cell_text(dict(run, ending='cutoff')) == '1.0 ' + GRAY % r'1.25$\times$' + r'$^{\ddagger}$'  # late: not bold
    assert analyze.cell_text(dict(run, req=1.0, ending='cutoff')) == r'\textbf{1.0} ' + GRAY % r'1.00$\times$' + r'$^{\ddagger}$'
    assert analyze.cell_text(dict(run, check='transcript')).endswith(r'$^{*}$')
    assert (analyze.ratio_text(1.0504), analyze.ratio_text(1.1), analyze.ratio_text(12.3)) == ('1.050', '1.10', '12')
    assert (analyze.minus(-0.001), analyze.minus(-0.12)) == ('0.00', '$-$0.12')


def test_scripts_run(table, tmp_path):
    pytest.importorskip('matplotlib')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    args = [str(table / 'runs.csv'), '--cells', str(table / 'cells.csv')]
    out = subprocess.run([sys.executable, '-B', str(EXP / 'analyze.py'), *args], capture_output=True, text=True,
                         env=env, check=True).stdout
    assert '% tab:harness-ablation\n' in out and 'Fable output tokens, Codex over Claude Code: median 2.0' in out
    subprocess.run([sys.executable, '-B', str(EXP / 'figures.py'), *args, '--out', str(tmp_path / 'f')],
                   capture_output=True, env=env, check=True)
    assert sorted(p.name for p in (tmp_path / 'f').iterdir()) == ['harness_swap.pdf', 'harness_swap_cells.pdf']


def test_cells_and_prompt_check(tmp_path):
    cells = list(csv.DictReader(open(EXP / 'cells.csv')))
    assert len(cells) == 101 and sum(c['block'] == 'questions' for c in cells) == 54
    longest = {}
    for c in cells:
        longest[c['family'], c['task_id']] = max(longest.get((c['family'], c['task_id']), 0), float(c['requested_s']))
    for c in cells:
        want = (runner.backstop_s(float(c['requested_s'])) if c['block'] == 'questions'
                else 2 * longest[c['family'], c['task_id']])
        assert float(c['backstop_s']) == want
    prompt, csv_path = b'Q\n\nPlease work on this task for a full 5 minutes.', tmp_path / 'cells.csv'
    csv_path.write_text('cell,block,family,task_id,request,requested_s,backstop_s,selection,prompt_sha256\n'
                        'c,questions,gpqa-diamond,t,middle,300,1800,picked,%s\n' % hashlib.sha256(prompt).hexdigest())
    (tmp_path / 'p.txt').write_bytes(prompt)
    assert runner.load_cell('c', tmp_path / 'p.txt', csv_path)[1] == prompt.decode()
    (tmp_path / 'p.txt').write_bytes(prompt + b' ')
    with pytest.raises(SystemExit):
        runner.load_cell('c', tmp_path / 'p.txt', csv_path)


def test_codex_question_run():
    assert runner.codex_argv('anthropic/claude-fable-5.1', 'P') == [
        'codex', 'exec', '--dangerously-bypass-approvals-and-sandbox', '--skip-git-repo-check', '--model',
        'anthropic/claude-fable-5.1', '--json', '--enable', 'unified_exec', '-c', 'model_reasoning_effort=max',
        '--strict-config', '--ignore-rules', '--', 'P']
    gpqa, hle = runner.codex_config('fable', 28801, False), runner.codex_config('astra', 1, True)
    assert 'model = "anthropic/claude-fable-5.1"\nmodel_provider = "openrouter_cached"' in gpqa
    assert 'suppress_unstable_features_warning = true\n\n[agents]' in gpqa and 'developer_instructions' not in gpqa
    assert '[features]\nshell_tool = false\nview_image = false\n' in gpqa
    assert 'request_permissions_tool = false\nskip_host_skill_discovery = true\n\n[projects."/workspace"]' in gpqa
    assert '[model_providers.openrouter]\nname = "OpenRouter"\nbase_url = "http://127.0.0.1:1/v1"' in hle
    assert 'developer_instructions = """Your response should be in the following format:\n' in hle
    assert 'astra-or-clock-model-catalog.json' in hle and '$' not in gpqa + hle
    sol = {'slug': 'gpt-5.6-sol', 'display_name': 'GPT-5.6-Sol', 'use_responses_lite': True, 'support_verbosity': True}
    got = json.loads(runner.codex_catalog({'models': [sol, dict(sol, slug='other')]}, 'fable'))['models']
    assert got == [dict(sol, slug='anthropic/claude-fable-5.1', display_name='Fable 5.1 via OpenRouter',
                        use_responses_lite=False, support_verbosity=False, shell_type='disabled',
                        apply_patch_tool_type=None, experimental_supported_tools=['clock'], tool_mode='direct')]


def test_claude_question_run(tmp_path):
    assert runner.claude_command('S', 'openai/gpt-6-astra') == (
        "export CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1; export HOME=/opt/agenttime-cc-home; "
        "cat /opt/agenttime-cc-cell/prompt.txt | /opt/agenttime-cc/claude --verbose --output-format=stream-json "
        "--session-id S --model openai/gpt-6-astra --permission-prompts none "
        "--settings /opt/agenttime-cc/closed-book-settings.json --tools mcp__agenttime_clock__current_time "
        "--allowedTools mcp__agenttime_clock__current_time --restricted --strict-mcp-config "
        "--mcp-config /opt/agenttime-cc/clock-mcp.json --setting-sources '' --disable-slash-commands --no-chrome "
        "--system-prompt-file /opt/agenttime-cc-cell/system.txt --effort max --permission-mode=dontAsk "
        "--print 2>&1 | tee /logs/agent/claude-code.txt")
    env = runner.claude_env('openai/gpt-6-astra', 'https://openrouter.ai/api')
    assert env['ANTHROPIC_API_KEY'] == '' and env['CLAUDE_CODE_SUBAGENT_MODEL'] == 'openai/gpt-6-astra'
    files = runner.claude_static_files()
    assert files['closed-book-settings.json'] == b'{"disableAllHooks":true,"disableClaudeAiConnectors":true}\n'
    assert files['clock-mcp.json'] == (b'{"mcpServers":{"agenttime_clock":{"args":["-I","-B","/opt/agenttime-cc/'
                                       b'clock_tool.py"],"command":"/usr/local/bin/python3","type":"stdio"}}}\n')
    assert files['clock_tool.py'] == (DF / 'agenttime/clock_mcp.py').read_bytes()
    events = tmp_path / 'events.jsonl'
    for text, ending, want in (('{"type":"result","is_error":false}\n', 'own', 'own'), ('', 'cutoff', 'cutoff'),
                               ('{"type":"result","is_error":true}\n', 'own', 'error'), ('', 'own', 'error'),
                               ('x model_refusal_no_fallback\n', 'own', 'refusal')):
        events.write_text(text)
        assert runner.claude_ending(ending, events) == want


def test_cutoff_sends_term_then_kill(tmp_path, monkeypatch):
    calls = []

    class Proc:
        returncode = -9

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired('docker', timeout)

    monkeypatch.setattr(runner.subprocess, 'Popen', lambda args, **kw: calls.append(args) or Proc())
    monkeypatch.setattr(runner, 'sh', calls.append)
    assert runner.timed_exec('box', ['codex', 'exec'], 75, 'codex exec', tmp_path)['ending'] == 'cutoff'
    assert calls == [['docker', 'exec', '-i', 'box', 'codex', 'exec'],
                     ['docker', 'exec', 'box', 'pkill', '-TERM', '-f', 'codex exec'], ['docker', 'kill', 'box']]


def test_proxy_codex_body():
    body, added = proxy.codex_body(b'{"model": "m", "input": []}', True)
    assert added and json.loads(body)['cache_control'] == {'type': 'ephemeral'}
    assert proxy.codex_body(b'{"model":"m"}', False) == (b'{"model": "m"}', False)      # re-serialized, as it ran
    assert proxy.codex_body(b'{"cache_control": 1}', True) == (b'{"cache_control": 1}', False)
    assert proxy.codex_body(b'[1]', True) == (b'[1]', False) and proxy.codex_body(b'x', True) == (b'x', False)
