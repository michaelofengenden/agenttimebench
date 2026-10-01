"""Figure 5: retrospective estimates in three panels, one per agent, the five conditions as rows.

    python figures.py parents.csv answers.csv [OUT_DIR]     # -> OUT_DIR/retrospective_forks.pdf and .png

Inputs as in analyze.py. Each dot is one counted answer (status "ok", minutes > 0) at log2(estimate / actual runtime);
the column to the right of each panel is the lane's deviation, exp(mean |ln ratio|). Printed at 396 pt.
"""
import math
import random
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import analyze  # noqa: E402

TITLE = {"claude-fable-5-1": "Fable 5.1 in Claude Code", "gpt-5.6-sol": "GPT-5.6 Sol in Codex",
         "gpt-6-astra": "GPT-6 Astra in Codex"}
COLOR = {"claude-fable-5-1": "#d97757", "gpt-5.6-sol": "#2f7ea2", "gpt-6-astra": "#2b2b2b"}   # paper-wide agent colors
ROWS = [("oracle", "R-oracle", "elapsed-time tool"), ("native", "R-native", "fork, tools on"),
        ("context-only", "R-context-only", "fork, tools off"), ("replay", "R-replay", "rebuilt transcript, API"),
        ("scrubbed", "R-scrubbed", "replay, no time cues")]
INK, BODY, MUTED, AXIS, GUIDE, EQUAL = "#161616", "#3d3d3d", "#6f6f6b", "#bdbdb8", "#e4e3de", "#7b7b77"
# Printed points.
W, LEFT, GAP, DEV_W = 396.0, 78.0, 7.0, 22.0
TOP, ROW, BOTTOM = 21.0, 22.0, 25.0
MARKER_S, MARKER_PT, JITTER = 13.0, 2.2, 0.17
EDGE_PT = MARKER_PT + 1.5
FS_TITLE, FS_ROW, FS_SUB, FS_TICK, FS_DEV, FS_HEAD, FS_XLABEL = 7.5, 8.0, 6.5, 6.5, 7.0, 6.0, 7.5


def fmt_ratio(t):
    return "1×" if t == 0 else (f"{2 ** t}×" if t > 0 else f"1/{2 ** -t}×")


def main(argv):
    every = analyze.load(argv[1], argv[2])
    out = Path(argv[3] if len(argv) > 3 else "figures")
    # dots in (benchmark, task, k) order, the order the jitter below was drawn in for the paper figure
    answers = sorted((a for a in every if a["ratio"] is not None), key=lambda a: (a["benchmark"], a["task"], a["k"]))
    lanes = analyze.lanes(every)
    plt.rcParams.update({"font.family": ["Helvetica", "Arial", "DejaVu Sans"], "pdf.fonttype": 42,
                         "svg.fonttype": "none", "axes.unicode_minus": False, "savefig.facecolor": "white"})
    n = len(ROWS)
    H = TOP + ROW * n + BOTTOM
    AX_W = (W - LEFT) / 3 - GAP - DEV_W
    fig = plt.figure(figsize=(W / 72, H / 72))

    # one shared log2 range from the data, padded so no marker is cut
    logs = [math.log2(a["ratio"]) for a in answers]
    lo, hi = min(min(logs), -1.1), max(max(logs), 1.1)
    span = (hi - lo) / (1 - 2 * EDGE_PT / AX_W)
    xmin, xmax = lo - EDGE_PT * span / AX_W, hi + EDGE_PT * span / AX_W
    ticks = [t for t in range(math.ceil(xmin), math.floor(xmax) + 1) if t % 3 == 0]

    rng = random.Random(7)
    axes = []
    for k, agent in enumerate(TITLE):
        x0 = LEFT + k * (GAP + AX_W + DEV_W) + GAP
        ax = fig.add_axes([x0 / W, BOTTOM / H, AX_W / W, ROW * n / H])
        axes.append(ax)
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(0, n)
        for t in ticks:
            if t:
                ax.axvline(t, color=GUIDE, lw=0.6, zorder=0)
        ax.axvline(0, color=EQUAL, lw=0.8, ls=(0, (3, 2)), zorder=1)
        dev_x = 1 + DEV_W / AX_W                      # right edge of the deviation column, in axes units
        for i, (cond, name, sub) in enumerate(ROWS):
            yc = n - i - 0.5
            if i:
                ax.axhline(yc + 0.5, color=GUIDE, lw=0.6, zorder=0, clip_on=False, xmax=dev_x)
            xs = [math.log2(a["ratio"]) for a in answers if a["agent"] == agent and a["condition"] == cond]
            ys = [yc + rng.uniform(-JITTER, JITTER) for _ in xs]
            ax.scatter(xs, ys, s=MARKER_S, marker="o", facecolor=COLOR[agent], edgecolor="white",
                       linewidths=0.35, alpha=0.8, zorder=3, clip_on=False)
            ax.text(dev_x, yc / n, f"{lanes[(agent, cond)][1]:.2f}×", transform=ax.transAxes, ha="right",
                    va="center", fontsize=FS_DEV, color=INK)
            if k == 0:
                ax.text(-(GAP + 1) / AX_W, (yc + 0.06) / n, name, transform=ax.transAxes, ha="right",
                        va="bottom", fontsize=FS_ROW, fontweight="bold", color=INK)
                ax.text(-(GAP + 1) / AX_W, (yc - 0.04) / n, sub, transform=ax.transAxes, ha="right",
                        va="top", fontsize=FS_SUB, color=MUTED)
        ax.text(0, 1 + 11 / (ROW * n), TITLE[agent], transform=ax.transAxes, ha="left", va="bottom",
                fontsize=FS_TITLE, color=INK)
        ax.text(dev_x, 1 + 2.5 / (ROW * n), "deviation", transform=ax.transAxes, ha="right", va="bottom",
                fontsize=FS_HEAD, color=MUTED)
        ax.set_yticks([])
        ax.set_xticks(ticks)
        ax.set_xticklabels([fmt_ratio(t) for t in ticks])
        ax.tick_params(axis="x", colors=AXIS, labelcolor=BODY, labelsize=FS_TICK, length=2.2, width=0.6, pad=1.5)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(AXIS)
        ax.spines["bottom"].set_linewidth(0.6)
    per_pt = (xmax - xmin) / AX_W
    assert xmin + MARKER_PT * per_pt <= min(logs) and max(logs) <= xmax - MARKER_PT * per_pt, "a marker is clipped"
    axes[1].set_xlabel("estimate ÷ actual runtime", fontsize=FS_XLABEL, color=INK, labelpad=2.5)

    out.mkdir(exist_ok=True)
    fig.savefig(out / "retrospective_forks.pdf")
    fig.savefig(out / "retrospective_forks.png", dpi=400)
    print(f"wrote {out / 'retrospective_forks.pdf'} and .png")


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__)
    main(sys.argv)
