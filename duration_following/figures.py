"""Render Figures 1, 2 and 3 of the paper as PDFs at their printed size.

    python figures.py RUNS SCORES LABELS [--out DIR]   # inputs as in analyze.py; DIR defaults to figures/

Both time axes use log(1 + t / 30 s): linear below about 30 s, logarithmic above, so equal durations lie on the
diagonal. Shading spans 0.8 to 1.25 times the request and dotted lines mark tenfold deviations.
"""
import argparse
import logging
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.patheffects as pe
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.lines import Line2D
from matplotlib.patches import Rectangle
import numpy as np

import analyze as A
import metrics as M

MIN, HOUR, LIMIT = 60, 3600, 72 * 3600  # seconds
FABLE, SOL, ASTRA = "claude-fable-5-1", "gpt-5.6-sol", "gpt-6-astra"
NAME = {FABLE: "Fable 5.1", SOL: "GPT-5.6 Sol", ASTRA: "GPT-6 Astra"}
POINT = {FABLE: "#d97757", SOL: "#2f7ea2", ASTRA: "#2b2b2b"}   # Figure 1 draws Astra in #141414
TREND = {FABLE: "#b34e2c", SOL: "#2f7ea2", ASTRA: "#2b2b2b"}
INK, BODY, MUTED, AXIS, GUIDE, EQUAL, BAND = "#161616", "#3d3d3d", "#6f6f6b", "#bdbdb8", "#cfcfcb", "#7b7b77", "#eeede9"


def T(s):
    return np.log1p(np.asarray(s, dtype=float) / 30)


def tick_label(s):
    return "0" if s == 0 else f"{s / MIN:g} min" if s < HOUR else f"{s / HOUR:g} h"


def setup():
    logging.getLogger("fontTools").setLevel(logging.ERROR)  # quiet font-subsetting notes on some system fonts
    names = {f.name for f in font_manager.fontManager.ttflist}
    family = next((f for f in ("Helvetica", "Arial", "Liberation Sans") if f in names), "DejaVu Sans")
    plt.rcParams.update({"font.family": family, "pdf.fonttype": 42, "axes.unicode_minus": False,
                         "savefig.facecolor": "white"})


def guides(ax, hero=False):
    """On-time band, tenfold guides and the equal-duration line (Figure 1 uses slightly lighter dashes)."""
    t = np.concatenate([[0], np.geomspace(0.001 if hero else 1, LIMIT, 900)])
    space = 2.5 if hero else 2.4
    ax.set_xlim(0, T(LIMIT)), ax.set_ylim(0, T(LIMIT))
    ax.fill_between(T(t), T(t * M.LO), T(np.minimum(t * M.HI, LIMIT)), color=BAND, linewidth=0, zorder=0)
    for r in (10, 0.1):
        keep = t * r <= LIMIT
        ax.plot(T(t[keep]), T(t[keep] * r), color="#c9c9c7" if hero else GUIDE, linewidth=0.55,
                linestyle=(0, (1.2, space)), zorder=1)
    ax.plot(T(t), T(t), color=EQUAL, linewidth=0.75 if hero else 0.8, linestyle=(0, (3, space)), zorder=2)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS), ax.spines[side].set_linewidth(0.65)


def along_diagonal(ax, text, at, offset, size):
    a, b = ax.transData.transform((T(at / 1.2), T(at / 1.2))), ax.transData.transform((T(at * 1.2), T(at * 1.2)))
    angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
    ax.annotate(text, (T(at), T(at)), xytext=(-math.sin(math.radians(angle)) * offset, math.cos(math.radians(angle)) * offset),
                textcoords="offset points", rotation=angle, rotation_mode="anchor", ha="center", va="center",
                color=MUTED, fontsize=size, zorder=7)


