#!/usr/bin/env python3
"""Render the three separate-turn figures.  python figures.py DATA_DIR [out_dir (default: figures)]

  forecast_vs_actual_by_task.png         Fig. programbench-separate-turn-forecast
  forecast_error_by_evidence_level.png   Fig. programbench-evidence-level
  self_score_calibration.png             Fig. separate-turn-self-score

DATA_DIR: see analyze.py.
"""
import math
import random
import statistics as st
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from analyze import SURFACES, load, task_name  # noqa: E402

OUT = Path("figures")
STYLE = {"opus-claude": ("Opus", "#D2603C", "o"), "sol-codex": ("Sol", "#2b2b2b", "D")}
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.edgecolor": "#c3c2b7", "axes.grid": True,
                     "grid.color": "#ecebe6", "grid.linewidth": 0.6, "savefig.bbox": "tight",
                     "savefig.facecolor": "white"})


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=300)
    plt.close(fig)
    print("wrote", OUT / name)


def forecast_vs_actual_by_task(runs, scores, forecasts):
    """Median forecast (hollow) and measured runtime (solid) per task, sorted by Opus runtime."""
    tasks = sorted({t for t, _ in runs}, key=lambda t: -runs[(t, "opus-claude")])
    fig, ax = plt.subplots(figsize=(9.6, 6.2))
    for i, task in enumerate(tasks):
        for dx, cell in ((-0.16, "opus-claude"), (0.16, "sol-codex")):
            name, color, marker = STYLE[cell]
            guess = st.median(f["minutes"] for f in forecasts
                              if f["task_id"] == task and f["cell"] == cell and f["status"] == "completed")
            actual = runs[(task, cell)]
            ax.annotate("", (i + dx, actual), (i + dx, guess),
                        arrowprops=dict(arrowstyle="-|>", color=color, alpha=0.35, lw=1.2, shrinkA=4, shrinkB=5))
            ax.scatter(i + dx, guess, s=46, marker=marker, facecolor="white", edgecolor=color, lw=1.4, zorder=3,
                       label=f"{name} forecast" if i == 0 else None)
            ax.scatter(i + dx, actual, s=46, marker=marker, color=color, zorder=3,
                       label=f"{name} actual" if i == 0 else None)
    for cell, (name, color, _) in STYLE.items():
        med = st.median(m for (t, c), m in runs.items() if c == cell)
        ax.axhline(med, color=color, ls="--", lw=0.9, alpha=0.6)
        ax.text(len(tasks) - 0.5, med * 1.04, f"{name} median actual · {med:.0f} min", ha="right", fontsize=8.5,
                color=color, style="italic")
    hard = {t for t in tasks if scores[(t, "opus-claude")] < 60 and scores[(t, "sol-codex")] < 60}
    ax.set_xticks(range(len(tasks)))
    ax.set_xticklabels([task_name(t) for t in tasks], rotation=40, ha="right", family="monospace")
    for label, task in zip(ax.get_xticklabels(), tasks):
        if task in hard:
            label.set(color="#9c3a1f", weight="bold")
    ax.set_yscale("log")
    ticks = [15, 30, 60, 120, 240, 480]
    ax.set_yticks(ticks)
    ax.set_yticklabels(["15 min", "30 min", "1 h", "2 h", "4 h", "8 h"])
    ax.minorticks_off()
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=4, frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.08))
    ax.set_xlim(-0.6, len(tasks) - 0.4)
    fig.text(0.99, 0.0, "task names in red: both models scored below 60%", ha="right", fontsize=8.5,
             color="#9c3a1f")
    save(fig, "forecast_vs_actual_by_task.png")


