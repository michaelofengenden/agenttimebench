"""Pick the parent runs (chosen before any fork ran).

Rule: tasks where Fable 5.1, GPT-5.6 Sol and GPT-6 Astra all have a kept duration-following run with its session log
available, at the same requested duration. For each task use the middle request; if one agent lacks it, use the
longest, then the shortest, but always the same request for all three. Take every CORE-Bench and TUA-Bench task that
qualifies, then fill to 23 tasks with GPQA Diamond tasks in sha256(task id) order. Ground truth is each run's
recorded runtime.

    python select_parents.py candidates.csv parents.csv

candidates.csv lists the eligible runs of the three benchmarks (kept, session log available), one row per run:
run_id, agent (claude-fable-5-1, gpt-5.6-sol, gpt-6-astra), benchmark, task, request_min, truth_min (minutes).
parents.csv gets the selected rows, same columns.
"""
import csv
import hashlib
import sys
from collections import defaultdict

AGENTS = ("claude-fable-5-1", "gpt-5.6-sol", "gpt-6-astra")
TARGET = 23


def select(rows):
    by = defaultdict(lambda: defaultdict(dict))          # (benchmark, task) -> agent -> request_min -> row
    for r in rows:
        by[(r["benchmark"], r["task"])][r["agent"]][float(r["request_min"])] = r

    def pick(task):
        per = by[task]
        if len(per) < 3:
            return None
        reqs = sorted(set.intersection(*(set(v) for v in per.values())))
        if not reqs:
            return None
        allreq = sorted(set().union(*(set(v) for v in per.values())))
        middle = allreq[len(allreq) // 2] if len(allreq) == 3 else None
        for want in ([middle] if middle else []) + [max(reqs), min(reqs)]:
            if want in reqs:
                return want
        return None

    chosen = []
    for bench in ("core-bench", "tua-bench"):
        chosen += [t for t in sorted(by) if t[0] == bench and pick(t) is not None]
    gpqa = sorted((t for t in by if t[0] == "gpqa-diamond" and pick(t) is not None),
                  key=lambda t: hashlib.sha256(t[1].encode()).hexdigest())
    chosen += gpqa[: TARGET - len(chosen)]
    return [by[t][agent][pick(t)] for t in chosen for agent in AGENTS]


def main(argv):
    src, dst = argv[1], argv[2]
    with open(src, newline="") as f:
        parents = select(list(csv.DictReader(f)))
    cols = ["run_id", "agent", "benchmark", "task", "request_min", "truth_min"]
    with open(dst, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(parents)
    print(f"{len(parents) // 3} tasks, {len(parents)} parents -> {dst}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv)
