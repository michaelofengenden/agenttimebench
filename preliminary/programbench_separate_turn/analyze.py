#!/usr/bin/env python3
"""Print the separate-turn ProgramBench numbers (20 tasks x 2 cells).

  python analyze.py DATA_DIR

DATA_DIR holds four CSVs (cell is opus-claude or sol-codex; other columns are ignored):
  runs.csv       one row per execution:   task_id, cell, wall_seconds (rows printed by `execute.py execution`)
  scores.csv     one row per execution:   task_id, cell, score (official tests passed / tests counted, percent,
                                          from `programbench info`)
  forecasts.csv  one row per forecast:    task_id, cell, surface (text | docs | probe), status, minutes
                                          (rows printed by `execute.py forecast`)
  forks.csv      one row per fork (both answers): task_id, cell, arm, status, minutes, self_score (`fork.py`)
status is `completed` when every requested answer parsed. Other rows (`parse_invalid`, or `interrupted`
when the operator stopped a fork) stay in the counts but are not used.

Forecast error = |log(forecast / measured wall minutes of that task's execution)|. A retrospective answer is
compared with its own parent execution. Self-score error = self_score minus the official score.
"""
import csv
import math
import statistics as st
import sys
from pathlib import Path

CELLS = {"opus-claude": "Opus 5 (Claude Code)", "sol-codex": "GPT-5.6 Sol (Codex)"}
SURFACES = {"text": "P1 text", "docs": "P2 docs", "probe": "P3 probe"}
ARMS = ("elapsed-oracle", "native", "context-only", "replay", "scrubbed")


def read(folder, name):
    return list(csv.DictReader(open(Path(folder) / name)))


def load(folder):
    runs = {(r["task_id"], r["cell"]): float(r["wall_seconds"]) / 60 for r in read(folder, "runs.csv")}
    scores = {(r["task_id"], r["cell"]): float(r["score"]) for r in read(folder, "scores.csv")}
    forecasts = [{**r, "minutes": int(r["minutes"]) if r["minutes"] else None} for r in read(folder, "forecasts.csv")]
    forks = [{**r, "minutes": int(r["minutes"]) if r["minutes"] else None,
              "self_score": int(r["self_score"]) if r["self_score"] else None} for r in read(folder, "forks.csv")]
    return runs, scores, forecasts, forks


def abs_log_error(estimate, actual):
    return abs(math.log(estimate / actual))


def task_name(task_id):
    return task_id.split("__")[1].split(".")[0]


def main(folder):
    runs, scores, forecasts, forks = load(folder)
    for cell, name in CELLS.items():
        wall = [m for (t, c), m in runs.items() if c == cell]
        sc = [s for (t, c), s in scores.items() if c == cell]
        print(f"\n{name}")
        print(f"  executions {len(wall)}: median runtime {st.median(wall):.1f} min (range {min(wall):.0f}-"
              f"{max(wall):.0f}); official score median {st.median(sc):.2f}%, mean {st.mean(sc):.1f}%")
        usable = [f for f in forecasts if f["cell"] == cell and f["status"] == "completed"]
        above = sum(f["minutes"] > runs[(f["task_id"], cell)] for f in usable)
        print(f"  forecasts {sum(f['cell'] == cell for f in forecasts)}, usable {len(usable)}, "
              f"above the measured runtime {above}")
        for surface, label in SURFACES.items():
            rows = [f for f in usable if f["surface"] == surface]
            errs = [abs_log_error(f["minutes"], runs[(f["task_id"], cell)]) for f in rows]
            print(f"    {label:9s} mean |log(forecast/actual)| {st.mean(errs):.2f}, median forecast "
                  f"{st.median(f['minutes'] for f in rows):.0f} min  (n = {len(errs)})")
        done = [k for k in forks if k["cell"] == cell and k["status"] == "completed"]
        print(f"  retrospective fork answers {sum(k['cell'] == cell for k in forks)}, usable {len(done)}")
        for arm in ARMS:
            rows = [k for k in done if k["arm"] == arm]
            errs = [abs_log_error(k["minutes"], runs[(k["task_id"], cell)]) for k in rows]
            gaps = [k["self_score"] - scores[(k["task_id"], cell)] for k in rows]
            print(f"    {arm:15s} mean |log(report/actual)| {st.mean(errs):.3f}  self-score error median "
                  f"{st.median(gaps):+.1f}  (n = {len(rows)})")
        gaps = [k["self_score"] - scores[(k["task_id"], cell)] for k in done]
        per_task = [st.median(k["self_score"] for k in done if k["task_id"] == t) - scores[(t, cell)]
                    for t in sorted({k["task_id"] for k in done})]
        print(f"  self-score error, all arms: median {st.median(gaps):+.1f} pts, below official "
              f"{sum(g < 0 for g in gaps)}/{len(gaps)}; median over per-task medians {st.median(per_task):+.1f}")
    print("\nPer-task median self-score vs official score (task: Opus, Sol)")
    for task in sorted({t for t, _ in runs}, key=task_name):
        cells = []
        for cell in CELLS:
            selves = [k["self_score"] for k in forks if k["task_id"] == task and k["cell"] == cell
                      and k["status"] == "completed"]
            cells.append(f"{scores[(task, cell)]:5.1f}% -> {st.median(selves):4.1f}")
        print(f"  {task_name(task):10s} {cells[0]}   {cells[1]}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
