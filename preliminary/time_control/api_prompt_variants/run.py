"""Coordinator for the 288 OpenRouter requests. No retries and no replays.

Blocks run in manifest order: the first block alone, then up to three blocks at once (at most
12 requests). Each request runs in its own openrouter_capture.py process, killed at 2T after
dispatch if it has not closed. A model is held (no new requests) after a failed or ineligible
receipt, except a validated content_filter decline, which is kept as a non-timing outcome.
Every admitted request has a folder, so a restart never sends it again.

    OPENROUTER_API_KEY=... python -m api_prompt_variants.run --manifest manifest.json --out attempts
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

CAPTURE = Path(__file__).with_name('openrouter_capture.py')


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError):
        return default


def is_decline(folder, cell, receipt):
    """A provider content_filter finish that is otherwise a well-formed single generation."""
    if not (folder / 'events.jsonl').exists():
        return False
    events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
    finishes = [i for i, e in enumerate(events) if e.get('finish_reason')]
    return (receipt.get('http_status') == 200 and receipt.get('reason') == 'unsupported_finish_reason'
            and receipt.get('finish_reason') == 'content_filter' and receipt.get('done_seen') is False
            and len(receipt.get('generation_ids', [])) == 1
            and set(receipt.get('observed_models', [])) <= {cell['model'], cell['canonical_model']}
            and finishes == [len(events) - 1])


def holds_model(folder, cell, receipt):
    bad = receipt['status'] == 'failed' or (receipt['status'] == 'completed' and not receipt['eligible'])
    return bad and not is_decline(folder, cell, receipt)


def force_receipt(folder, status, reason):
    """The capture's own receipt, or one written for a process that was killed or died."""
    receipt = load(folder / 'receipt.json')
    if receipt is None:
        receipt = {'status': status, 'eligible': False, 'reason': reason, 'coordinator_forced': True}
        dispatch = load(folder / 'dispatch.json', {}).get('dispatch_monotonic_ns')
        if dispatch is not None:
            receipt['timings_ns'] = {'finished': time.monotonic_ns() - dispatch}
        (folder / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return receipt


def coordinate(manifest_path, out):
    manifest = json.loads(manifest_path.read_text())
    cells = {c['id']: c for c in manifest['cells']}
    out.mkdir(parents=True, exist_ok=True)
    held = set()
    for identity, cell in cells.items():
        if (out / identity).exists():
            receipt = load(out / identity / 'receipt.json')
            if receipt is None:
                raise RuntimeError('unclosed_prior_attempt_no_replay:' + identity)
            if holds_model(out / identity, cell, receipt):
                held.add(cell['model'])

    def run_request(cell):
        folder = out / cell['id']
        process = subprocess.Popen([sys.executable, '-B', str(CAPTURE), '--manifest', str(manifest_path),
                                    '--cell', cell['id'], '--out', str(out)], start_new_session=True)
        started = time.monotonic_ns()
        while process.poll() is None:
            dispatch = load(folder / 'dispatch.json', {}).get('dispatch_monotonic_ns')
            late = (time.monotonic_ns() - dispatch >= cell['backstop_seconds'] * 1e9 if dispatch is not None
                    else time.monotonic_ns() - started > 60e9)
            if late:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                force_receipt(folder, *(('cutoff', 'administrative_backstop') if dispatch is not None
                                        else ('failed', 'dispatch_setup_timeout')))
            time.sleep(0.2)
        receipt = force_receipt(folder, 'failed', 'capture_process_exited_without_receipt')
        if holds_model(folder, cell, receipt):
            held.add(cell['model'])
        print(json.dumps({'cell': cell['id'], 'status': receipt['status'],
                          'first_text_ns': (receipt.get('timings_ns') or {}).get('first_text')}), flush=True)

    def run_block(block):
        selected = [cells[i] for i in manifest['blocks'][block] if not (out / i).exists() and cells[i]['model'] not in held]
        for cell in selected:
            (out / cell['id']).mkdir()  # admitted: never run again
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run_request, selected))

    order = manifest['block_order']
    run_block(order[0])
    with ThreadPoolExecutor(max_workers=manifest['max_concurrent_blocks']) as pool:
        list(pool.map(run_block, order[1:]))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    coordinate(args.manifest, args.out)
