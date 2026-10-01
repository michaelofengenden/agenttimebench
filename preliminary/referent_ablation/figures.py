"""Render the four referent-ablation figures of the paper as PDFs, from the same inputs as analyze.py.

  forecast_by_referent_geometric_mean                 Figure forecast-by-referent
  time_estimate_rationale_cues_by_referent            Figure rationale-cues (Fable 5)
  self_to_human_estimate_ratio_by_human_estimate      Figure self-to-human-ratio
  self_forecast_calibration_by_task_length_quartile   Figure task-length-calibration

Usage: python figures.py <receipts_dir> <codes.jsonl> <actuals.csv> [out_dir (default figures/)]
"""
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

import analyze

COLOR = {"fable": "#cc4a29", "sol": "#1f1f1f"}
POINT = {"fable": "#e8ab91", "sol": "#919191"}
CUE_COLOR = ("#3d3d3d", "#cc4a29", "#c7c5c0")
ARMS = {"you": "you", "frontier": "frontier agent", "human": "skilled human"}
MUTED = "#595959"
plt.rcParams.update({"font.size": 8.5, "axes.spines.top": False, "axes.spines.right": False,
                     "legend.frameon": False, "pdf.fonttype": 42, "savefig.bbox": "tight"})


def subject_legend(ax, **kw):
    ax.legend(handles=[Patch(color=COLOR[s], label=analyze.NAMES[s]) for s in analyze.SUBJECTS], ncol=2, **kw)


def grouped_barh(ax, rows, colors):
    """One group of horizontal bars per arm in ARMS; rows[i] lists (value, label) for each bar."""
    h = 0.8 / len(colors)
    for i, bars in enumerate(rows):
        for j, (value, label) in enumerate(bars):
            y = i + (j - (len(bars) - 1) / 2) * h
            ax.barh(y, value, height=0.9 * h, color=colors[j])
            ax.text(value, y, f"  {label}", va="center", fontsize=7.5, color=MUTED)
    ax.set_yticks(range(len(rows)), ARMS.values())
    ax.invert_yaxis()


def forecast_by_referent(records, _codes, _actuals):
    gm = analyze.arm_geomeans(records)
    fig, ax = plt.subplots(figsize=(4.6, 3.2))
    grouped_barh(ax, [[(gm[s, arm][0], f"{gm[s, arm][0]:.1f}  n={gm[s, arm][1]}") for s in analyze.SUBJECTS]
                      for arm in ARMS], [COLOR[s] for s in analyze.SUBJECTS])
    ax.set_xlabel("geometric-mean forecast (minutes)")
    subject_legend(ax, loc="lower center", bbox_to_anchor=(0.5, 1.0))
    return fig


def rationale_cues(_records, codes, _actuals, subj="fable"):
    cues = analyze.rationale_cues(codes)
    fig, ax = plt.subplots(figsize=(4.6, 3.6))
    grouped_barh(ax, [[(cues[subj, arm][c], f"{cues[subj, arm][c]:.0f}%") for c in analyze.CUES] for arm in ARMS],
                 CUE_COLOR)
    ax.set_xlim(0, 105)
    ax.set_xlabel("share of rationales (%)")
    ns = " / ".join(str(cues[subj, arm]["n"]) for arm in ARMS)
    ax.set_title(f"{analyze.NAMES[subj]}, n = {ns} rationales", fontsize=8, color=MUTED)
    ax.legend(handles=[Patch(color=c, label=label) for c, label in zip(CUE_COLOR, analyze.CUES.values())],
              ncol=3, loc="lower center", bbox_to_anchor=(0.5, 1.07), fontsize=7.5)
    return fig