def forecast_error_by_evidence_level(runs, forecasts):
    """Each forecast divided by the measured runtime, per evidence surface; margin = mean |log ratio|."""
    rng = random.Random(0)
    fig, ax = plt.subplots(figsize=(9.6, 3.4))
    labels = {"text": "task statement only", "docs": "statement + documentation", "probe": "docs + a runnable binary"}
    for row, surface in enumerate(SURFACES):
        for lane, cell in enumerate(STYLE):
            _, color, marker = STYLE[cell]
            ratios = [f["minutes"] / runs[(f["task_id"], cell)] for f in forecasts
                      if f["cell"] == cell and f["surface"] == surface and f["status"] == "completed"]
            y0 = -(row * 2.6 + lane * 1.0)
            ax.scatter(ratios, [y0 + rng.uniform(-0.3, 0.3) for _ in ratios], s=24, marker=marker, color=color,
                       alpha=0.55, lw=0)
            ax.text(1.01, y0, f"{st.mean(abs(math.log(r)) for r in ratios):.2f}", va="center",
                    family="monospace", transform=ax.get_yaxis_transform())
        ax.text(-0.01, -(row * 2.6 + 0.5), f"$\\bf{{{surface}}}$\n{labels[surface]}", ha="right", va="center",
                transform=ax.get_yaxis_transform())
        if row:
            ax.axhline(-(row * 2.6 - 0.8), color="#dddcd6", lw=0.8)
    ax.axvline(1, color="#888", ls="--", lw=0.9)
    ax.set_xscale("log", base=2)
    ax.set_xticks([0.5, 1, 2, 4, 8, 16])
    ax.set_xticklabels(["½×", "1×", "2×", "4×", "8×", "16×"])
    ax.minorticks_off()
    ax.set(xlim=(0.15, 48), yticks=[])
    ax.spines["left"].set_visible(False)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("forecast ÷ measured runtime (Opus circles, upper lane; Sol diamonds, lower lane); "
                  "right: mean |log ratio|")
    save(fig, "forecast_error_by_evidence_level.png")


def compress(score):
    """Axis transform that spreads the crowded top end (ticks 0, 50, 75, 90, 97, 100)."""
    return -np.log(1.01 - np.asarray(score, dtype=float) / 100)


def self_score_calibration(runs, scores, forks):
    """All self-assessments (faint) and each task's median (solid) against the official score."""
    rng = random.Random(0)
    fig, ax = plt.subplots(figsize=(9.6, 6.0))
    ticks = [0, 50, 75, 90, 97, 100]
    ax.plot(compress([0, 100]), compress([0, 100]), color="#aaa", ls="--", lw=0.9)
    for cell, (name, color, marker) in STYLE.items():
        for task in sorted({t for t, _ in runs}):
            official = scores[(task, cell)]
            selves = [k["self_score"] for k in forks
                      if k["task_id"] == task and k["cell"] == cell and k["status"] == "completed"]
            xs = [compress(official) + rng.uniform(-0.04, 0.04) for _ in selves]
            ax.scatter(xs, compress(selves), s=14, marker=marker, color=color, alpha=0.25, lw=0)
            med = st.median(selves)
            ax.scatter(compress(official), compress(med), s=70, marker=marker, color=color, edgecolor="white",
                       zorder=3, label=f"{name} (task median)" if task == min(t for t, _ in runs) else None)
            if abs(med - official) > 25 or official < 60:
                below = cell == "opus-claude"
                ax.annotate(task_name(task), (compress(official), compress(med)), xytext=(7, -11 if below else 5),
                            textcoords="offset points", fontsize=8, family="monospace", color="#555")
    ax.set_xticks(compress(ticks))
    ax.set_xticklabels([f"{t}%" for t in ticks])
    ax.set_yticks(compress(ticks))
    ax.set_yticklabels([str(t) for t in ticks])
    ax.set(xlim=compress([0, 100]), ylim=compress([0, 100]))
    ax.minorticks_off()
    ax.set_xlabel("official ProgramBench score")
    ax.set_ylabel("self_score (after the run, separate turn)")
    ax.legend(frameon=False, loc="lower right")
    save(fig, "self_score_calibration.png")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    runs, scores, forecasts, forks = load(sys.argv[1])
    forecast_vs_actual_by_task(runs, scores, forecasts)
    forecast_error_by_evidence_level(runs, forecasts)
    self_score_calibration(runs, scores, forks)
