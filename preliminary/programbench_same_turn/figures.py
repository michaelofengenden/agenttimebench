#!/usr/bin/env python3
"""Render the three same-turn figures.  python figures.py records.csv [out_dir (default: figures)]

  forecasts_vs_actual.png            Fig. programbench-same-turn-forecast
  self_score_overconfidence.png      Fig. same-turn-self-score
  runtime_correlation_baselines.png  Fig. runtime-correlation-baselines

records.csv: see analyze.py.
"""
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FixedLocator, NullFormatter, NullLocator  # noqa: E402

from analyze import SELF_SCORE_EXCLUDED, load, mean, pearson  # noqa: E402

OUT = Path("figures")
COLOR = {"claude": "#D2603C", "codex": "#141414"}
NAME = {"claude": "Claude", "codex": "Codex"}
INK, INK2, MUTED, GRID, AXIS, BAND = "#0b0b0b", "#3d3c39", "#6f6d66", "#e1e0d9", "#c3c2b7", "#8d8b84"
plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold",
    "axes.labelsize": 9.5, "axes.labelcolor": INK2, "axes.edgecolor": AXIS, "axes.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "axes.axisbelow": True,
    "grid.color": GRID, "grid.linewidth": 0.6, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
    "xtick.color": AXIS, "ytick.color": AXIS, "xtick.labelcolor": "#52514e", "ytick.labelcolor": "#52514e",
    "savefig.bbox": "tight", "savefig.facecolor": "white",
})


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, dpi=400)
    plt.close(fig)
    print("wrote", OUT / name)


def forecasts_vs_actual(rows):
    fig, ax = plt.subplots(figsize=(3.45, 3.62))
    lims = (4, 600)
    ax.fill_between(lims, [v / 1.5 for v in lims], [v * 1.5 for v in lims], color=BAND, alpha=0.10, lw=0)
    ax.plot(lims, lims, color=INK2, lw=1.0, ls=(0, (4, 3)))
    for agent, z in (("codex", 3), ("claude", 4)):
        data = [r for r in rows if r["agent"] == agent]
        ax.scatter([r["actual_minutes"] for r in data], [r["estimate_minutes"] for r in data], s=16, alpha=0.55,
                   color=COLOR[agent], edgecolors="white", linewidths=0.4, zorder=z)
        est, act = [r["estimate_minutes"] for r in data], [r["actual_minutes"] for r in data]
        r = pearson(est, act)
        within = sum(2 / 3 <= e / a <= 1.5 for e, a in zip(est, act))
        x, y, ha, va = (0.035, 0.965, "left", "top") if agent == "codex" else (0.965, 0.035, "right", "bottom")
        ax.text(x, y, f"$\\bf{{{NAME[agent]}}}$  {mean(est) / mean(act):.2f}× actual\n"
                      f"r = {r:.2f}   R² = {r * r:.2f}\n{within}/{len(data)} within 1.5×",
                transform=ax.transAxes, ha=ha, va=va, fontsize=8, color=INK, linespacing=1.45, zorder=7,
                bbox=dict(boxstyle="round,pad=0.42", facecolor="white", edgecolor=COLOR[agent], lw=1.2))
    ax.set(xscale="log", yscale="log", xlim=lims, ylim=lims, aspect="equal")
    ticks = [5, 10, 30, 100, 300]
    for axis in (ax.xaxis, ax.yaxis):
        axis.set_major_locator(FixedLocator(ticks))
        axis.set_minor_locator(NullLocator())
        axis.set_minor_formatter(NullFormatter())
    ax.set_xticklabels(map(str, ticks))
    ax.set_yticklabels(map(str, ticks))
    ax.text(420, 462, "1:1", fontsize=7.5, color=INK2, rotation=45, rotation_mode="anchor", ha="center")
    ax.text(8, 8 / 1.5, "within 1.5×", fontsize=7.5, color=MUTED, rotation=45, rotation_mode="anchor", ha="center")
    ax.set_xlabel("Actual runtime (min, log)")
    ax.set_ylabel("Estimated beforehand (min, log)")
    save(fig, "forecasts_vs_actual.png")