def binned_medians(points):
    """Seven equal-count bins along the human estimate; the median point of each bin with >= 3 points."""
    ordered = sorted(points, key=lambda p: p["human"])
    size = math.ceil(len(ordered) / 7)
    med = lambda v: sorted(v)[len(v) // 2]
    bins = [ordered[i:i + size] for i in range(0, len(ordered), size)]
    return [(med([p["human"] for p in b]), med([p["ratio"] for p in b])) for b in bins if len(b) >= 3]


def self_to_human(records, _codes, _actuals, y_max=4):
    points = analyze.scatter_points(records)
    fig, ax = plt.subplots(figsize=(6.5, 3.3))
    for subj in analyze.SUBJECTS:
        mine = [p for p in points if p["subject"] == subj]
        for clipped, marker, size in ((False, "o", 9), (True, "^", 22)):
            shown = [p for p in mine if (p["ratio"] > y_max) == clipped]
            ax.scatter([p["human"] for p in shown], [min(p["ratio"], y_max) for p in shown],
                       s=size, marker=marker, color=POINT[subj], lw=0)
        ax.plot(*zip(*binned_medians(mine)), color=COLOR[subj], lw=2)
    ax.set_xscale("log")
    ax.set_xlim(0.1, 5e5)
    ax.set_xticks([0.1, 1, 10, 100, 1e3, 1e4, 1e5], ["0.1", "1", "10", "100", "1k", "10k", "100k"])
    ax.set_yscale("function", functions=(lambda y: y ** 0.5, lambda y: y ** 2))
    ax.set_ylim(0, y_max * 1.02)
    ax.set_yticks([0, 0.25, 1, 2, 4], ["0", "0.25", "1", "2", "4"])
    ax.axhline(1, color=MUTED, lw=0.7, ls=":")
    ax.set_xlabel("Human time estimate (minutes, log scale)")
    ax.set_ylabel("Model estimate / human estimate")
    handles = [Line2D([], [], marker="o", ls="", color=COLOR[s], label=analyze.NAMES[s]) for s in analyze.SUBJECTS]
    ax.legend(handles=handles + [Line2D([], [], color="k", lw=2, label="Binned median")], ncol=3,
              loc="lower left", bbox_to_anchor=(0, 1.0))
    return fig


def task_length_calibration(records, _codes, actuals):
    q = analyze.quartiles(records, actuals)
    fig, ax = plt.subplots(figsize=(3.4, 3.2))
    for k, subj in enumerate(analyze.SUBJECTS):
        ax.bar([i + (k - 0.5) * 0.36 for i in range(4)], [r["ratio"] for r in q if r["subject"] == subj],
               width=0.33, color=COLOR[subj])
    fable = [r for r in q if r["subject"] == "fable"]
    ax.set_xticks(range(4), [f"{r['q']}\n{r['median_actual']:.1f} min\nn={r['n']}" for r in fable])
    ax.set_ylim(0, 1.05)
    ax.axhline(1, color=MUTED, lw=0.7, ls=":")
    ax.set_xlabel("Quartile of actual task runtime")
    ax.set_title("Actual runtime / self-forecast", loc="left", fontsize=9, fontweight="bold", pad=18)
    subject_legend(ax, loc="lower left", bbox_to_anchor=(0, 0.99))
    return fig


def main(receipts_dir, codes, actuals, out="figures"):
    out = Path(out)
    out.mkdir(exist_ok=True)
    records = analyze.latest_ok(analyze.load_receipts(receipts_dir))
    for name, draw in (("forecast_by_referent_geometric_mean", forecast_by_referent),
                       ("time_estimate_rationale_cues_by_referent", rationale_cues),
                       ("self_to_human_estimate_ratio_by_human_estimate", self_to_human),
                       ("self_forecast_calibration_by_task_length_quartile", task_length_calibration)):
        fig = draw(records, codes, actuals)
        fig.savefig(out / f"{name}.pdf")
        plt.close(fig)
        print("wrote", out / f"{name}.pdf")


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit(__doc__.rsplit("Usage: ", 1)[1].strip())
    main(*sys.argv[1:])
