"""Render the two harness-swap figures: harness_swap.pdf (fig:harness-swap) and harness_swap_cells.pdf
(fig:harness-swap-all-cells), printed at 396 pt wide.

    python figures.py RUNS_CSV [--cells CELLS_CSV] [--out figures]

RUNS_CSV and CELLS_CSV as in analyze.py. harness_swap: (a) Fable 5.1 in Claude Code (filled) and Codex (hollow),
(b) GPT-6 Astra in Codex (filled) and Claude Code (hollow): runtime against request on the 18 questions with log-log
fits (every question has all three requests, so a fit's slope is the within-task slope); (c) within-task slopes with
95% intervals over tasks, questions (circles) and agentic tasks (squares). harness_swap_cells: runtime/request in the
usual harness against the swapped harness for every cell both harnesses kept.
"""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.lines
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager

from analyze import DROP, HERE, ON_TIME, compute, load, paired

_fonts = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams.update({'font.family': next(f for f in ('Helvetica', 'Arial', 'DejaVu Sans') if f in _fonts),
                     'pdf.fonttype': 42, 'axes.unicode_minus': False, 'savefig.facecolor': 'white'})
FABLE, FABLE_TREND, ASTRA = '#d97757', '#b34e2c', '#2b2b2b'
INK, BODY, MUTED, AXIS, GUIDE, EQUAL, BAND = '#161616', '#3d3d3d', '#6f6f6b', '#bdbdb8', '#cfcfcb', '#7b7b77', '#eeede9'
W = 396.0


def style(ax):
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    for s in ('left', 'bottom'):
        ax.spines[s].set_color(AXIS)
        ax.spines[s].set_linewidth(0.65)
    ax.tick_params(colors=AXIS, labelcolor=BODY, labelsize=6.4, length=2.2, width=0.6, pad=1.8)
    ax.tick_params(which='minor', length=0)
    ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())


def question_points(runs, setup):
    return [(r['req'], r['min']) for r in runs if r['setup'] == setup and r['block'] == 'questions'
            and r['ending'] not in DROP]