def self_score(rows):
    fig, axes = plt.subplots(1, 2, figsize=(5.6, 3.05), sharey=True)
    for ax, agent in zip(axes, ("claude", "codex")):
        data = [r for r in rows if r["agent"] == agent and r["self_score"] is not None
                and r["instance_id"] not in SELF_SCORE_EXCLUDED]
        xs, ys = [r["test_pass_score"] for r in data], [r["self_score"] for r in data]
        ax.plot([0, 100], [0, 100], color=INK2, lw=1.0, ls=(0, (4, 3)))
        ax.text(35, 31.5, "1:1", fontsize=7.5, color=MUTED, rotation=45, rotation_mode="anchor", ha="center", va="top")
        ax.scatter(xs, ys, s=13, alpha=0.4, color=COLOR[agent], edgecolors="white", linewidths=0.35)
        bx, by = [], []  # 20-point bucket means; 100 joins the top bucket; buckets need >= 3 runs
        for lo in range(0, 100, 20):
            bucket = [s for a, s in zip(xs, ys) if lo <= a < lo + 20 or (lo == 80 and a == 100)]
            if len(bucket) >= 3:
                bx.append(lo + 10)
                by.append(mean(bucket))
        ax.plot(bx, by, color=COLOR[agent], lw=2.2, marker="o", ms=5.5, mec="white", mew=0.8, zorder=5)
        ax.text(0.96, 0.06, f"r = {pearson(xs, ys):.2f} · n = {len(data)}", transform=ax.transAxes, fontsize=8,
                ha="right", bbox=dict(boxstyle="round,pad=0.38", facecolor="white", edgecolor=AXIS, lw=0.8))
        bias = mean([s - a for a, s in zip(xs, ys)])
        ax.set_title(f"{NAME[agent]}\n+{bias:.0f} pts overconfident", fontsize=9, linespacing=1.35)
        ax.set(xlim=(0, 100), ylim=(0, 100), xticks=range(0, 101, 25), yticks=range(0, 101, 25), aspect="equal")
        ax.set_xlabel("Actual hidden-test score (%)")
    axes[0].set_ylabel("Self-score before tests")
    fig.subplots_adjust(wspace=0.24)
    save(fig, "self_score_overconfidence.png")


def correlation_baselines(rows):
    predictors = [("Before-run estimate", "estimate_minutes"), ("After-run lived report", "perceived_minutes"),
                  ("Visible event count", "visible_event_count"), ("Full visible characters", "full_visible_chars"),
                  ("Raw log bytes", "raw_log_bytes"), ("Compacted grader input", "compacted_chars")]
    fig, axes = plt.subplots(1, 2, figsize=(6.15, 3.25), sharex=True, sharey=True)
    for ax, agent in zip(axes, ("claude", "codex")):
        data = [r for r in rows if r["agent"] == agent]
        log_actual = [math.log(r["actual_minutes"]) for r in data]
        for y, (_, key) in enumerate(predictors):
            value = pearson([math.log(r[key]) for r in data], log_actual)
            report = y < 2
            ax.hlines(y, 0, value, color=GRID, lw=1.6)
            ax.scatter([value], [y], marker="sD"[y] if report else "o", s=42 if report else 36,
                       color=COLOR[agent] if report else MUTED, edgecolors="white", linewidths=0.7, zorder=4)
            ax.text(value + 0.018, y, f"{value:.2f}", va="center", fontsize=8, color=INK)
        ax.set_title(f"{NAME[agent]}  ·  {len(data)} runs", fontsize=9.3)
        ax.set(xlim=(0, 1.02), xticks=[0, 0.25, 0.5, 0.75, 1], yticks=range(len(predictors)))
        ax.set_xticklabels(["0", "0.25", "0.50", "0.75", "1"])
        ax.set_yticklabels([label for label, _ in predictors], fontsize=8.1)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.axhline(1.5, color=AXIS, lw=0.8)
        ax.grid(False)
        ax.grid(True, axis="x")
    axes[0].invert_yaxis()
    fig.supxlabel("Correlation with true runtime · Pearson r on logs", y=0.035, fontsize=9.5, color=INK2)
    fig.suptitle("How well estimates and transcript-length baselines track true runtime", y=1.01,
                 fontsize=10.5, weight="bold")
    fig.subplots_adjust(wspace=0.14, bottom=0.18, top=0.80)
    save(fig, "runtime_correlation_baselines.png")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else OUT
    records = load(sys.argv[1])
    forecasts_vs_actual(records)
    self_score(records)
    correlation_baselines(records)
