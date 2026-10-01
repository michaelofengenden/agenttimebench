"""Tests for preliminary/time_control on synthetic inputs: request builders, captures, scorers and analyses."""
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / 'preliminary/time_control'
sys.path.insert(0, str(ROOT))
from api_prompt_variants import analyze as api, build_manifest as manifest, openrouter_capture as capture  # noqa: E402
from cli_native import analyze as cli, cells  # noqa: E402
from cli_native.capture import run_cell  # noqa: E402
from cli_native.score import score_attempt  # noqa: E402
from crazy_eights import analyze as drawing  # noqa: E402
from crazy_eights.observer import validate_svg  # noqa: E402
sys.path.remove(str(ROOT))

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="{}"/></svg>'


def fake_question(task_id):
    return 'Question placeholder for ' + task_id


def write_csv(path, rows):
    with open(path, 'w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_api_manifest_prompts_and_order():
    built = manifest.build_manifest(manifest.load_tasks(fake_question))
    assert len(built['cells']) == 288 and len(built['blocks']) == 72
    assert built['block_order'][:3] == ['festival-food-waste__original_minutes__120s__r1',
                                        'hle-6716afcdb8bcd4d88c34cd1d__timing_priority__180s__r2',
                                        'festival-food-waste__equivalent_seconds__180s__r2']
    food = {(c['variant'], c['target_seconds']): c['prompt'] for c in built['cells']
            if c['task_id'] == 'festival-food-waste'}
    sha16 = lambda text: hashlib.sha256(text.encode()).hexdigest()[:16]
    assert sha16(food[('original_minutes', 120)]) == '671eae0c854e63d1'
    assert sha16(food[('concise_minutes', 120)]) == 'f5f8cc7f1cd6e524'
    assert sha16(food[('timing_priority', 180)]) == 'fae2ab593a19bfd4'
    assert ('Think about the task for exactly 2 minutes. Then begin your final answer. Produce no visible '
            'text during that interval.') in food[('concise_minutes', 120)]
    payload = built['cells'][0]['payload']
    assert payload['reasoning'] == {'effort': 'xhigh', 'exclude': True}
    assert payload['provider']['allow_fallbacks'] is False and built['cells'][0]['backstop_seconds'] == 240


class _Response:
    status, headers = 200, {'Content-Type': 'text/event-stream'}

    def __init__(self, chunks, clock):
        self.chunks, self.clock = chunks, clock

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read1(self, size):
        if not self.chunks:
            return b''
        self.clock['now'], data = self.chunks.pop(0)
        return data


def _capture(folder, monkeypatch, chunks, deadline=240):
    folder.mkdir()
    clock = {'now': 0}
    monkeypatch.setattr(capture.time, 'monotonic_ns', lambda: clock['now'])
    monkeypatch.setattr(capture.urllib.request, 'urlopen', lambda request, timeout: _Response(list(chunks), clock))
    return capture.capture({'model': 'openai/gpt-6-astra'}, folder, 'key', deadline, ['openai/gpt-6-astra'])


def _sse(content=None, finish=None):
    choice = {'index': 0, 'delta': {} if content is None else {'content': content}, 'finish_reason': finish}
    record = {'id': 'g1', 'model': 'openai/gpt-6-astra', 'provider': 'OpenAI', 'choices': [choice]}
    return b'data: ' + json.dumps(record).encode() + b'\n\n'


def test_first_nonwhitespace_text_is_the_endpoint(tmp_path, monkeypatch):
    chunks = [(5, b': OPENROUTER PROCESSING\n\n'), (7, _sse('\n ')), (9, _sse('Plan')),
              (11, _sse(' more', 'stop')), (12, b'data: [DONE]\n\n')]
    receipt = _capture(tmp_path / 'a', monkeypatch, chunks)
    assert receipt['status'] == 'completed' and receipt['timings_ns']['first_text'] == 9
    assert receipt['timings_ns']['last_text'] == 11


def test_content_filter_and_deadline(tmp_path, monkeypatch):
    receipt = _capture(tmp_path / 'a', monkeypatch, [(3, _sse(None, 'content_filter'))])
    assert receipt['status'] == 'failed' and receipt['reason'] == 'unsupported_finish_reason'
    receipt = _capture(tmp_path / 'b', monkeypatch, [(2, b': x\n\n'), (240 * 10**9, b': x\n\n')])
    assert receipt['status'] == 'cutoff' and receipt['timings_ns']['first_text'] is None


def test_api_analysis(tmp_path):
    # Astra's second request is cut off with no answer; Fable's second one is filtered.
    times = {('sol', 1): 130, ('sol', 2): 150, ('astra', 1): 125, ('fable', 1): 200, ('opus', 1): 125, ('opus', 2): 125}
    rows = []
    for rep in (1, 2):
        for model in api.MODELS:
            t = times.get((model, rep))
            outcome = 'completed' if t else 'cutoff' if model == 'astra' else 'content_filter'
            rows.append({'model': model, 'task': 'food', 'variant': 'concise_minutes', 'target_s': 120, 'rep': rep,
                         'phase': 'initial', 'outcome': outcome, 't_first_text_s': t or '',
                         't_finished_s': 240 if outcome == 'cutoff' else (t or 3) + 1,
                         'delivery_events': (5 if model == 'opus' else 1) if t else ''})
    s = api.summarize(api.load(write_csv(tmp_path / 'attempts.csv', rows)))
    assert s['sol_vs_astra'] == {'sol': 0, 'astra': 1, 'tie': 0}
    # Sol's 30 s error beats the cut-off Astra request's lower bound of 240 - 120 s.
    assert s['sol_vs_astra_with_bounds'] == {'sol': 1, 'astra': 1, 'tie': 0, 'unresolved': 0}
    assert 'Sol & 2/2 & 0 & 0 & 20 & 1/2 \\\\' in api.table(s)  # median of |130-120| and |150-120|
    assert 'Fable 5.1 & 1/2 & 1 & 0 & 80 & 0/2 \\\\' in api.table(s)
    assert s['by_model']['opus']['single_event'] == 0 and s['by_model']['sol']['single_event'] == 2


def test_cli_grid():
    initial = cells.initial_cells(fake_question)
    tools = cells.tools_cells(initial)
    assert len(initial) == 159 and len(tools) == 66 and len({c['family'] for c in initial}) == 11
    first = next(c for c in initial if c['id'] == 'scheduled_cues-community-reading-space-two-minutes-codex')
    assert first['prompt_sha256'] == '3c68203e04399d2350d6f056daf620a954fd5b61edeae236987a0ff5d195ed8a'
    assert [c['id'] for c in tools[:3]] == [
        'crazy_eights-summer-bus-stop-shade-eight-minutes-codex-capability-v1-cli-tools',
        'crazy_eights-summer-bus-stop-shade-eight-minutes-codex-capability-v1-no-tools',
        'crazy_eights-travel-bag-small-items-eight-minutes-claude-capability-v1-cli-tools']
    assert cells.TOOLS_SENTENCE in tools[0]['prompt'] and 'Do not use tools.' in tools[1]['prompt']


def test_scorers():
    v2 = cells.v2_cells(fake_question)
    cell = next(c for c in v2 if c['family'] == 'reasoning_duration')
    done = {'kind': 'native', 'state': 'completed', 'model_attempt_started': True}
    events = [{'kind': 'text', 'offset_ms': 120400.0, 'text': ' Answer'}]
    assert score_attempt(cell, events, done)['timing_pass'] is True
    assert score_attempt(cell, [{**events[0], 'offset_ms': 121500.0}], done)['issues'] == ['answer_onset_late']
    pause = next(c for c in cells.legacy_cells() if c['family'] == 'interrupted_rehearsal')
    acked = [{'kind': 'intervention_ack', 'offset_ms': pause['intervention']['at_ms'] + 5, 'evidence': 'harness_accepted'}]
    assert score_attempt(pause, acked, done)['timing_eligible'] is False
    sketches = next(c for c in v2 if c['family'] == 'crazy_eights')
    text = [{'kind': 'text', 'offset_ms': 1000.0 * i, 'text': f'{i}. idea\n'} for i in range(1, 8)]
    result = score_attempt(sketches, text + [{'kind': 'text', 'offset_ms': 480500.0, 'text': '8. last'}], done)
    assert result['timing_pass'] is True and [s['number'] for s in result['sketches']] == list(range(1, 9))


def test_cli_analysis(tmp_path):
    def row(study, model, state='completed', errors='answer_onset=200.0', issues='', condition='', source='', final=''):
        return {'study': study, 'model': model, 'tool_condition': condition, 'source_cell_id': source,
                'target_ms': 120000, 'state': state, 'provenance_valid': 1, 'capture_checked': 1, 'scorer_eligible': 1,
                'issues': issues, 'boundary_errors_ms': errors, 'final_text_ms': final}
    rows = [row('initial', 'gpt-5.6-sol'), row('initial', 'gpt-5.6-sol', 'cutoff'),
            row('initial', 'claude-fable-5-1', issues='answer_onset_late', errors='answer_onset=1500.0'),
            row('tools', 'gpt-6-astra', condition='cli_tools', source='a', final=120500),
            row('tools', 'gpt-6-astra', condition='no_tools', source='a', final=90000, errors='answer_onset='),
            row('tools', 'gpt-6-astra', 'failed', condition='cli_tools', source='b'),
            row('tools', 'gpt-6-astra', condition='no_tools', source='b', final=1)]
    s = cli.summarize(cli.load(write_csv(tmp_path / 'runs.csv', rows)))
    assert s['studies']['initial']['by_model']['gpt-5.6-sol'] == {'planned': 2, 'done': 1, 'eligible': 1, 'strict_pass': 1}
    assert s['studies']['initial']['by_model']['claude-fable-5-1']['strict_pass'] == 0
    assert s['planned_pairs'] == 2 and [p['closer_with_tools'] for p in s['pairs']] == [True]
    assert 'Astra & 0/0 & 0/0 & 3/4 & 2/3 & 1/1 \\\\' in cli.table(s)


def _drawing_run(tmp_path, brief, model, last_change_s):
    folder = tmp_path / 'svgs' / f'{brief}_{model}'
    folder.mkdir(parents=True)
    sha = {}
    for i in range(1, 9):
        data = SVG.format(i).encode()
        (folder / f'sketch-{i:02d}.svg').write_bytes(data)
        sha[i] = hashlib.sha256(data).hexdigest()
    changes = {2: ';'.join(f'{i:02d}={sha[i]}' for i in range(1, 8)), 3: f'08={sha[8]}'}
    seconds = [(-0.5, -0.4), (1.0, 1.1), (last_change_s - 1, last_change_s - 0.9), (last_change_s, last_change_s + 0.1),
               (last_change_s + 1, last_change_s + 1.1)]
    observations = [{'run_id': f'{brief}-{model}', 'kind': 'baseline' if k == 0 else 'final' if k == 4 else 'poll',
                     'before_ns': int(lo * 1e9), 'after_ns': int(hi * 1e9),
                     'changes': ';'.join(f'{i:02d}=missing' for i in range(1, 9)) if k == 0 else changes.get(k, '')}
                    for k, (lo, hi) in enumerate(seconds)]
    run = {'run_id': f'{brief}-{model}', 'brief': brief, 'model': model, 'svg_dir': folder.name, 'svg_count': 8,
           'state': 'completed', 'naturally_quiescent': 1, 'export_complete': 1, 'native_terminal_s': last_change_s + 5}
    return run, observations


def test_drawing_analysis(tmp_path):
    runs, observations = [], []
    for k, brief in enumerate(drawing.BRIEFS):
        for model in drawing.MODELS:
            run, obs = _drawing_run(tmp_path, brief, model, 480 + k)
            runs.append(run)
            observations += obs
    s = drawing.summarize(write_csv(tmp_path / 'runs.csv', runs), write_csv(tmp_path / 'obs.csv', observations),
                          tmp_path / 'svgs')
    assert s['svgs'] == s['valid_svgs'] == 96 and s['reached_backstop'] == 0
    # The last file changed between the start of the snapshot before it and the end of the one showing it.
    assert [round(v, 2) for v in s['runs'][0]['endpoint']] == [479.0, 480.1]
    assert 'Lunch container & 8 & [479.00, 480.10] & [479.00, 480.10] \\\\' in drawing.table(s)
    assert not validate_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script/></svg>')['valid']


FAKE_CODEX = '''import json, sys, time
def send(message):
    print(json.dumps(message), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    if message.get('id') == 1:
        send({'id': 1, 'result': {}})
    elif message.get('id') == 2:
        send({'id': 2, 'result': {'model': message['params']['model'], 'thread': {'id': 'th'}}})
    elif message.get('id') == 3:
        send({'id': 3, 'result': {'turn': {'id': 'tu'}}})
        time.sleep(float(message['params']['input'][0]['text']))
        for text in ('Answer', ' done'):
            send({'method': 'item/agentMessage/delta',
                  'params': {'threadId': 'th', 'turnId': 'tu', 'itemId': 'm', 'delta': text}})
        send({'method': 'turn/completed', 'params': {'threadId': 'th', 'turn': {'id': 'tu', 'status': 'completed'}}})
'''


def test_native_capture_with_a_fake_codex(tmp_path, monkeypatch):
    executable = tmp_path / 'bin/codex'
    executable.parent.mkdir()
    executable.write_text(f'#!{sys.executable}\n' + FAKE_CODEX)
    executable.chmod(0o755)
    monkeypatch.setenv('PATH', f'{executable.parent}:/usr/bin:/bin')
    cell = next(c for c in cells.v2_cells(fake_question) if c['family'] == 'reasoning_duration')
    cell = {**cell, 'prompt': '0.3', 'backstop_ms': 5000,
            'requested_boundaries': [cells.boundary('answer_onset', 300, 'first_text')]}
    receipt, events = run_cell(cell, tmp_path / 'done')
    assert receipt['state'] == 'completed' and receipt['model_attempt_started'] is True
    assert receipt['score']['timing_pass'] is True and 300 <= receipt['score']['first_text_ms'] < 1300
    assert [e['kind'] for e in events][0] == 'dispatch' and json.loads((tmp_path / 'done/receipt.json').read_text())
    receipt, _ = run_cell({**cell, 'prompt': '3', 'backstop_ms': 400}, tmp_path / 'cut')
    assert receipt['state'] == 'cutoff' and receipt['score']['issues'][0] == 'outcome_cutoff'
