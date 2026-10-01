"""Recompute the numbers of the referent ablation ("Whose Prior Is It?").

Inputs:
  receipts   directory of session receipts (*.json, as written by elicit_claude.mjs and
             elicit_codex.py); fields used: source, task_id, subject (fable|sol), arm, rep, attempt,
             outcome (ok|refusal|parse_fail|infra_fail), estimate_min
  codes      JSONL from code_reasons.mjs: key (receipt file name) and the booleans
             explicit_human_referent, explicit_capability_claim, work_step_narration
  actuals    CSV with columns source, task_id, subject, actual_min: the runtime of the subject's one
             completed, answered, uncapped run of the task in the forecasting study

The latest attempt of each (subject, arm, rep, task) session is kept if its outcome is ok. A task's
value for an arm is the mean log estimate over the selected reps; a contrast a/b is
exp(mean over tasks of value[a] - value[b]) over the tasks that have both arms. Reps encode the
batch (design.py), so each number uses a fixed selection:
  BATCH1       you, human, frontier, placebo r1-2   you/human; you/placebo on tasks with all four arms
  same batch   you r1-2 vs frontier on the 50-task subset, you r3-4 vs frontier on the other tasks
  EXPERT       expert r1-2 vs human r5-6
  DESCRIPTIVE  you r1-4; human, frontier, placebo r1-2   figures, length bands, OSWorld, calibration

Usage: python analyze.py <receipts_dir> <codes.jsonl> <actuals.csv>
"""
import csv
import json
import math
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

import design

SUBJECTS = ("fable", "sol")
NAMES = {"fable": "Fable 5", "sol": "GPT-5.6 Sol"}
BATCH1 = {"you": (1, 2), "human": (1, 2), "frontier": (1, 2), "placebo": (1, 2)}
DESCRIPTIVE = {**BATCH1, "you": (1, 2, 3, 4)}
EXPERT = {"expert": (1, 2), "human": (5, 6)}
CUES = {"explicit_human_referent": "names a person", "explicit_capability_claim": "cites capability",
        "work_step_narration": "narrates work steps"}
EXAMPLE = ("ProgramBench", "luajit__luajit.a553b3d")


def gmean(logs):
    logs = list(logs)
    return math.exp(st.mean(logs)) if logs else math.nan


def load_receipts(folder):
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("*.json"))]
    return [r for r in rows if r["subject"] in SUBJECTS]


def latest_ok(receipts):
    latest = {}
    for r in receipts:
        key = (r["subject"], r["arm"], r["rep"], r["source"], r["task_id"])
        if key not in latest or r["attempt"] > latest[key]["attempt"]:
            latest[key] = r
    return [r for r in latest.values() if r["outcome"] == "ok"]


def task_values(records, reps):
    """{(subject, source, task_id): {arm: mean log estimate over the selected reps}}."""
    logs = defaultdict(list)
    for r in records:
        if r["rep"] in reps.get(r["arm"], ()):
            logs[(r["subject"], r["source"], r["task_id"]), r["arm"]].append(math.log(r["estimate_min"]))
    values = defaultdict(dict)
    for (task, arm), v in logs.items():
        values[task][arm] = st.mean(v)
    return values


def contrast(values, subj, a, b, keep=lambda task, arms: True):
    d = [arms[a] - arms[b] for task, arms in values.items()
         if task[0] == subj and a in arms and b in arms and keep(task, arms)]
    return gmean(d), len(d)


def contrasts(records, subj):
    b1 = task_values(records, BATCH1)
    in50 = {t[:2] for t in design.subset50(design.load_tasks())}
    b2 = task_values(records, {"you": (3, 4), "frontier": (1, 2)})
    same_batch = [arms["you"] - arms["frontier"] for values, inside in ((b1, True), (b2, False))
                  for task, arms in values.items()
                  if task[0] == subj and "you" in arms and "frontier" in arms and (task[1:] in in50) == inside]
    return {"you/human": contrast(b1, subj, "you", "human"),
            "you/placebo (finish)": contrast(b1, subj, "you", "placebo", lambda t, arms: len(arms) == 4),
            "expert/professional": contrast(task_values(records, EXPERT), subj, "expert", "human"),
            "you/frontier": (gmean(same_batch), len(same_batch))}


def arm_geomeans(records):
    """{(subject, arm): (geometric-mean estimate over tasks, n tasks)}."""
    out = defaultdict(list)
    for task, arms in task_values(records, DESCRIPTIVE).items():
        for arm, v in arms.items():
            out[task[0], arm].append(v)
    return {key: (gmean(v), len(v)) for key, v in out.items()}


def scatter_points(records):
    """One point per task: the subject's human estimate and its you/human ratio."""
    return [{"subject": task[0], "source": task[1], "human": math.exp(arms["human"]),
             "ratio": math.exp(arms["you"] - arms["human"])}
            for task, arms in task_values(records, DESCRIPTIVE).items() if "you" in arms and "human" in arms]


