"""Task subset and session orders (seed 0 everywhere).

The frontier, placebo and expert arms ran on a 50-task subset, drawn by source with
largest-remainder allocation. Each batch is one seeded shuffle of its grid, for both subjects:
  main    232 tasks x {you, human} r1-2  +  50 x {frontier, placebo} r1-2   2,256 sessions
  ext     the other 182 tasks x {frontier r1-2, you r3-4}                   1,456
  expert  50 tasks x {expert r1-2, human r5-6}                                400

Usage: python design.py <main|ext|expert> > session_order.jsonl
"""
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

from strip_anchors import safe_id

TASKS = Path(__file__).resolve().parent / "tasks.csv"
SEED = 0
SUBJECTS = ("fable", "sol")
BATCHES = ("main", "ext", "expert")


def load_tasks(path=TASKS):
    """(source, task_id, safe_id) for each task, in file order."""
    with open(path, newline="", encoding="utf-8") as fh:
        return [(r["source"], r["task_id"], safe_id(r["source"], r["task_id"])) for r in csv.DictReader(fh)]


def subset50(tasks, k=50):
    """k tasks allocated to sources by largest remainder, drawn per source in safe-id order."""
    by_src = defaultdict(list)
    for t in sorted(tasks, key=lambda t: t[2]):
        by_src[t[0]].append(t)
    sources = sorted(by_src)
    quota = {s: len(by_src[s]) * k / len(tasks) for s in sources}
    alloc = {s: int(quota[s]) for s in sources}
    for s in sorted(sources, key=lambda s: (quota[s] - alloc[s], s), reverse=True)[:k - sum(alloc.values())]:
        alloc[s] += 1
    rng = random.Random(SEED)
    return sorted((t for s in sources for t in rng.sample(by_src[s], alloc[s])), key=lambda t: t[2])


def session_order(batch, tasks=None):
    tasks = tasks or load_tasks()
    sub50 = subset50(tasks)
    rest = [t for t in tasks if t not in sub50]
    grid = {"main": [(tasks, [("you", 1), ("you", 2), ("human", 1), ("human", 2)]),
                     (sub50, [("frontier", 1), ("frontier", 2), ("placebo", 1), ("placebo", 2)])],
            "ext": [(rest, [("frontier", 1), ("frontier", 2), ("you", 3), ("you", 4)])],
            "expert": [(sub50, [("expert", 1), ("expert", 2), ("human", 5), ("human", 6)])]}[batch]
    sessions = [{"subject": subj, "arm": arm, "rep": rep, "source": source, "task_id": task_id, "safe_id": sid}
                for rows, cells in grid for source, task_id, sid in rows for arm, rep in cells for subj in SUBJECTS]
    random.Random(SEED).shuffle(sessions)
    return sessions


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in BATCHES:
        sys.exit(f"usage: python design.py <{'|'.join(BATCHES)}>")
    for s in session_order(sys.argv[1]):
        print(json.dumps(s))
