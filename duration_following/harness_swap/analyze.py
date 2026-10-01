"""Harness swap (Appendix B): prints the LaTeX bodies of Table 3's lower block (tab:duration-following),
tab:harness-ablation, tab:harness-swap-cells and tab:harness-swap-agentic, then the Appendix B numbers that come from
runtimes, scores and token counts:

    python analyze.py RUNS_CSV [--cells CELLS_CSV]

RUNS_CSV: one row per run, ours and the main runs of the same cells, in the order the bootstrap uses (tasks are
resampled in order of first appearance). Columns: source (swap, provider_check, or campaign for a main run); model
(claude-fable-5-1, gpt-5.6-sol, gpt-6-astra); harness (claude-code, codex); cell (CELLS_CSV row, default cells.csv,
which gives block, family, task and request); runtime_s; ending (own; cutoff; refusal; error, runtime a lower bound;
harness, a main run that did not end on its own; unlabelled, counted as own); failed_check (the main-run check an
agentic run failed after ending on its own, time kept; else empty); correct (1/0 for questions); output_tokens
(Fable's question runs).

On time is 0.8 to 1.25 times the request; deviation is exp(mean |ln(runtime/request)|) pooled over runs; slope is the
mean of per-task log-log slopes over tasks with all three requests; brackets are 95% percentile intervals from 20,000
resamples of tasks (numpy default_rng(20260925)). Refusals and errors are left out, and so is the same task and request
in the model's other harness, so both harnesses of a model cover the same runs.
"""
import argparse
import csv
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ON_TIME = (0.8, 1.25)
DROP = ('refusal', 'error')
SEED, RESAMPLES = 20260925, 20000
SETUP = {('claude-fable-5-1', 'claude-code', 'campaign'): 'fable_cc',
         ('claude-fable-5-1', 'claude-code', 'provider_check'): 'fable_cc_or',
         ('claude-fable-5-1', 'codex', 'swap'): 'fable_codex',
         ('gpt-5.6-sol', 'codex', 'campaign'): 'sol_codex',
         ('gpt-6-astra', 'codex', 'campaign'): 'astra_codex',
         ('gpt-6-astra', 'codex', 'provider_check'): 'astra_codex_or',
         ('gpt-6-astra', 'claude-code', 'swap'): 'astra_cc'}
LABEL = {'fable_cc': ('Fable 5.1', 'Claude Code', False), 'fable_cc_or': ('Fable 5.1', 'Claude Code', True),
         'fable_codex': ('Fable 5.1', 'Codex', True), 'sol_codex': ('GPT-5.6 Sol', 'Codex', False),
         'astra_codex': ('GPT-6 Astra', 'Codex', False), 'astra_codex_or': ('GPT-6 Astra', 'Codex', True),
         'astra_cc': ('GPT-6 Astra', 'Claude Code', True)}            # (model, harness, through OpenRouter)
MAIN = ('fable_cc', 'fable_codex', 'sol_codex', 'astra_codex', 'astra_cc')
PAIRS = (('fable_cc', 'fable_codex'), ('astra_codex', 'astra_cc'))   # (usual harness, swapped harness)
AGENTIC = ('terminal-bench', 'tua-bench', 'pptarena', 'wildclawbench')
FAMILY = {'gpqa-diamond': 'GPQA', 'humanitys-last-exam': 'HLE', 'terminal-bench': 'Terminal-Bench',
          'tua-bench': 'TUA-Bench', 'pptarena': 'PPTArena', 'wildclawbench': 'WildClawBench'}
SHORT = {'102-daily-email-report': '102-email-report', '006-extract-gym-auditorium': '006-gym-auditorium',
         '011-epw-parquet-check': '011-epw-parquet', '088-extract-presenter-photos': '088-presenter-photos',
         '01_Productivity_Flow_task_3_bibtex': 'bibtex', '03_Social_Interaction_task_1_meeting_negotiation':
         'meeting-negotiation', '03_Social_Interaction_task_5_chat_escalation_routing': 'chat-escalation',
         '05_Creative_Synthesis_task_2_goal_highlights': 'goal-highlights'}