def harness_swap(runs, num, out):
    left, gap_ab, right, top, bottom, side, c_labels = 29.0, 9.0, 2.0, 25.0, 22.0, 92.0, 70.0
    c_x0 = left + 2 * side + gap_ab + 10.0 + c_labels
    c_w = W - c_x0 - right - 16.0
    H = side + top + bottom
    lim, reqs = (0.3, 36.0), (1.25, 5, 20)
    fig = plt.figure(figsize=(W / 72, H / 72))
    axes = lambda x0, w: fig.add_axes([x0 / W, bottom / H, w / W, side / H])
    fits = {}
    for k in ('fable_cc', 'fable_codex', 'astra_codex', 'astra_cc'):
        pts = question_points(runs, k)
        fits[k] = np.polyfit(np.log([p[0] for p in pts]), np.log([p[1] for p in pts]), 1)
        assert abs(fits[k][0] - num['blocks']['questions'][k]['slope']) < 1e-9     # balanced: pooled = within-task

    def scatter_panel(ax, own, swap, color, trend, title, show_y):
        t = np.geomspace(*lim, 200)
        ax.fill_between(t, t * ON_TIME[0], t * ON_TIME[1], color=BAND, linewidth=0, zorder=0)
        ax.plot(t, t, color=EQUAL, linewidth=0.8, linestyle=(0, (3, 2.4)), zorder=1)
        for k, filled, dx in ((own, True, 0.9), (swap, False, 1.11)):
            pts = question_points(runs, k)
            kw = (dict(s=9, facecolor=color, edgecolor='white', linewidths=0.3, alpha=0.8) if filled
                  else dict(s=8, facecolor='white', edgecolor=color, linewidths=0.75))
            ax.scatter([p[0] * dx for p in pts], [p[1] for p in pts], zorder=3, clip_on=False, **kw)
            tt = np.geomspace(reqs[0], reqs[-1], 50)
            kw = dict(color=trend, linewidth=1.15, zorder=4, solid_capstyle='round',
                      path_effects=[pe.Stroke(linewidth=2.2, foreground='white'), pe.Normal()])
            if not filled:
                kw.update(linestyle=(0, (2.6, 1.6)), dash_capstyle='round')
            ax.plot(tt, np.exp(fits[k][1]) * tt ** fits[k][0], **kw)
        ax.set_xscale('log'); ax.set_yscale('log'); ax.set_xlim(*lim); ax.set_ylim(*lim)
        ax.set_xticks(list(reqs), ['1.25', '5', '20 min'])
        ax.set_yticks([0.5, 1, 5, 20], ['30 s', '1 min', '5 min', '20 min'] if show_y else [])
        style(ax)
        ax.annotate(title, (0, 1), xycoords='axes fraction', xytext=(0, 13.5), textcoords='offset points',
                    ha='left', va='bottom', fontsize=7.9, fontweight='bold', color=INK)

    ax_a, ax_b = axes(left, side), axes(left + side + gap_ab, side)
    scatter_panel(ax_a, 'fable_cc', 'fable_codex', FABLE, FABLE_TREND, 'a  Fable 5.1', True)
    scatter_panel(ax_b, 'astra_codex', 'astra_cc', ASTRA, ASTRA, 'b  GPT-6 Astra', False)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    for ax, entries, color, trend in ((ax_a, ('Claude Code', 'Codex'), FABLE, FABLE_TREND),
                                      (ax_b, ('Codex', 'Claude Code'), ASTRA, ASTRA)):
        x_pt = 1.0                                      # key under the panel title: filled = usual harness
        for name, filled in zip(entries, (True, False)):
            fx, fy = x_pt / side, 1 + 5.4 / side
            ax.plot([fx, fx + 9.0 / side], [fy, fy], transform=ax.transAxes, clip_on=False, color=trend,
                    linewidth=1.0, linestyle='-' if filled else (0, (2.2, 1.4)), zorder=4)
            ax.scatter([fx + 4.5 / side], [fy], transform=ax.transAxes, s=9, clip_on=False, zorder=5,
                       facecolor=color if filled else 'white', edgecolor='white' if filled else color,
                       linewidths=0.3 if filled else 0.75)
            t = ax.text(fx + 11.5 / side, fy, name, transform=ax.transAxes, ha='left', va='center', fontsize=6.3,
                        color=BODY)
            x_pt += 11.5 + t.get_window_extent(renderer).width * 72 / fig.dpi + 6.5
    fig.text((left + side + gap_ab / 2) / W, 3 / H, 'Requested duration', ha='center', va='bottom', fontsize=7.6,
             color=INK)
    fig.text(2 / W, (bottom + side / 2) / H, 'Runtime', ha='left', va='center', rotation=90, fontsize=7.6, color=INK)
    ax_a.annotate('equal duration', (0.62, 0.62), xytext=(-3.2, 3.2), textcoords='offset points', rotation=45,
                  rotation_mode='anchor', ha='center', va='center', color=MUTED, fontsize=5.8, zorder=6)

    ax_c = axes(c_x0, c_w)
    rows = [('Fable 5.1', None, None, None), ('Claude Code', 'fable_cc', FABLE, True),
            ('Codex', 'fable_codex', FABLE, False), ('GPT-6 Astra', None, None, None),
            ('Codex', 'astra_codex', ASTRA, True), ('Claude Code', 'astra_cc', ASTRA, False)]
    ax_c.set_xlim(-0.42, 1.1)
    ax_c.set_ylim(len(rows) - 0.45, -0.55)
    ax_c.axvline(0, color=GUIDE, linewidth=0.7, zorder=0)
    ax_c.axvline(1, color=EQUAL, linewidth=0.8, linestyle=(0, (3, 2.4)), zorder=0)
    label_x = -c_labels + 2.0
    for i, (label, k, color, filled) in enumerate(rows):
        if k is None:
            ax_c.annotate(label, (0, i), xycoords=('axes fraction', 'data'), xytext=(label_x, 0),
                          textcoords='offset points', ha='left', va='center', fontsize=6.6, fontweight='bold',
                          color=FABLE_TREND if label.startswith('Fable') else INK, annotation_clip=False)
            continue
        ax_c.annotate(label, (0, i), xycoords=('axes fraction', 'data'), xytext=(label_x + 5.0, 0),
                      textcoords='offset points', ha='left', va='center', fontsize=6.3, color=BODY,
                      annotation_clip=False)
        for block, marker, dy in (('questions', 'o', -0.17), ('agentic', 's', 0.17)):
            s = num['blocks'][block][k]
            b, (lo, hi), y = s['slope'], s['slope_ci'], i + dy
            ax_c.plot([lo, hi], [y, y], color=color, alpha=0.45, linewidth=1.1, solid_capstyle='round', zorder=2)
            ax_c.scatter([b], [y], s=13 if marker == 'o' else 11, marker=marker, facecolor=color if filled else 'white',
                         edgecolor='white' if filled else color, linewidths=0.3 if filled else 0.75, zorder=3)
            right_side = b < 0.5        # values near 1 go left of the interval, clear of the dashed line at 1
            ax_c.annotate(('%.2f' % b).replace('-', '−'), (max(b, hi) if right_side else min(b, lo), y),
                          xytext=(4.0 if right_side else -4.0, 0), textcoords='offset points',
                          ha='left' if right_side else 'right', va='center', fontsize=5.6, color=BODY,
                          annotation_clip=False, zorder=5,
                          bbox=dict(boxstyle='square,pad=0.06', facecolor='white', edgecolor='none'))
    ax_c.set_yticks([])
    ax_c.set_xticks([0, 0.5, 1], ['0', '0.5', '1'])
    style(ax_c)
    ax_c.spines['left'].set_visible(False)
    ax_c.annotate('c  Within-task slope', (0, 1), xycoords='axes fraction', xytext=(label_x, 13.5),
                  textcoords='offset points', ha='left', va='bottom', fontsize=7.9, fontweight='bold', color=INK)
    for v, text in ((0, 'ignores\nrequest'), (1, 'scales\nwith it')):
        ax_c.annotate(text, (v, 1), xycoords=('data', 'axes fraction'), xytext=(0, 2.0), textcoords='offset points',
                      ha='center', va='bottom', fontsize=5.8, color=MUTED, linespacing=0.95, annotation_clip=False)
    fig.text((c_x0 + c_w / 2) / W, 3 / H, 'Slope', ha='center', va='bottom', fontsize=7.6, color=INK)
    nq = len({r['task'] for r in runs if r['block'] == 'questions'})
    na = len({(r['family'], r['task']) for r in runs if r['block'] == 'agentic'})
    for j, (marker, text) in enumerate((('o', '%d questions' % nq), ('s', '%d agentic tasks' % na))):
        fx, fy = (label_x + 4.0) / c_w, (-7.0 - 7.6 * j) / side
        ax_c.scatter([fx], [fy], transform=ax_c.transAxes, s=9, marker=marker, facecolor=MUTED,
                     edgecolor='white', linewidths=0.3, clip_on=False, zorder=5)
        ax_c.annotate(text, (fx, fy), xycoords='axes fraction', xytext=(4.5, 0), textcoords='offset points',
                      ha='left', va='center', fontsize=5.8, color=MUTED, annotation_clip=False)
    fig.savefig(out / 'harness_swap.pdf', dpi=400)
    plt.close(fig)


