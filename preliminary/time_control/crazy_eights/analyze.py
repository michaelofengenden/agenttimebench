"""Endpoint intervals for the twelve drawing runs (drawing table), structural validity of their
SVGs, and the figure's four SVGs.

Inputs (other columns are ignored):
  runs.csv          run_id, brief (request file stem), model (gpt-5.6-sol | gpt-6-astra),
                    svg_dir (folder under SVGS), svg_count, state, naturally_quiescent (1/0: no task
                    process left in the container), export_complete (1/0), native_terminal_s
  observations.csv  every observer snapshot in order: run_id, kind (baseline | poll | final),
                    before_ns, after_ns (nanoseconds since dispatch), changes (`NN=<sha256>` or
                    `NN=missing` for each sketch-NN.svg whose state differs from the previous
                    snapshot, joined with ';')
  SVGS              <svg_dir>/sketch-NN.svg, the files exported at the end of each run

    python -m crazy_eights.analyze runs.csv observations.csv svgs/ [--tex drawing_table.tex] [--figures DIR]

--figures copies the first two imaginative scenes of each model to DIR/<model>-scene-<n>.svg,
unchanged (the paper rendered them to PNG with sharp at density 192, width 1600).
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import shutil

from crazy_eights.observer import names, summarize as observe

BRIEFS = {'design_kitchen': 'Lunch container', 'design_bus_stop': 'Bus-stop shade',
          'design_travel_bag': 'Travel organizer', 'fox_variations': 'Fox playing a violin',
          'nine_subjects': 'Nine distinct subjects', 'imaginative': 'Eight imaginative scenes'}
MODELS = {'gpt-5.6-sol': 'sol', 'gpt-6-astra': 'astra'}
BACKSTOP_S = 960


def read_csv(path):
    with open(path, newline='') as handle:
        return list(csv.DictReader(handle))


def snapshot_rows(observations):
    """Rebuild full snapshots from the change-only encoding."""
    rows, state = [], {}
    for obs in observations:
        for change in filter(None, obs['changes'].split(';')):
            index, value = change.split('=')
            state[f'sketch-{index}.svg'] = {'status': 'missing'} if value == 'missing' else {'status': 'ok', 'sha256': value}
        rows.append({'kind': obs['kind'], 'before_monotonic_ns': int(obs['before_ns']),
                     'after_monotonic_ns': int(obs['after_ns']), 'files': dict(state), 'error': None})
    return rows


def summarize(runs_csv, observations_csv, svgs):
    observations = {}
    for obs in read_csv(observations_csv):
        observations.setdefault(obs['run_id'], []).append(obs)
    runs = []
    for run in read_csv(runs_csv):
        count = int(run['svg_count'])
        folder = Path(svgs) / run['svg_dir']
        exported = {n: (folder / n).read_bytes() for n in names(count) if (folder / n).exists()}
        result = observe(snapshot_rows(observations[run['run_id']]), 0, count, exported, run['export_complete'] == '1')
        qualified = result['timing_qualified'] and run['state'] == 'completed' and run['naturally_quiescent'] == '1'
        runs.append({**run, 'endpoint': result['endpoint_interval_seconds'] if qualified else None,
                     'valid_svgs': result['valid_svgs'], 'reasons': result['reasons']})
    return {'runs': runs, 'svgs': sum(int(r['svg_count']) for r in runs),
            'valid_svgs': sum(r['valid_svgs'] for r in runs),
            'reached_backstop': sum(float(r['native_terminal_s']) >= BACKSTOP_S for r in runs)}


def table(summary):
    """The appendix table tab:drawing-timing-short."""
    rows = []
    for brief, label in BRIEFS.items():
        pair = {r['model']: r for r in summary['runs'] if r['brief'] == brief}
        cells = [f"[{r['endpoint'][0]:.2f}, {r['endpoint'][1]:.2f}]" if r['endpoint'] else 'unqualified'
                 for r in (pair['gpt-5.6-sol'], pair['gpt-6-astra'])]
        rows.append(f"{label} & {pair['gpt-5.6-sol']['svg_count']} & {cells[0]} & {cells[1]} \\\\")
    return ('\\begin{tabular}{@{}lrrr@{}}\n\\toprule\nBrief & SVGs/model & Sol endpoint (s) & '
            'Astra endpoint (s) \\\\\n\\midrule\n' + '\n'.join(rows) + '\n\\bottomrule\n\\end{tabular}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('runs', type=Path)
    parser.add_argument('observations', type=Path)
    parser.add_argument('svgs', type=Path)
    parser.add_argument('--tex', type=Path, help='also write the LaTeX table here')
    parser.add_argument('--figures', type=Path, help='copy the figure SVGs here')
    args = parser.parse_args()
    s = summarize(args.runs, args.observations, args.svgs)
    print(table(s))
    if args.tex:
        args.tex.write_text(table(s))
    print(f"Runs: {len(s['runs'])}; SVGs: {s['svgs']}; structurally valid: {s['valid_svgs']}; "
          f"runs reaching the {BACKSTOP_S} s backstop: {s['reached_backstop']}")
    late = max((r for r in s['runs'] if r['endpoint']), key=lambda r: r['endpoint'][1])
    print(f"Latest endpoint: {late['brief']} / {late['model']} at {late['endpoint'][1] / 60:.1f} min")
    if args.figures:
        args.figures.mkdir(parents=True, exist_ok=True)
        for run in s['runs']:
            if run['brief'] == 'imaginative':
                for scene in (1, 2):
                    shutil.copyfile(args.svgs / run['svg_dir'] / f'sketch-{scene:02d}.svg',
                                    args.figures / f"{MODELS[run['model']]}-scene-{scene}.svg")


if __name__ == '__main__':
    main()