def figure1(runs):
    """Hero: Fable 5.1 and GPT-6 Astra, one shared time scale, pooled log-log fits."""
    width, left, right, bottom, top = 198, 39, 10, 22, 15
    side = width - left - right
    fig = plt.figure(figsize=(width / 72, (side + bottom + top) / 72))
    ax = fig.add_axes([left / width, bottom / (side + bottom + top), side / width, side / (side + bottom + top)])
    guides(ax, hero=True)
    tt = np.concatenate([[0], np.geomspace(1, 60 * HOUR, 400)])
    style = {FABLE: ("o", POINT[FABLE], 7.5, 0.57, 3), ASTRA: ("D", "#141414", 6.0, 0.73, 4)}
    for m, (marker, color, area, alpha, z) in style.items():
        rs = [r for r in runs if r["agent"] == m]
        b, a = M.loglog_fit(rs)
        trend = TREND[m] if m == FABLE else "#141414"
        ax.plot(T(tt), T(np.exp(a) * tt ** b), color=trend, linewidth=1.15, zorder=z - 0.5, solid_capstyle="round")
        ax.scatter(T([r["requested_s"] for r in rs]), T([r["worked_s"] for r in rs]), marker=marker, s=area,
                   facecolor=color, edgecolor="white", linewidths=0.15, alpha=alpha, zorder=z)
    ticks = [MIN, 15 * MIN, HOUR, 4 * HOUR, 24 * HOUR, LIMIT]
    ax.set_xticks(T([0] + ticks), [tick_label(v) for v in [0] + ticks])
    ax.get_xticklabels()[0].set_ha("right")
    ax.set_yticks(T(ticks), [tick_label(v) for v in ticks])
    ax.tick_params(colors=AXIS, labelcolor="#4c4c49", labelsize=6.9, length=2.2, width=0.6, pad=1.8)
    ax.set_xlabel("Requested work duration", color="#242423", fontsize=7.8, labelpad=3.8)
    ax.set_ylabel("Elapsed runtime", color="#242423", fontsize=7.8, labelpad=4)
    handles = [Line2D([], [], color=TREND[m] if m == FABLE else "#141414", linewidth=1.15, marker=style[m][0],
                      markersize=3.9 if m == FABLE else 3.4, markerfacecolor=style[m][1], markeredgecolor="white",
                      markeredgewidth=0.35, label=NAME[m]) for m in style]
    ax.legend(handles=handles, loc="lower left", bbox_to_anchor=(0, 1.025), ncol=2, frameon=False, borderaxespad=0,
              fontsize=7.5, handlelength=1.3, columnspacing=1, handletextpad=0.45)
    fig.canvas.draw()
    along_diagonal(ax, "Equal duration", 14 * HOUR, 11, 6.7)
    return fig