def length_dependence(points, subj):
    """OLS slope of log(you/human) on log(human estimate), and the ratio per human-estimate band."""
    xy = [(math.log(p["human"]), math.log(p["ratio"])) for p in points if p["subject"] == subj]
    if len({x for x, _ in xy}) < 2:
        return math.nan, {}
    mx, my = st.mean(x for x, _ in xy), st.mean(y for _, y in xy)
    slope = sum((x - mx) * (y - my) for x, y in xy) / sum((x - mx) ** 2 for x, _ in xy)
    bands = {}
    ten, two_hours = math.log(10), math.log(120)
    for label, lo, hi in (("<10 min", -math.inf, ten), ("10-120 min", ten, two_hours), (">120 min", two_hours, math.inf)):
        d = [y for x, y in xy if lo <= x < hi]
        bands[label] = (gmean(d), len(d))
    return slope, bands


def quartiles(records, actuals_csv):
    """Actual runtime / self-forecast (you arm) by quartile of actual runtime, per subject."""
    with open(actuals_csv, newline="", encoding="utf-8") as fh:
        actual = {(r["subject"], r["source"], r["task_id"]): float(r["actual_min"]) for r in csv.DictReader(fh)}
    out = []
    for subj in SUBJECTS:
        pts = sorted((math.log(actual[task]), math.log(actual[task]) - arms["you"])
                     for task, arms in task_values(records, DESCRIPTIVE).items()
                     if task[0] == subj and "you" in arms and actual.get(task, 0) > 0)
        k = len(pts) // 4
        for i, chunk in enumerate((pts[:k], pts[k:2 * k], pts[2 * k:3 * k], pts[3 * k:]) if k else ()):
            out.append({"subject": subj, "q": f"Q{i + 1}", "n": len(chunk),
                        "median_actual": math.exp(st.median(x for x, _ in chunk)), "ratio": gmean(y for _, y in chunk)})
    return out


def rationale_cues(codes_jsonl):
    """{(subject, arm): {"n": coded sentences, cue: % carrying it}}."""
    groups = defaultdict(list)
    for line in Path(codes_jsonl).read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        _, arm, subj, *_ = row["key"].rsplit(".", 5)
        if "coder_error" not in row:
            groups[subj, arm].append(row)
    return {key: {"n": len(rows), **{c: 100 * sum(r[c] for r in rows) / len(rows) for c in CUES}}
            for key, rows in groups.items()}


def main(receipts_dir, codes_jsonl, actuals_csv):
    receipts = load_receipts(receipts_dir)
    records = latest_ok(receipts)
    first = Counter(r["subject"] for r in receipts if r["attempt"] == 1)
    print(f"{sum(first.values()):,} first-attempt sessions ({first['fable']:,} Fable 5, {first['sol']:,} Sol), "
          f"{len(receipts):,} receipts with retries")

    print("\nReferent contrasts (geometric mean of within-task ratios)")
    for subj in SUBJECTS:
        for name, (ratio, n) in contrasts(records, subj).items():
            print(f"  {NAMES[subj]:12s} {name:21s} x{ratio:.3f}  n={n}")

    print("\nGeometric-mean forecast by referent, minutes (Figure forecast-by-referent)")
    gm = arm_geomeans(records)
    for subj in SUBJECTS:
        cells = [(arm, *gm.get((subj, arm), (math.nan, 0))) for arm in ("you", "frontier", "human", "placebo")]
        print(f"  {NAMES[subj]:12s} " + "  ".join(f"{arm} {g:.1f} (n={n})" for arm, g, n in cells))

    points = scatter_points(records)
    print(f"\nSelf/human ratio by human estimate (Figure self-to-human-ratio, {len(points)} points)")
    for subj in SUBJECTS:
        slope, bands = length_dependence(points, subj)
        mine = [p for p in points if p["subject"] == subj]
        osworld = [math.log(p["ratio"]) for p in mine if p["source"] == "OSWorld 2.0"]
        above = [p for p in mine if p["ratio"] > 1]
        print(f"  {NAMES[subj]:12s} slope {slope:+.3f}  " + "  ".join(f"{b} x{r:.3f} (n={n})" for b, (r, n) in bands.items()))
        print(f"  {'':12s} OSWorld 2.0 x{gmean(osworld):.3f};  you > human on {len(above)}/{len(mine)} "
              f"tasks ({sum(p['source'] == 'OSWorld 2.0' for p in above)} OSWorld 2.0)")

    print(f"\n{EXAMPLE[0]} {EXAMPLE[1]}, batch-1 estimates in minutes")
    for subj in SUBJECTS:
        ex = {arm: sorted(r["estimate_min"] for r in records if (r["subject"], r["arm"], r["source"], r["task_id"])
                          == (subj, arm, *EXAMPLE) and r["rep"] in (1, 2)) for arm in ("you", "human")}
        print(f"  {NAMES[subj]:12s} you {ex['you']}  human {ex['human']}")

    print("\nRationale cues, % of coded REASON sentences (Figure rationale-cues)")
    for (subj, arm), c in sorted(rationale_cues(codes_jsonl).items()):
        print(f"  {NAMES[subj]:12s} {arm:9s} n={c['n']:3d}  " + "  ".join(f"{label} {c[cue]:5.1f}" for cue, label in CUES.items()))

    print("\nActual runtime / self-forecast by quartile of actual runtime (Figure task-length-calibration)")
    for q in quartiles(records, actuals_csv):
        print(f"  {NAMES[q['subject']]:12s} {q['q']}  x{q['ratio']:.3f}  median actual {q['median_actual']:.2f} min  n={q['n']}")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__.rsplit("Usage: ", 1)[1].strip())
    main(*sys.argv[1:])
