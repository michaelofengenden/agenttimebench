"""Answer timing for the 288 API requests: the API table, matched Sol/Astra comparisons,
public-text delivery and cutoff overruns.

t is client dispatch to the first non-whitespace public answer text; T is the target.
Medians use valid completed answers; on-time counts use every attempted request.

Input CSV, one row per request (other columns are ignored):
  model            sol | astra | fable | opus
  task, variant, target_s, rep    the block; target_s is T in seconds
  phase            collection phase (`continuation` = collected later)
  outcome          completed | content_filter | cutoff
  t_first_text_s   seconds to the first public text (empty when there was none)
  t_finished_s     seconds to the close of the stream
  delivery_events  number of SSE records that carried public text (completed rows)

    python -m api_prompt_variants.analyze attempts.csv [--tex api_table.tex]
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from statistics import median

MODELS = {'sol': 'Sol', 'astra': 'Astra', 'fable': 'Fable 5.1', 'opus': 'Opus 5.5'}


def load(path):
    with open(path, newline='') as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        target = float(row['target_s'])
        row['block'] = (row['task'], row['variant'], row['target_s'], row['rep'])
        row['valid'] = row['outcome'] == 'completed'
        row['abs_err'] = abs(float(row['t_first_text_s']) - target) if row['valid'] else None
        # A cutoff without any answer only bounds the error from below.
        row['cutoff_bound'] = (max(0.0, float(row['t_finished_s']) - target)
                               if row['outcome'] == 'cutoff' and not row['t_first_text_s'] else None)
    return rows


def by_model(rows):
    table = {}
    for model in MODELS:
        group = [r for r in rows if r['model'] == model]
        valid = [r for r in group if r['valid']]
        table[model] = {
            'attempted': len(group), 'valid': len(valid),
            'filtered': sum(r['outcome'] == 'content_filter' for r in group),
            'cutoff': sum(r['outcome'] == 'cutoff' for r in group),
            'median_abs_err_s': median(r['abs_err'] for r in valid) if valid else None,
            'within_10pct': sum(r['abs_err'] <= float(r['target_s']) * 0.1 for r in valid),
            'within_1s': sum(r['abs_err'] <= 1 for r in valid),
            'single_event': sum(r['delivery_events'] == '1' for r in valid),
            'later_phase': sum(r['phase'] == 'continuation' for r in group)}
    return table


def sol_vs(rows, other):
    """Matched blocks (same task, wording, duration, repetition): who was closer to T."""
    blocks = {}
    for row in rows:
        blocks.setdefault(row['block'], {})[row['model']] = row
    both = {'sol': 0, other: 0, 'tie': 0}
    bounded = {'sol': 0, other: 0, 'tie': 0, 'unresolved': 0}
    for block in blocks.values():
        a, b = block['sol'], block[other]
        if a['valid'] and b['valid']:
            delta = b['abs_err'] - a['abs_err']
            winner = 'sol' if delta > 1e-9 else other if delta < -1e-9 else 'tie'
            both[winner] += 1
            bounded[winner] += 1
            continue
        winner = 'unresolved'
        for name, x, y in (('sol', a, b), (other, b, a)):
            if x['valid'] and y['cutoff_bound'] is not None and x['abs_err'] < y['cutoff_bound']:
                winner = name
        bounded[winner] += 1
    return both, bounded


def summarize(rows):
    both, bounded = sol_vs(rows, 'astra')
    overruns = [(float(r['t_finished_s']), 2 * float(r['target_s'])) for r in rows
                if r['outcome'] == 'cutoff' and float(r['t_finished_s']) > 2 * float(r['target_s']) + 1]
    return {'by_model': by_model(rows), 'sol_vs_astra': both, 'sol_vs_astra_with_bounds': bounded,
            'overruns': overruns}


def table(summary):
    """The appendix table tab:api-timing-short."""
    rows = [f"{MODELS[m]} & {g['valid']}/{g['attempted']} & {g['filtered']} & {g['cutoff']} & "
            f"{g['median_abs_err_s']:.0f} & {g['within_10pct']}/{g['attempted']} \\\\"
            for m, g in summary['by_model'].items()]
    return ('\\begin{tabular}{@{}lrrrrr@{}}\n\\toprule\nModel & Valid / tried & Filtered & Cutoff & '
            'Median $|t-T|$ (s) & Within $\\pm10\\%$ \\\\\n\\midrule\n' + '\n'.join(rows)
            + '\n\\bottomrule\n\\end{tabular}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('attempts', type=Path)
    parser.add_argument('--tex', type=Path, help='also write the LaTeX table here')
    args = parser.parse_args()
    s = summarize(load(args.attempts))
    print(table(s))
    if args.tex:
        args.tex.write_text(table(s))
    for model, m in s['by_model'].items():
        print(f"{MODELS[model]}: valid within 1 s {m['within_1s']}; answers in one public text event "
              f"{m['single_event']}/{m['valid']}; collected later {m['later_phase']}/{m['attempted']}")
    both, bounded = s['sol_vs_astra'], s['sol_vs_astra_with_bounds']
    print(f"Sol vs Astra, both answered: {sum(both.values())} pairs; Astra closer {both['astra']}, "
          f"Sol closer {both['sol']}, ties {both['tie']}")
    print(f"Including cutoff lower bounds: Astra {bounded['astra']}, Sol {bounded['sol']}, "
          f"unresolved {bounded['unresolved']}")
    for finished, backstop in s['overruns']:
        print(f'Cutoff overran its backstop: closed at {finished:.1f} s against {backstop:.0f} s')


if __name__ == '__main__':
    main()
