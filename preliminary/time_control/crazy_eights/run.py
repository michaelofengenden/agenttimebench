"""Timed SVG drawing runs: Sol and Astra per brief, Codex CLI at max effort, 480 s target.

The model gets the temporal shell (files, clock, waits) in a networkless container, and the
observer hashes the requested SVG files about once per second. Four short infrastructure
checks must pass first. Collection runs at most four requests at once, with no retries; a
failed request stops the next wave.

    python -m crazy_eights.run --brief crazy_eights/requests/design_*.txt --out runs/design \
        --docker /usr/local/bin/docker --image sha256:<local image with python3>
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from cli_native.capture import run_cell
from crazy_eights.observer import ArtifactObserver

MODELS = ('gpt-5.6-sol', 'gpt-6-astra')
SKETCHES = {'nine_subjects': 9}  # every other brief asks for eight files


def cell(identity, model, prompt, count, target_ms=480000, backstop_ms=960000):
    return {'id': identity, 'system': 'codex', 'model': model, 'effort': 'max',
            'tool_condition': 'cli_tools', 'artifact_profile': 'crazy_eights_svg_v1',
            'sketch_count': count, 'target_ms': target_ms, 'backstop_ms': backstop_ms,
            'prompt': prompt, 'intervention': None}


def check_cell(index, count):
    """Infrastructure check only; never counted as a drawing run."""
    code = ("from pathlib import Path\nimport time\nfor i in range(1,9):\n p=Path('/workspace')/f'sketch-{i:02}.svg'\n"
            " p.write_text(f'<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 200 200\"><circle cx=\"100\" "
            "cy=\"100\" r=\"{i*8}\" fill=\"none\" stroke=\"black\"/></svg>')\n if i==4: time.sleep(2)")
    code = code.replace('range(1,9)', f'range(1,{count + 1})')
    prompt = (f"Use the temporal shell to execute this Python code exactly, which creates "
              f"{'eight' if count == 8 else 'nine'} small SVG test files. "
              'Then reply READY. This is a short infrastructure test, with no eight-minute waiting requirement.\n\n' + code)
    return cell(f'check-{index}', MODELS[index % 2], prompt, count, target_ms=1000, backstop_ms=180000)


def collect(spec, out, tools):
    receipt, events = run_cell(spec, out / spec['id'], tools,
                               lambda container, root: ArtifactObserver(container, root, spec['sketch_count']))
    observation = receipt.get('artifact_observation', {})
    if spec['id'].startswith('check-'):
        text = ''.join(e['text'] for e in events if e['kind'] == 'text')
        receipt['qualification_pass'] = (
            receipt['state'] == 'completed' and receipt.get('observed_model') == spec['model']
            and receipt.get('observed_effort') == 'max' and observation.get('timing_qualified') is True
            and observation.get('valid_svgs') == spec['sketch_count'] and receipt['tool_calls'] > 0
            and 'READY' in text and receipt.get('container_cleanup', {}).get('naturally_quiescent') is True)
    print(json.dumps({'id': spec['id'], 'state': receipt['state'], 'qualification_pass': receipt.get('qualification_pass'),
                      'endpoint_s': observation.get('endpoint_interval_seconds')}), flush=True)
    return receipt


def run_waves(cells, out, tools):
    rows = []
    for start in range(0, len(cells), 4):
        with ThreadPoolExecutor(max_workers=4) as pool:
            wave = list(pool.map(lambda c: collect(c, out, tools), cells[start:start + 4]))
        rows += wave
        if any(r['state'] not in {'completed', 'cutoff'} for r in wave):
            break
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--brief', type=Path, nargs='+', required=True, help='files from crazy_eights/requests/')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--docker', required=True)
    parser.add_argument('--image', required=True, help='local Docker image id with python3')
    args = parser.parse_args()
    tools = {'docker': args.docker, 'image': args.image}
    count = SKETCHES.get(args.brief[0].stem, 8)
    checks = run_waves([check_cell(i, count) for i in range(4)], args.out / 'qualification', tools)
    if len(checks) != 4 or not all(r.get('qualification_pass') for r in checks):
        raise SystemExit('qualification failed; no drawing request sent')
    cells = [cell(f'{brief.stem}-{m}', m, brief.read_text(), count) for brief in args.brief for m in MODELS]
    run_waves(cells, args.out / 'collection', tools)


if __name__ == '__main__':
    main()
