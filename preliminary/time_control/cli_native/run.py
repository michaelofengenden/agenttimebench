"""Run the native-CLI grid: a worker pool per CLI, no retries, one attempt folder per cell.

Initial study: available cells in a seeded random order. Tools study: the seeded pair
order from cells.tools_cells, and a pair's second arm starts only after its first arm
has closed. A CLI is held (no new requests; running ones finish) after a failed or
unavailable attempt, or a completed one without any model activity.

    python -m cli_native.run --study initial --out runs/initial --seed 20260920 --workers 10
    python -m cli_native.run --study tools --out runs/tools --docker /usr/local/bin/docker --image sha256:...
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import threading

from cli_native import cells as grid
from cli_native.capture import run_cell


def holds(receipt):
    return receipt['state'] in {'failed', 'unavailable'} or (
        receipt['state'] == 'completed' and not receipt['model_attempt_started'])


def run_lane(queue, out, tools, workers):
    held = threading.Event()
    closed = {cell['id']: threading.Event() for cell in queue}
    first_arm = {}
    for cell in queue:
        first_arm.setdefault(cell.get('source_cell_id'), cell['id'])

    def one(cell):
        first = first_arm.get(cell.get('source_cell_id'))
        if cell.get('source_cell_id') and first != cell['id']:
            closed[first].wait()
        if not held.is_set():
            receipt, _ = run_cell(cell, Path(out) / cell['id'], tools)
            print(json.dumps({'cell': cell['id'], 'state': receipt['state'],
                              'timing_pass': receipt['score']['timing_pass']}), flush=True)
            if holds(receipt):
                held.set()
        closed[cell['id']].set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one, queue))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', choices=['initial', 'tools'], required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=20260920)
    parser.add_argument('--workers', type=int, default=10, help='concurrent requests per CLI')
    parser.add_argument('--docker')
    parser.add_argument('--image')
    args = parser.parse_args()
    if args.study == 'tools' and not (args.docker and args.image):
        parser.error('the tools study needs --docker and --image')
    initial = grid.initial_cells()
    if args.study == 'initial':
        cells = [c for c in initial if not (c['system'] == 'claude' and c['intervention'])]
        random.Random(args.seed).shuffle(cells)
        tools = None
    else:
        cells = grid.tools_cells(initial)
        tools = {'docker': args.docker, 'image': args.image}
    lanes = [[c for c in cells if c['system'] == system] for system in ('codex', 'claude')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for lane in lanes:
            pool.submit(run_lane, lane, args.out, tools, args.workers)


if __name__ == '__main__':
    main()
