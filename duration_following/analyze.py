"""Print Table 3 (upper block), the Section 4.1 numbers, Figure 3 (a, b, c) and Appendix A Table 5 with its suite row.

    python analyze.py RUNS SCORES [LABELS] [--json OUT]

Inputs are CSV files with a header row (other columns are ignored):
  RUNS    one row per run: run_id, agent (claude-fable-5-1, gpt-5.6-sol, gpt-6-astra), benchmark (a slug of
          BENCHMARKS in analyze.py), task (the benchmark's task id), request (shortest, middle, longest), requested_s
          (whole seconds) and worked_s (the measured runtime in seconds).
  SCORES  one row per run: run_id; score, the native score on the paper's scale (percent; ALE-Bench performance;
          YC-Bench final funds in USD), empty if the run was not graded; score_raw, the native value compared within a
          task in Figure 3c (0-1; PPTArena (IF + VQ) / 10; ALE-Bench raw score; YC-Bench funds), empty with score;
          no_output, true if the run left nothing to grade and scores 0.
  LABELS  one row per run with a timestamped transcript: run_id, label (a LABELING_RUBRIC.md label; empty if unread).
          Optional: without it the Figure 3b counts are skipped.
Bootstrap CIs resample tasks in the order they first appear: in RUNS for Table 3 and Section 4.1, in SCORES for the
suite row.
"""
import argparse
import collections
import csv
import json
import random

import numpy as np
from scipy.stats import spearmanr

import metrics as M

AGENTS = {"claude-fable-5-1": "Fable 5.1 (Claude Code)", "gpt-5.6-sol": "GPT-5.6 Sol (Codex)",
          "gpt-6-astra": "GPT-6 Astra (Codex)"}
ARMS = ("shortest", "middle", "longest")
QUESTIONS = {"gpqa-diamond", "humanitys-last-exam"}
OPEN_ENDED = {"sakana-ale-bench", "paperbench", "programbench", "metr-public-tasks", "posttrainbench"}
BINARY = {"gpqa-diamond", "humanitys-last-exam", "appworld", "deepswe", "terminal-bench", "core-bench"}
RAW = {"sakana-ale-bench", "yc-bench"}  # no common range across tasks: only higher, lower or same within a task
TIE = 0.01  # bounded non-binary scores: a change smaller than this on the 0-1 scale counts as the same score
BENCHMARKS = {  # Table 5 order
    "gpqa-diamond": "GPQA Diamond", "humanitys-last-exam": "Humanity's Last Exam", "appworld": "AppWorld",
    "assistantbench": "AssistantBench", "osworld": "OSWorld 2.0", "terminal-bench": "Terminal-Bench 4.0",
    "programbench": "ProgramBench", "agents-last-exam": "Agents' Last Exam", "core-bench": "CORE-Bench v1.1",
    "deepswe": "DeepSWE v1.1", "tua-bench": "TUA-Bench", "pptarena": "PPTArena", "wildclawbench": "WildClawBench",
    "paperbench": "PaperBench", "metr-public-tasks": "METR public tasks", "posttrainbench": "PostTrainBench v1.1",
    "sakana-ale-bench": "Sakana ALE-Bench", "yc-bench": "YC-Bench"}
# Figure 3b groups of the reader labels (LABELING_RUBRIC.md); early and late always come from the clock.
GROUP = {"WORKED_THROUGH": "working", "WAITED_ON_JOB": "working", "FINISHED_THEN_RECHECKED": "rechecked",
         "FINISHED_THEN_SLEPT": "slept", "MIXED_OR_UNCLEAR": "unclear"}