GRAY = r'{\scriptsize\color{black!55}%s}'


def load(runs_csv, cells_csv=HERE / 'cells.csv'):
    cells = {c['cell']: c for c in csv.DictReader(open(cells_csv))}
    runs = []
    for r in csv.DictReader(open(runs_csv)):
        c = cells[r['cell']]
        runs.append({'setup': SETUP[(r['model'], r['harness'], r['source'])], 'block': c['block'],
                     'family': c['family'], 'task': c['task_id'], 'selection': c['selection'],
                     'req': float(c['requested_s']) / 60, 'min': float(r['runtime_s']) / 60, 'ending': r['ending'],
                     'check': r['failed_check'], 'correct': r['correct'], 'tokens': r['output_tokens']})
    return pair_runs(runs)


def key(r):
    return r['block'], r['family'], r['task'], round(r['req'], 4)


def pair_runs(runs):
    """Mark kept runs whose partner in the model's other harness was left out (refused, an error, or never run)."""
    for a, b in PAIRS:
        kept = {s: {key(r) for r in runs if r['setup'] == s and r['ending'] not in DROP} for s in (a, b)}
        for r in runs:
            if r['setup'] in (a, b) and r['ending'] not in DROP:
                r['partner_left_out'] = key(r) not in kept[b if r['setup'] == a else a]
    return runs


def stats(runs):
    kept = [r for r in runs if r['ending'] not in DROP]
    ratios = [r['min'] / r['req'] for r in kept]
    by = {}
    for r in kept:
        by.setdefault((r['family'], r['task']), []).append(
            (math.log(r['req']), math.log(r['min']), abs(math.log(r['min'] / r['req']))))
    per = []
    for p in by.values():
        if len({round(x, 6) for x, _, _ in p}) == 3 and len(p) == 3:
            mx, my = np.mean([x for x, _, _ in p]), np.mean([y for _, y, _ in p])
            per.append(sum((x - mx) * (y - my) for x, y, _ in p) / sum((x - mx) ** 2 for x, _, _ in p))
    s = {'runs': len(kept), 'on_time': sum(ON_TIME[0] <= x <= ON_TIME[1] for x in ratios),
         'early': sum(x < ON_TIME[0] for x in ratios), 'late': sum(x > ON_TIME[1] for x in ratios),
         'deviation': math.exp(np.mean([abs(math.log(x)) for x in ratios])) if ratios else None,
         'slope': float(np.mean(per)) if per else None, 'slope_ci': [None, None], 'deviation_ci': [None, None]}
    rng = np.random.default_rng(SEED)
    if len(per) > 1:          # slope interval first, then deviation, from the same generator
        a = np.array(per)
        s['slope_ci'] = [float(v) for v in np.percentile(
            a[rng.integers(0, len(a), size=(RESAMPLES, len(a)))].mean(axis=1), [2.5, 97.5])]
    groups = list(by.values())
    if len(groups) > 1:
        sums = np.array([sum(d for _, _, d in g) for g in groups])
        counts = np.array([len(g) for g in groups])
        idx = rng.integers(0, len(groups), size=(RESAMPLES, len(groups)))
        s['deviation_ci'] = [float(v) for v in np.percentile(
            np.exp(sums[idx].sum(axis=1) / counts[idx].sum(axis=1)), [2.5, 97.5])]
    return s


def select(runs, setup, block=None, cells=None):
    return [r for r in runs if r['setup'] == setup and (block is None or r['block'] == block)
            and not r.get('partner_left_out') and (cells is None or key(r) in cells)]


def paired(runs, own, swap, family=None):
    """Kept runs of the same cells in two setups: {cell key: (own run, swap run)}."""
    sel = {s: {key(r): r for r in runs if r['setup'] == s and r['ending'] not in DROP
               and (family is None or r['family'] == family)} for s in (own, swap)}
    return {k: (sel[own][k], sel[swap][k]) for k in sorted(set(sel[own]) & set(sel[swap]))}


