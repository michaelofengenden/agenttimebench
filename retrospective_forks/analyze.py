"""Figure 5 numbers: retrospective deviation per agent and condition.

    python analyze.py parents.csv answers.csv

parents.csv: run_id, agent (claude-fable-5-1, gpt-5.6-sol, gpt-6-astra), benchmark, task, request_min, truth_min
(minutes; truth_min is the parent's recorded runtime). answers.csv (written by run_all.py): one row per answer with
run_id, agent, benchmark, task, condition, k, minutes (empty when none was parsed), status (ok | parse_fail | error)
and pressed (true when re-asked).

An answer counts when its status is "ok" and minutes > 0; its ratio is minutes / truth_min. Deviation =
exp(mean |ln ratio|), the geometric mean of the factor by which the answers miss the actual runtime. Prints every lane
of Figure 5, then for R-scrubbed the re-asked replies, the deviation without GPQA Diamond and the ratio to R-replay.
"""
import csv
import math
import sys
from collections import defaultdict

AGENTS = {"claude-fable-5-1": "Fable 5.1", "gpt-5.6-sol": "GPT-5.6 Sol", "gpt-6-astra": "GPT-6 Astra"}
CONDITIONS = {"oracle": "R-oracle", "native": "R-native", "context-only": "R-context-only", "replay": "R-replay",
              "scrubbed": "R-scrubbed"}


def load(parents_csv, answers_csv):
    with open(parents_csv, newline="") as f:
        truth = {r["run_id"]: float(r["truth_min"]) for r in csv.DictReader(f)}
    with open(answers_csv, newline="") as f:
        answers = list(csv.DictReader(f))
    for a in answers:
        m = float(a["minutes"]) if a["minutes"] else None
        a["ratio"] = m / truth[a["run_id"]] if a["status"] == "ok" and m is not None and m > 0 else None
        a["pressed"] = a["pressed"] == "true"
    return answers


def deviation(ratios):
    return math.exp(sum(abs(math.log(x)) for x in ratios) / len(ratios)) if ratios else None


def lanes(answers, keep=lambda a: True):
    """{(agent, condition): (n counted, deviation, n re-asked, n not counted)}"""
    by = defaultdict(list)
    for a in answers:
        if keep(a):
            by[(a["agent"], a["condition"])].append(a)
    out = {}
    for key, rows in by.items():
        ratios = [a["ratio"] for a in rows if a["ratio"] is not None]
        out[key] = (len(ratios), deviation(ratios), sum(a["pressed"] for a in rows), len(rows) - len(ratios))
    return out


def main(argv):
    answers = load(argv[1], argv[2])
    all_lanes = lanes(answers)
    no_gpqa = lanes(answers, lambda a: a["benchmark"] != "gpqa-diamond")
    print(f"{'agent':<12} {'condition':<15} {'n':>3} {'deviation':>10} {'re-asked':>8} {'not counted':>11}")
    for agent, name in AGENTS.items():
        for cond, label in CONDITIONS.items():
            n, dev, pressed, missing = all_lanes[(agent, cond)]
            print(f"{name:<12} {label:<15} {n:>3} {dev:>10.4f} {pressed:>8} {missing:>11}")
    print("\nR-scrubbed: re-asked replies, deviation without GPQA Diamond, ratio to R-replay")
    for agent, name in AGENTS.items():
        n_all, dev_all, pressed, missing = all_lanes[(agent, "scrubbed")]
        n, dev, _, _ = no_gpqa[(agent, "scrubbed")]
        print(f"  {name:<12} re-asked {pressed} of {n_all + missing}; without GPQA (n={n}) {dev:.4f}; "
              f"R-scrubbed / R-replay = {dev_all / all_lanes[(agent, 'replay')][1]:.2f}x")
    tasks = {(a["benchmark"], a["task"]) for a in answers}
    print("\ntasks:", {b: sum(1 for t in tasks if t[0] == b) for b in sorted({t[0] for t in tasks})},
          "answers:", len(answers))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv)