def read_csv(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def load_runs(path):
    """RUNS rows in file order, with typed times."""
    rows = read_csv(path)
    for r in rows:
        r["requested_s"], r["worked_s"] = int(r["requested_s"]), float(r["worked_s"])
    return rows


def load_scores(path, runs):
    """SCORES rows in file order, each joined to its run."""
    by_id = {r["run_id"]: r for r in runs}
    out = []
    for s in read_csv(path):
        score = float(s["score"]) if s["score"] else None
        out.append({**by_id[s["run_id"]], "score": score, "scored": score is not None,
                    "score_raw": float(s["score_raw"]) if s["score_raw"] else None, "no_output": s["no_output"] == "true"})
    return out


def load_labels(path):
    """run_id -> reader label for every run with a timestamped transcript ('' if no reader labelled it)."""
    return {r["run_id"]: r["label"] for r in read_csv(path)}


def by_request(rs):
    out = {}
    for k in ARMS:
        sub = [r for r in rs if r["request"] == k]
        on, early, late = M.shares(sub)
        out[k] = {"n": len(sub), "on_time": on, "early": early, "late": late,
                  "median_ratio": float(np.median([M.ratio(r) for r in sub]))}
    return out


def duration_following(runs):
    """Table 3 upper block, Figure 3a and the Section 4.1 numbers."""
    out = {}
    for a in AGENTS:
        rs = [r for r in runs if r["agent"] == a]
        on, early, late = M.shares(rs)
        ws = M.within_slope(rs)
        out[a] = {"runs": len(rs), "tasks": len({(r["benchmark"], r["task"]) for r in rs}),
                  "on_time": on, "early": early, "late": late,
                  "deviation": M.deviation(rs), "deviation_ci": M.task_bootstrap(rs, M.deviation, 1000),
                  "pooled_slope": M.pooled_slope(rs), "pooled_slope_ci": M.task_bootstrap(rs, M.pooled_slope, 2000),
                  "within_slope": ws, "within_slope_ci": M.task_bootstrap(rs, M.within_slope, 2000),
                  "within_tasks": len(M.within_parts(rs)), "at_16x": 16 ** ws, "by_request": by_request(rs)}
    return out


def cutoff_counts(runs):
    """Runs per agent that reached the hidden cutoff, twice the task's longest request (such runs were stopped)."""
    longest = collections.defaultdict(int)
    for r in runs:
        key = (r["benchmark"], r["task"])
        longest[key] = max(longest[key], r["requested_s"])
    hit = collections.Counter(r["agent"] for r in runs if r["worked_s"] >= 2 * longest[(r["benchmark"], r["task"])])
    return {a: hit[a] for a in AGENTS}


def details(runs, scores):
    """The other Section 4.1 numbers: open-ended tasks, examples, Astra's misses, cutoff runs."""
    fable = [r for r in runs if r["agent"] == "claude-fable-5-1"]
    oe = [r for r in fable if r["benchmark"] in OPEN_ENDED]
    rest = [r for r in fable if r["benchmark"] not in OPEN_ENDED]
    astra = [r for r in runs if r["agent"] == "gpt-6-astra"]
    miss = [r for r in astra if not M.LO <= M.ratio(r) <= M.HI]
    late = [r for r in miss if M.ratio(r) > M.HI]
    late_short = [M.ratio(r) for r in late if r["request"] == "shortest"]
    requests = collections.defaultdict(set)
    for r in runs:
        requests[(r["benchmark"], r["task"])].add(r["requested_s"])
    stretch = [max(v) / min(v) for v in requests.values() if len(v) > 1]
    osworld = {r["request"]: r for r in scores
               if r["agent"] == "claude-fable-5-1" and r["benchmark"] == "osworld" and r["task"] == "038"}
    return {
        "fable_open_ended": {"slope": M.within_slope(oe), "tasks": len(M.within_parts(oe)),
                             "other_slope": M.within_slope(rest), "other_tasks": len(M.within_parts(rest)),
                             "other_benchmarks": len({r["benchmark"] for r in rest})},
        "fable_ahc016": sorted((r["requested_s"] / 60, round(r["worked_s"] / 60, 2)) for r in fable
                               if r["benchmark"] == "sakana-ale-bench" and r["task"] == "ahc016"),
        "fable_osworld_038": [(osworld[k]["requested_s"] / 60, osworld[k]["score"]) for k in ARMS if k in osworld],
        "astra_misses": {"n": len(miss), "late": len(late), "early": len(miss) - len(late),
                         "late_shortest": len(late_short), "median_late_shortest": float(np.median(late_short))},
        "cutoff": cutoff_counts(runs),
        "longest_over_shortest_request_median": float(np.median(stretch)),
    }


def transcripts(runs, labels):
    """Figure 3b (every run with a timestamped transcript) and the on-time agentic runs of Section 4.1."""
    by_id = {r["run_id"]: r for r in runs}
    out = {}
    for a in AGENTS:
        counts, agentic = collections.Counter(), collections.Counter()
        for rid, label in labels.items():
            r = by_id[rid]
            if r["agent"] != a:
                continue
            q = M.ratio(r)
            if q < M.LO:
                counts["early"] += 1
            elif q > M.HI:
                counts["late"] += 1
            else:
                group = GROUP[label] if label else "unread"
                counts[group] += 1
                if r["benchmark"] not in QUESTIONS:
                    agentic[group] += 1
        n = sum(counts[k] for k in ("early", "working", "rechecked", "slept", "late"))
        out[a] = {"n": n, "counts": {k: counts[k] for k in ("early", "working", "rechecked", "slept", "late")},
                  "unclear": counts["unclear"], "unread": counts["unread"],
                  "questions": sum(1 for rid in labels if by_id[rid]["agent"] == a and by_id[rid]["benchmark"] in QUESTIONS),
                  "on_time_agentic": {k: agentic[k] for k in ("working", "rechecked", "slept", "unclear")}}
    return out


def score_change(scores):
    """Figure 3c: native score at each task's longest request against its shortest."""
    ale = collections.defaultdict(list)
    for r in scores:
        if r["benchmark"] == "sakana-ale-bench" and r["scored"]:
            ale[r["task"]].append((r["score_raw"], r["score"]))
    # ALE-Bench raw scores are maximized or minimized per problem: the sign of Spearman(raw, performance) says which.
    direction = {("sakana-ale-bench", t): -1 if len(v) >= 3 and spearmanr(*zip(*v)).statistic < 0 else 1
                 for t, v in ale.items()}
    out = {}
    for a in AGENTS:
        cell = collections.defaultdict(dict)
        for r in scores:
            if r["agent"] == a:
                cell[(r["benchmark"], r["task"])][r["request"]] = r
        c = collections.Counter()
        for (b, t), arms in cell.items():
            s, l = arms.get("shortest"), arms.get("longest")
            if not s or not l or not s["scored"] or not l["scored"]:
                continue
            d = (l["score_raw"] - s["score_raw"]) * direction.get((b, t), 1)
            same = abs(d) < TIE if b not in BINARY | RAW else d == 0
            c["same" if same else "higher" if d > 0 else "lower"] += 1
        out[a] = {"tasks": sum(c.values()), **{k: c[k] for k in ("higher", "same", "lower")}}
    out["minimized_ale_tasks"] = sorted(t for (_, t), d in direction.items() if d < 0)
    return out


def native_scores(scores):
    """Table 5: mean native score per benchmark and agent, pooled over the three requests, and the suite row."""
    table = {}
    for b in BENCHMARKS:
        table[b] = {}
        for a in AGENTS:
            rs = [r for r in scores if r["agent"] == a and r["benchmark"] == b]
            vals = [r["score"] for r in rs if r["scored"]]
            table[b][a] = {"mean": round(sum(vals) / len(vals), 1) if vals else None, "scored": len(vals), "runs": len(rs)}
    # Suite: (benchmark, task, request) cells scored for all three agents; benchmarks with >= 3 such cells weigh equally.
    val = {}
    for r in scores:
        if r["benchmark"] not in RAW and r["scored"]:
            val[(r["benchmark"], r["task"], r["request"], r["agent"])] = r["score"]
    cells = collections.defaultdict(dict)
    for (b, t, k, a), v in val.items():
        cells[(b, t, k)][a] = v
    byfam = collections.defaultdict(list)
    for (b, t, k), d in cells.items():
        if all(a in d for a in AGENTS):
            byfam[b].append((t, d))
    # benchmark means are rounded to 0.1 before they are averaged
    means = {b: {a: round(sum(d[a] for _, d in cl) / len(cl), 1) for a in AGENTS} for b, cl in sorted(byfam.items())}
    fams = [b for b in means if len(byfam[b]) >= 3]
    rng = random.Random(1)
    suite = {}
    for a in AGENTS:
        boots = []
        for _ in range(2000):  # resample tasks within each benchmark
            acc = []
            for b in fams:
                by_task = collections.defaultdict(list)
                for t, d in byfam[b]:
                    by_task[t].append(d[a])
                ts = list(by_task)
                xs = [x for t in [rng.choice(ts) for _ in ts] for x in by_task[t]]
                acc.append(sum(xs) / len(xs))
            boots.append(sum(acc) / len(acc))
        boots.sort()
        suite[a] = {"mean": round(sum(means[b][a] for b in fams) / len(fams), 1),
                    "ci": [round(boots[49], 1), round(boots[1949], 1)]}
    no_output = collections.Counter(r["benchmark"] for r in scores if r["no_output"])
    graded = {"runs": len(scores), "scored": sum(r["scored"] for r in scores), "no_output": sum(no_output.values()),
              "no_output_by_benchmark": dict(sorted(no_output.items(), key=lambda x: (-x[1], x[0])))}  # ties by name
    return {"benchmarks": table, "suite": suite, "suite_benchmarks": fams,
            "suite_cells": sum(len(byfam[b]) for b in fams), "graded": graded}


def compute(runs, scores, labels=None):
    """All numbers; transcripts (Figure 3b) is None without labels."""
    return {"duration_following": duration_following(runs), "details": details(runs, scores),
            "transcripts": None if labels is None else transcripts(runs, labels),
            "score_change": score_change(scores), "native_scores": native_scores(scores)}


def pct(x):
    return f"{100 * x:.0f}%"


def report(n):
    t, d, b, c, s = (n[k] for k in ("duration_following", "details", "transcripts", "score_change", "native_scores"))
    print(f"Table 3 (upper block): {sum(v['runs'] for v in t.values())} runs")
    print(f"  {'Agent':26s} {'Runs':>5s} {'On time':>8s} {'Early':>6s} {'Late':>5s}  Deviation            Slope")
    for a, v in t.items():
        dci, sci = v["deviation_ci"], v["pooled_slope_ci"]
        print(f"  {AGENTS[a]:26s} {v['runs']:5d} {pct(v['on_time']):>8s} {pct(v['early']):>6s} {pct(v['late']):>5s}  "
              f"{v['deviation']:.2f}x [{dci[0]:.2f}, {dci[1]:.2f}]  {v['pooled_slope']:.2f} [{sci[0]:.2f}, {sci[1]:.2f}]")
    print("\nSection 4.1")
    for a, v in t.items():
        print(f"  {AGENTS[a]}: within-task slope {v['within_slope']:.2f} [{v['within_slope_ci'][0]:.2f}, "
              f"{v['within_slope_ci'][1]:.2f}] over {v['within_tasks']} tasks; 16x the request -> {v['at_16x']:.1f}x the runtime")
        for k, x in v["by_request"].items():
            print(f"    {k:8s} n {x['n']:3d}: on time {pct(x['on_time']):>4s}, early {pct(x['early']):>4s}, "
                  f"late {pct(x['late']):>4s}, median ratio {x['median_ratio']:.2f}")
    oe = d["fable_open_ended"]
    print(f"  Fable within-task slope: open-ended {oe['slope']:.2f} ({oe['tasks']} tasks) against {oe['other_slope']:.2f} "
          f"on the other {oe['other_benchmarks']} benchmarks ({oe['other_tasks']} tasks)")
    print("  Fable ALE-Bench ahc016 (requested min, worked min):", d["fable_ahc016"])
    print("  Fable OSWorld 038 (requested min, score):", d["fable_osworld_038"])
    m = d["astra_misses"]
    print(f"  Astra misses {m['n']}: late {m['late']} ({m['late_shortest']} on the shortest request, median "
          f"{m['median_late_shortest']:.2f}x), early {m['early']}")
    print(f"  Runs that hit the cutoff (worked >= 2x the task's longest request): {sum(d['cutoff'].values())} "
          + str({AGENTS[a].split(' (')[0]: k for a, k in d['cutoff'].items()}))
    print("\nFigure 3a: Table 3's on time / early / late shares above")
    if b is None:
        print("Figure 3b: skipped, no LABELS file given")
    else:
        print("Figure 3b: runs with a timestamped transcript (early and late from the clock, on time from "
              "reader labels)")
        for a, v in b.items():
            k = v["counts"]
            print(f"  {AGENTS[a]:26s} n {v['n']:3d} ({v['unclear']} unclear left out, {v['questions']} questions): "
                  + ", ".join(f"{x} {k[x]} ({pct(k[x] / v['n'])})" for x in k))
            o = v["on_time_agentic"]
            print(f"  {'':26s} on-time agentic runs: working {o['working']} of "
                  f"{o['working'] + o['rechecked'] + o['slept']}, re-checked {o['rechecked']}, slept {o['slept']}")
    print(f"Figure 3c: score at the longest request against the shortest (median longest/shortest request "
          f"{d['longest_over_shortest_request_median']:.0f}x; ALE-Bench minimized: {c['minimized_ale_tasks']})")
    for a in AGENTS:
        v = c[a]
        print(f"  {AGENTS[a]:26s} tasks {v['tasks']}: same {v['same']} ({pct(v['same'] / v['tasks'])}), higher {v['higher']} "
              f"({pct(v['higher'] / v['tasks'])}), lower {v['lower']} ({pct(v['lower'] / v['tasks'])})")
    print("\nTable 5: mean native score (runs with a score / runs)")
    for bm, row in s["benchmarks"].items():
        cells = []
        for a, v in row.items():
            x = v["mean"]
            txt = "--" if x is None else f"${x / 1e6:.2f}M" if bm == "yc-bench" else f"{x:.0f}" if bm in RAW else f"{x:.1f}"
            cells.append(f"{txt} ({v['scored']}/{v['runs']})")
        print(f"  {BENCHMARKS[bm]:22s} " + "  ".join(f"{x:>17s}" for x in cells))
    print(f"  {'Suite, matched cells':22s} " + "  ".join(
        f"{v['mean']:.1f} [{v['ci'][0]:.1f}, {v['ci'][1]:.1f}]".rjust(17) for v in s["suite"].values())
        + f"   ({len(s['suite_benchmarks'])} benchmarks, {s['suite_cells']} cells)")
    g = s["graded"]
    print(f"  {g['scored'] - g['no_output']} of the {g['runs']} runs have a native score, and {g['no_output']} more score 0 "
          f"because they left nothing to grade: {g['no_output_by_benchmark']}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("runs", help="RUNS csv")
    ap.add_argument("scores", help="SCORES csv")
    ap.add_argument("labels", nargs="?", help="LABELS csv (optional)")
    ap.add_argument("--json", help="also write the numbers to this file")
    args = ap.parse_args(argv)
    runs = load_runs(args.runs)
    numbers = compute(runs, load_scores(args.scores, runs), load_labels(args.labels) if args.labels else None)
    report(numbers)
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(numbers, fh, indent=1)


if __name__ == "__main__":
    main()