def compute(runs):
    num = {'blocks': {b or 'all': {k: stats(select(runs, k, b)) for k in MAIN} for b in ('questions', 'agentic', None)},
           'provider': {}}
    for direct, via in (('fable_cc', 'fable_cc_or'), ('astra_codex', 'astra_codex_or')):
        cells = {key(r) for r in select(runs, via, 'questions') if r['ending'] not in DROP}
        num['provider'][via] = {k: stats(select(runs, k, 'questions', cells)) for k in (direct, via)}
    q = {k: select(runs, k, 'questions') for k in MAIN}
    num['correct'] = {k: (sum(r['correct'] == '1' for r in q[k]), sum(r['correct'] != '' for r in q[k])) for k in MAIN}
    for own, swap in PAIRS:
        pairs = paired(runs, own, swap).values()
        la = [math.log(a['min'] / a['req']) for a, _ in pairs]
        lb = [math.log(b['min'] / b['req']) for _, b in pairs]
        num[swap] = {'cells': len(la), 'longer': sum(y > x for x, y in zip(la, lb)),
                     'spearman': float(np.corrcoef(np.argsort(np.argsort(la)), np.argsort(np.argsort(lb)))[0, 1]),
                     'median_factor': float(np.exp(np.median([y - x for x, y in zip(la, lb)])))}
    kept = [r for r in runs if r['ending'] not in DROP]
    shortest = {}
    for r in kept:
        shortest[key(r)[:3]] = min(shortest.get(key(r)[:3], 1e9), r['req'])
    late = [r for r in kept if r['setup'] == 'astra_cc' and r['min'] / r['req'] > ON_TIME[1]]
    num['astra_cc_late'] = (len(late), sum(abs(r['req'] - shortest[key(r)[:3]]) < 1e-6 for r in late))
    agentic = [r for r in kept if r['block'] == 'agentic']
    num['flagged'] = {k: (sum(r['setup'] == k and r['check'] != '' for r in agentic),
                          sum(r['setup'] == k for r in agentic)) for k in ('fable_codex', 'astra_cc')}
    num['flagged_subagents'] = sum(r['setup'] == 'astra_cc' and r['check'] == 'subagents' for r in agentic)
    first = {}
    for r in runs:
        if r['family'] == 'terminal-bench':
            first[r['task']] = min(first.get(r['task'], 1e9), round(r['req'], 4))
    tb = paired(runs, 'fable_cc', 'fable_codex', 'terminal-bench')
    short = {k: v for k, v in tb.items() if k[3] == first[k[2]]}
    span = lambda xs: (min(xs), max(xs))
    num['fable_tb'] = {'cells': len(tb), 'longer_in_codex': sum(b['min'] > a['min'] for a, b in tb.values()),
                       'shortest_requests': sorted({k[3] for k in short}),
                       'cc': span([a['min'] for a, _ in short.values()]),
                       'codex': span([b['min'] for _, b in short.values()])}
    tok = {}
    for r in runs:
        if r['block'] == 'questions' and r['tokens'] and r['setup'] in ('fable_cc', 'fable_codex'):
            tok.setdefault(key(r), {})[r['setup']] = float(r['tokens'])
    ratio = [t['fable_codex'] / t['fable_cc'] for t in tok.values() if len(t) == 2]
    num['fable_tokens'] = (float(np.median(ratio)), sum(x > 1 for x in ratio), len(ratio))
    return num


# ---- LaTeX table bodies, in the paper's format
def minus(x, fmt='%.2f'):
    t = fmt % x
    return (fmt % 0.0 if float(t) == 0 else t).replace('-', '$-$')      # never print -0.00


def br(lo, hi):
    return '' if lo is None else ' ' + GRAY % ('[%s, %s]' % (minus(lo), minus(hi)))


def ratio_text(r):
    on = ON_TIME[0] <= r <= ON_TIME[1]
    for nd in (2, 3):                      # a third decimal where two would round onto the 0.8 or 1.25 bound
        s = ('%.0f' % r) if r >= 10 else ('%.' + str(nd) + 'f') % r
        if (ON_TIME[0] <= float(s) <= ON_TIME[1]) == on:
            return s
    return s