def figure2(runs):
    """Every run, one panel per agent, with the agent's on-time share and deviation (Table 3) in the corner."""
    width, left, right, gap, top, bottom = 396.0, 37.0, 9.0, 13.0, 13.0, 24.0
    w = (width - left - right - 2 * gap) / 3
    height = w + top + bottom
    fig = plt.figure(figsize=(width / 72, height / 72))
    ticks = [MIN, 15 * MIN, HOUR, 4 * HOUR, 24 * HOUR, LIMIT]
    for i, m in enumerate(A.AGENTS):
        rs = [r for r in runs if r["agent"] == m]
        ax = fig.add_axes([(left + i * (w + gap)) / width, bottom / height, w / width, w / height])
        guides(ax)
        ax.scatter(T([r["requested_s"] for r in rs]), T([r["worked_s"] for r in rs]), s=5.0, marker="o",
                   facecolor=POINT[m], edgecolor="white", linewidths=0.15, alpha=0.5, zorder=3, clip_on=False)
        b, a = M.loglog_fit(rs)
        req = [r["requested_s"] for r in rs]
        tt = np.geomspace(min(req), max(req), 300)  # the fit is drawn only over the requests
        ax.plot(T(tt), T(np.exp(a) * tt ** b), color=TREND[m], linewidth=1.15, zorder=4, solid_capstyle="round",
                path_effects=[pe.Stroke(linewidth=2.1, foreground="white"), pe.Normal()])
        ax.set_xticks(T([0] + ticks), [tick_label(v) if v != 24 * HOUR else "" for v in [0] + ticks])
        ax.get_xticklabels()[0].set_ha("right")
        ax.set_yticks(T(ticks), [tick_label(v) for v in ticks] if i == 0 else [])
        ax.tick_params(colors=AXIS, labelcolor=BODY, labelsize=6.4, length=2.2, width=0.6, pad=1.8)
        ax.annotate(NAME[m], (0, 1), xycoords="axes fraction", xytext=(0, 3), textcoords="offset points",
                    ha="left", va="bottom", fontsize=7.9, fontweight="bold", color=INK)
        for text, y, size, weight in ((f"{100 * M.shares(rs)[0]:.0f}%", 21, 10.5, "bold"), ("on time", 12, 6.4, "normal"),
                                      (f"{M.deviation(rs):.2f}× deviation", 3.5, 6.4, "normal")):
            ax.annotate(text, (1, 0), xycoords="axes fraction", xytext=(-1, y), textcoords="offset points",
                        ha="right", va="bottom", fontsize=size, fontweight=weight, color=INK if weight == "bold" else BODY)
        if i == 0:
            fig.canvas.draw()
            along_diagonal(ax, "equal duration", 28 * HOUR, 7, 5.8)
    fig.text((left + 1.5 * w + gap) / width, 3 / height, "Requested work duration", ha="center", va="bottom", fontsize=7.6, color=INK)
    fig.text(3 / width, (bottom + w / 2) / height, "Elapsed runtime", ha="left", va="center", rotation=90, fontsize=7.6, color=INK)
    return fig


def mix(color, share):
    return matplotlib.colors.to_hex([share * c + (1 - share) for c in matplotlib.colors.to_rgb(color)])


def text_color(color):
    """White on dark fills, ink on light ones, whichever has more contrast (WCAG relative luminance)."""
    lum = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    y = sum(k * lum(c) for k, c in zip((0.2126, 0.7152, 0.0722), matplotlib.colors.to_rgb(color)))
    return "white" if 1.05 / (y + 0.05) >= (y + 0.05) / 0.0605 else "#0b0b0b"


