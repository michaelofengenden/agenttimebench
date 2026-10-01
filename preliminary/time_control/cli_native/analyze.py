"""Coverage, timing eligibility, strict one-second passes and tools/no-tools pairs (CLI table).

Eligible: completed, recorded with valid provenance and a checked capture, and scoreable by
score.py (private-phase families and unverified live-update receipt never are). Strict pass:
eligible, at least one scored boundary, no issue, and every boundary within 1 s. Pair error:
|last public text - T| for each arm of a tools/no-tools pair when both arms are eligible.

Input CSV, one row per planned cell (other columns are ignored):
  study               initial | tools
  model               gpt-5.6-sol | gpt-6-astra | claude-fable-5-1
  tool_condition, source_cell_id   tools study only: cli_tools | no_tools, and the pair's source cell
  target_ms           T in milliseconds
  state               completed | cutoff | failed | unavailable | unattempted
  provenance_valid, capture_checked, scorer_eligible   1 or 0 (scorer_eligible = score.py timing_eligible)
  issues              score.py issues joined with ';'
  boundary_errors_ms  label=error_ms pairs joined with ';' (empty error = boundary not observed)
  final_text_ms       last public text offset (score.py final_text_ms)
The paper's tools-study rows were scored after restoring line breaks between distinct Claude
assistant messages (timestamps unchanged); this changed two Fable scores and no table number.

    python -m cli_native.analyze runs.csv [--tex cli_table.tex]
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

MODELS = {'gpt-5.6-sol': 'Sol', 'gpt-6-astra': 'Astra', 'claude-fable-5-1': 'Fable 5.1'}
TOLERANCE_MS = 1000


def load(path):
    with open(path, newline='') as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row['errors'] = [None if v == '' else float(v) for v in
                         (b.split('=', 1)[1] for b in row['boundary_errors_ms'].split(';') if b)]
        row['eligible'] = (row['state'] == 'completed' and row['provenance_valid'] == '1'
                           and row['capture_checked'] == '1' and row['scorer_eligible'] == '1')
        row['strict_pass'] = row['eligible'] and bool(row['errors']) and not row['issues'] and all(
            e is not None and abs(e) <= TOLERANCE_MS for e in row['errors'])
    return rows


def pairs(rows):
    """Tools vs no-tools for the same source cell, when both arms are eligible."""
    arms = {}
    for row in rows:
        if row['study'] == 'tools':
            arms.setdefault(row['source_cell_id'], {})[row['tool_condition']] = row
    error = lambda r: abs(float(r['final_text_ms']) - float(r['target_ms'])) / 1000
    usable = [{'source_cell_id': source, 'model': pair['cli_tools']['model'],
               'tools_error_s': error(pair['cli_tools']), 'no_tools_error_s': error(pair['no_tools']),
               'closer_with_tools': error(pair['cli_tools']) < error(pair['no_tools'])}
              for source, pair in sorted(arms.items())
              if pair['cli_tools']['eligible'] and pair['no_tools']['eligible']]
    return usable, len(arms)


def summarize(rows):
    usable, planned = pairs(rows)
    out = {'pairs': usable, 'planned_pairs': planned, 'studies': {}}
    for study in ('initial', 'tools'):
        group = [r for r in rows if r['study'] == study]
        out['studies'][study] = {'attempts': sum(r['state'] != 'unattempted' for r in group),
                                 'eligible': sum(r['eligible'] for r in group), 'by_model': {}}
        for model in MODELS:
            g = [r for r in group if r['model'] == model]
            out['studies'][study]['by_model'][model] = {
                'planned': len(g), 'done': sum(r['state'] == 'completed' for r in g),
                'eligible': sum(r['eligible'] for r in g), 'strict_pass': sum(r['strict_pass'] for r in g)}
    return out


def table(summary):
    """The appendix table tab:cli-timing-short."""
    rows = []
    for model, label in MODELS.items():
        a, t = (summary['studies'][study]['by_model'][model] for study in ('initial', 'tools'))
        pairs_ = [p for p in summary['pairs'] if p['model'] == model]
        rows.append(f"{label} & {a['done']}/{a['planned']} & {a['strict_pass']}/{a['eligible']} & "
                    f"{t['done']}/{t['planned']} & {t['strict_pass']}/{t['eligible']} & "
                    f"{sum(p['closer_with_tools'] for p in pairs_)}/{len(pairs_)} \\\\")
    return ('\\begin{tabular}{@{}lrrrrr@{}}\n\\toprule\n'
            ' & \\multicolumn{2}{c}{Initial, no tools} & \\multicolumn{2}{c}{Tools study, both arms} '
            '& Closer with \\\\\n\\cmidrule(lr){2-3}\\cmidrule(lr){4-5}\n'
            'Model & Done / planned & Pass / eligible & Done / planned & Pass / eligible & tools / pairs \\\\\n'
            '\\midrule\n' + '\n'.join(rows) + '\n\\bottomrule\n\\end{tabular}\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('runs', type=Path)
    parser.add_argument('--tex', type=Path, help='also write the LaTeX table here')
    args = parser.parse_args()
    s = summarize(load(args.runs))
    print(table(s))
    if args.tex:
        args.tex.write_text(table(s))
    for study, d in s['studies'].items():
        print(f"{study} study: {d['attempts']} attempts, {d['eligible']} eligible completions, "
              f"strict passes {sum(m['strict_pass'] for m in d['by_model'].values())}")
    print(f"Usable pairs: {len(s['pairs'])} of {s['planned_pairs']} planned; "
          f"closer with tools: {sum(p['closer_with_tools'] for p in s['pairs'])}")


if __name__ == '__main__':
    main()