def cell_text(run):
    if run is None:
        return '--'
    m, ratio = run['min'], run['min'] / run['req']
    s = '%.1f' % m if m >= 1 else '%.2f' % m
    if run['ending'] == 'refusal':
        return s + ' ' + GRAY % 'refused'
    if run['ending'] == 'error':
        return r'$\geq$%s %s' % (s, GRAY % 'error')
    if ON_TIME[0] <= ratio <= ON_TIME[1]:
        s = r'\textbf{%s}' % s
    mark = {'harness': r'$^{\S}$', 'cutoff': r'$^{\ddagger}$'}.get(run['ending'], r'$^{*}$' if run['check'] else '')
    return s + ' ' + GRAY % (ratio_text(ratio) + r'$\times$') + mark


def latex(runs, num):
    out = {}
    nq = len({r['task'] for r in runs if r['block'] == 'questions'})
    na = len({(r['family'], r['task']) for r in runs if r['block'] == 'agentic'})
    pct = lambda k, n: r'%d\%%' % round(100 * k / n)
    lines = [r'\midrule', r'\multicolumn{7}{@{}l}{\emph{Harness swap (Appendix~\ref{sec:harness-swap}): '
             r'%d questions and %d agentic tasks, within-task slope}} \\' % (nq, na)]
    for k in ('fable_cc', 'fable_codex', 'astra_codex', 'astra_cc'):
        s, (model, harness, via) = num['blocks']['all'][k], LABEL[k]
        lines.append(r'%s (%s)%s & %d & %s & %s & %s & %.2f$\times$%s & %s%s \\' % (
            model, harness, r'$^{\dagger}$' if via else '', s['runs'], pct(s['on_time'], s['runs']),
            pct(s['early'], s['runs']), pct(s['late'], s['runs']), s['deviation'], br(*s['deviation_ci']),
            minus(s['slope']), br(*s['slope_ci'])))
    out['tab:duration-following, lower block'] = lines

    def row(k, s, first):
        model, harness, via = LABEL[k]
        return r'%s & %s%s & %d & %d & %d & %d & %.2f$\times$ & %s%s \\' % (
            model if first else '', harness, r'$^{\dagger}$' if via else '', s['runs'], s['on_time'], s['early'],
            s['late'], s['deviation'], '--' if s['slope'] is None else minus(s['slope']), br(*s['slope_ci']))
    lines = []
    for block, title in (('questions', '%d GPQA and HLE questions, each asked for 1.25, 5 and 20 minutes' % nq),
                         ('agentic', '%d agentic tasks from Terminal-Bench, TUA-Bench, PPTArena and WildClawBench, '
                                     'three requests each' % na)):
        lines.append(r'\multicolumn{8}{@{}l}{\emph{%s}} \\' % title)
        for i, k in enumerate(MAIN):
            lines.append(row(k, num['blocks'][block][k], i == 0 or LABEL[k][0] != LABEL[MAIN[i - 1]][0]))
        lines.append(r'\addlinespace[3pt]')
    lines.append(r'\multicolumn{8}{@{}l}{\emph{Provider check: question runs repeated through OpenRouter, against the '
                 r'same runs direct}} \\')
    for via, direct in (('fable_cc_or', 'fable_cc'), ('astra_codex_or', 'astra_codex')):
        lines += [row(direct, num['provider'][via][direct], True), row(via, num['provider'][via][via], False)]
    out['tab:harness-ablation'] = lines

    def row_of_cells(name, req, fam, task):
        found = [next((r for r in runs if r['setup'] == k and r['family'] == fam and r['task'] == task
                       and abs(r['req'] - req) < 1e-6), None) for k in MAIN]
        return r'%s & %g & %s \\' % (name, round(req, 2), ' & '.join(cell_text(r) for r in found))
    lines = []
    for fam in ('gpqa-diamond', 'humanitys-last-exam'):
        for selection, title in (('picked', 'picked where Fable in Claude Code had missed a request'),
                                 ('random', 'drawn at random')):
            order = list(dict.fromkeys(r['task'] for r in runs if r['family'] == fam and r['selection'] == selection))
            lines.append(r'\multicolumn{7}{@{}l}{\emph{%s, %s}} \\' % (FAMILY[fam], title))
            for i, task in enumerate(order):
                for j, req in enumerate((1.25, 5.0, 20.0)):
                    name = 'Question %d' % (i + 1 + (3 if selection == 'random' else 0)) if j == 0 else ''
                    lines.append(row_of_cells(name, req, fam, task))
            lines.append(r'\addlinespace[3pt]')
    out['tab:harness-swap-cells'] = lines[:-1]
    lines = []
    for fam in AGENTIC:
        lines.append(r'\multicolumn{7}{@{}l}{\emph{%s}} \\' % FAMILY[fam])
        for task in sorted({r['task'] for r in runs if r['family'] == fam}):
            reqs = sorted({round(r['req'], 4) for r in runs if r['family'] == fam and r['task'] == task})
            for j, req in enumerate(reqs):
                name = '' if j else ('Task %s' % task if fam == 'pptarena' else r'\texttt{%s}' % SHORT.get(task, task))
                lines.append(row_of_cells(name, req, fam, task))
        lines.append(r'\addlinespace[3pt]')
    out['tab:harness-swap-agentic'] = lines[:-1]
    return out