def figure3(runs, scores, labels):
    """(a) when runs ended, (b) what filled the time, (c) whether 16x more time changed the score."""
    gray = {"early": "#e2dfd8", "late": "#aaa69d"}
    fill = lambda m, role: gray[role] if role in gray else mix(POINT[m], {"key": 1.0, "mid": 0.55, "light": 0.28}[role])
    b, c = A.transcripts(runs, labels), A.score_change(scores)
    stretch = A.details(runs, scores)["longest_over_shortest_request_median"]
    panels = [
        ("a", "When did the run end?", [("Early", "early"), ("On time", "key"), ("Late", "late")],
         {m: [M.shares([r for r in runs if r["agent"] == m])[i] for i in (1, 0, 2)] for m in A.AGENTS},
         {m: sum(r["agent"] == m for r in runs) for m in A.AGENTS}),
        ("b", "What filled the time?",
         [("Early", "early"), ("Working", "key"), ("Re-checked", "mid"), ("Slept", "light"), ("Late", "late")],
         {m: [b[m]["counts"][k] / b[m]["n"] for k in ("early", "working", "rechecked", "slept", "late")] for m in A.AGENTS},
         {m: b[m]["n"] for m in A.AGENTS}),
        ("c", f"Did {stretch:.0f}× more time help?", [("Lower", "late"), ("Same", "early"), ("Higher", "key")],
         {m: [c[m][k] / c[m]["tasks"] for k in ("lower", "same", "higher")] for m in A.AGENTS},
         {m: c[m]["tasks"] for m in A.AGENTS}),
    ]
    width, labels_w, n_w, gap, bar_h, pitch = 396, 47, 14, 9, 10.5, 15
    bar_ws, title_h, legend_h, top = (95, 111, 83), 10.5, 11, 2
    height = top + title_h + legend_h + pitch * 2 + bar_h + 1
    fig = plt.figure(figsize=(width / 72, height / 72))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width), ax.set_ylim(height, 0), ax.axis("off")  # y grows downward, in points
    renderer = fig.canvas.get_renderer()
    def text_w(t):
        (x0, _), (x1, _) = ax.transData.inverted().transform(t.get_window_extent(renderer).get_points())
        return x1 - x0

    row_y = {m: top + title_h + legend_h + i * pitch for i, m in enumerate(A.AGENTS)}
    for m in A.AGENTS:
        ax.text(labels_w - 5, row_y[m] + bar_h / 2, NAME[m], ha="right", va="center_baseline", fontsize=7.4,
                color=TREND[m])
    x0 = labels_w
    for (letter, title, cats, shares, ns), bar_w in zip(panels, bar_ws):
        ax.text(x0, top, letter, ha="left", va="top", fontsize=7.6, fontweight="bold", color=INK)
        ax.text(x0 + 7.5, top, title, ha="left", va="top", fontsize=7.6, fontweight="bold", color=INK)
        lx, ly = x0, top + title_h + 1.5
        for name, role in cats:
            if letter == "b" and role in gray:
                continue  # (b) uses the grays of (a) for early and late
            shades = [fill(m, role) for m in A.AGENTS]
            stripes = shades[:1] if len(set(shades)) == 1 else shades  # one stripe per agent where colors differ
            sw = 5 if len(stripes) == 1 else 2.2
            for k, shade in enumerate(stripes):
                ax.add_patch(Rectangle((lx + sw * k, ly), sw, 5, facecolor=shade, edgecolor="none"))
            label = ax.text(lx + sw * len(stripes) + 1.8, ly + 2.5, name, ha="left", va="center_baseline",
                            fontsize=6.4, color="#52514e")
            lx += sw * len(stripes) + 1.8 + text_w(label) + 5.5
        for m in A.AGENTS:
            left = x0
            for (name, role), share in zip(cats, shares[m]):
                w = share * bar_w
                if w > 0:
                    color = fill(m, role)
                    ax.add_patch(Rectangle((left, row_y[m]), w, bar_h, facecolor=color, edgecolor="white", linewidth=1.0))
                    label = ax.text(left + w / 2, row_y[m] + bar_h / 2, f"{round(share * 100)}%", ha="center",
                                    va="center_baseline", fontsize=6.6, color=text_color(color))
                    if text_w(label) + 2.4 > w:  # keep a percentage only where it fits inside its segment
                        label.remove()
                left += w
            ax.text(x0 + bar_w + n_w - 1, row_y[m] + bar_h / 2, f"{ns[m]}", ha="right", va="center_baseline",
                    fontsize=6.2, color="#898781")
        x0 += bar_w + n_w + gap
    return fig


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ("runs", "scores", "labels"):
        ap.add_argument(name, help=f"{name.upper()} csv, as in analyze.py")
    ap.add_argument("--out", default="figures", help="output directory")
    args = ap.parse_args(argv)
    runs = A.load_runs(args.runs)
    scores, labels = A.load_scores(args.scores, runs), A.load_labels(args.labels)
    setup()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for stem, fig in (("agenttime_duration_following_hero_equal_axes", figure1(runs)),       # Figure 1
                      ("agenttime_duration_following_three_agents", figure2(runs)),          # Figure 2
                      ("duration_following_breakdown", figure3(runs, scores, labels))):     # Figure 3
        fig.savefig(out / f"{stem}.pdf")
        plt.close(fig)
        print("wrote", out / f"{stem}.pdf")


if __name__ == "__main__":
    main()