def cell_pairs(runs, own, swap):
    return [('questions' if k[0] == 'questions' else k[1], a['min'] / a['req'], b['min'] / b['req'])
            for k, (a, b) in paired(runs, own, swap).items()]


def harness_swap_cells(runs, out):
    shapes = [('questions', 'GPQA/HLE', 'o', 12), ('terminal-bench', 'Terminal-Bench', 's', 11),
              ('tua-bench', 'TUA-Bench', '^', 14), ('pptarena', 'PPTArena', 'D', 9),
              ('wildclawbench', 'WildClawBench', '*', 34)]
    lim, left, gap, right, top, bottom = (0.015, 40.0), 30.0, 40.0, 92.0, 24.0, 25.0
    side = (W - left - gap - right) / 2
    H = side + top + bottom
    fig = plt.figure(figsize=(W / 72, H / 72))
    panels = ((left, 'fable_cc', 'fable_codex', FABLE, 'a  Fable 5.1', 'Claude Code (campaign)', 'Codex (swap)'),
              (left + side + gap, 'astra_codex', 'astra_cc', ASTRA, 'b  GPT-6 Astra', 'Codex (campaign)',
               'Claude Code (swap)'))
    for x0, own, swap, color, title, xlabel, ylabel in panels:
        ax = fig.add_axes([x0 / W, bottom / H, side / W, side / H])
        ax.axvspan(*ON_TIME, color=BAND, linewidth=0, zorder=0)
        ax.axhspan(*ON_TIME, color=BAND, linewidth=0, zorder=0)
        t = np.geomspace(*lim, 10)
        ax.plot(t, t, color=EQUAL, linewidth=0.8, linestyle=(0, (3, 2.4)), zorder=1)
        pts = cell_pairs(runs, own, swap)
        for fam, _, marker, size in shapes:
            sel = [(x, y) for f, x, y in pts if f == fam]
            ax.scatter([x for x, _ in sel], [y for _, y in sel], s=size, marker=marker, facecolor=color,
                       edgecolor='white', linewidths=0.35, alpha=0.85, zorder=3, clip_on=False)
        ax.set_xscale('log'); ax.set_yscale('log'); ax.set_xlim(*lim); ax.set_ylim(*lim)
        ticks = ([0.1, 1, 10], ['0.1$\\times$', '1$\\times$', '10$\\times$'])
        ax.set_xticks(*ticks)
        ax.set_yticks(*ticks)
        style(ax)
        ax.annotate(title, (0, 1), xycoords='axes fraction', xytext=(0, 10.0), textcoords='offset points',
                    ha='left', va='bottom', fontsize=7.9, fontweight='bold', color=INK)
        ax.set_xlabel(xlabel, fontsize=7.0, color=INK, labelpad=2.5)
        ax.set_ylabel(ylabel, fontsize=7.0, color=INK, labelpad=1.5)
    kx = (left + 2 * side + gap + 14.0) / W
    for i, (_, name, marker, size) in enumerate(shapes):
        ky = (bottom + side - 6.0 - 10.5 * i) / H
        fig.add_artist(matplotlib.lines.Line2D([kx], [ky], marker=marker, markersize=np.sqrt(size) * 1.25,
                                               markerfacecolor=MUTED, markeredgecolor='white', markeredgewidth=0.35,
                                               linestyle='none', transform=fig.transFigure))
        fig.text(kx + 6.0 / W, ky, name, ha='left', va='center', fontsize=6.3, color=BODY)
    fig.text(kx - 3.0 / W, (bottom + side - 6.0 - 10.5 * len(shapes) - 6.0) / H,
             'Shading: 0.8–1.25$\\times$\nthe request\nDashed: same\nin both harnesses', ha='left', va='top',
             fontsize=5.8, color=MUTED, linespacing=1.1)
    fig.savefig(out / 'harness_swap_cells.pdf', dpi=400)
    plt.close(fig)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('runs', type=Path)
    ap.add_argument('--cells', type=Path, default=HERE / 'cells.csv')
    ap.add_argument('--out', type=Path, default=Path('figures'))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    runs = load(args.runs, args.cells)
    harness_swap(runs, compute(runs), args.out)
    harness_swap_cells(runs, args.out)
    print('wrote', args.out / 'harness_swap.pdf', 'and', args.out / 'harness_swap_cells.pdf')