def text_numbers(num):
    a = {k: num['blocks']['all'][k] for k in MAIN}
    s = {k: round(v['slope'], 2) for k, v in a.items()}       # Section 4.1 subtracts the tables' two-decimal slopes
    fc, ac, tb, (tok, more, n_tok) = num['fable_codex'], num['astra_cc'], num['fable_tb'], num['fable_tokens']
    return [
        'on time over both blocks: ' + ', '.join('%s %d of %d' % (k, a[k]['on_time'], a[k]['runs']) for k in MAIN),
        'slope gap Astra - Fable: %.2f in Claude Code, %.2f in Codex; Codex - Claude Code: Fable %.2f, Astra %.2f' % (
            s['astra_cc'] - s['fable_cc'], s['astra_codex'] - s['fable_codex'], s['fable_codex'] - s['fable_cc'],
            s['astra_codex'] - s['astra_cc']),
        'question runs correct: ' + ', '.join('%s %d of %d' % (k, *num['correct'][k]) for k in MAIN),
        'Fable, Codex vs Claude Code: %d cells, rank correlation %.2f, %d longer in Codex, median factor %.1f' % (
            fc['cells'], fc['spearman'], fc['longer'], fc['median_factor']),
        'Astra, Claude Code vs Codex: %d cells, rank correlation %.2f, median factor %.2f' % (
            ac['cells'], ac['spearman'], ac['median_factor']),
        'Astra in Claude Code: %d late runs, %d of them on the shortest request' % num['astra_cc_late'],
        'flagged by a campaign check: Fable in Codex %d of %d agentic runs, Astra in Claude Code %d of %d (%d for '
        'subagents)' % (*num['flagged']['fable_codex'], *num['flagged']['astra_cc'], num['flagged_subagents']),
        'Fable on Terminal-Bench: longer in Codex on %d of %d runs; at %s min, %.0f-%.0f min in Codex and %.0f-%.0f in '
        'Claude Code' % (tb['longer_in_codex'], tb['cells'], ' or '.join('%g' % x for x in tb['shortest_requests']),
                         *tb['codex'], *tb['cc']),
        'Fable output tokens, Codex over Claude Code: median %.1f, more in Codex on %d of %d question runs' % (
            tok, more, n_tok)]


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('runs', type=Path, help='per-run CSV (columns above)')
    ap.add_argument('--cells', type=Path, default=HERE / 'cells.csv')
    args = ap.parse_args()
    runs = load(args.runs, args.cells)
    num = compute(runs)
    for name, lines in latex(runs, num).items():
        print('%% %s\n%s\n' % (name, '\n'.join(lines)))
    print('\n'.join(text_numbers(num)))
