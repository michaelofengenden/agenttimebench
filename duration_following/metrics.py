"""Duration-following metrics used for Table 3, Section 4.1 and Figures 1-3.

A run is a dict with benchmark, task, requested_s and worked_s. Its ratio is worked time over requested time.
On time means 0.8 <= ratio <= 1.25 (both ends inclusive).
"""
import collections
import math
import random

import numpy as np

LO, HI = 0.8, 1.25


def ratio(r):
    return r["worked_s"] / r["requested_s"]


def shares(rs):
    """(on time, early, late) shares."""
    q = np.array([ratio(r) for r in rs])
    return float(np.mean((q >= LO) & (q <= HI))), float(np.mean(q < LO)), float(np.mean(q > HI))


def deviation(rs):
    """exp(mean over benchmarks of the mean |ln ratio|): every benchmark weighs equally."""
    by = collections.defaultdict(list)
    for r in rs:
        by[r["benchmark"]].append(abs(math.log(ratio(r))))
    return math.exp(np.mean([np.mean(v) for v in by.values()]))


def loglog_fit(rs):
    """OLS of ln(worked) on ln(requested): (slope, intercept), times in seconds."""
    b, a = np.polyfit(np.log([r["requested_s"] for r in rs]), np.log([r["worked_s"] for r in rs]), 1)
    return float(b), float(a)


def pooled_slope(rs):
    return loglog_fit(rs)[0]


def within_parts(rs):
    """Per task with >= 2 runs: demeaned sum(x*y) and sum(x*x) of x = ln requested, y = ln worked."""
    by = collections.defaultdict(list)
    for r in rs:
        by[(r["benchmark"], r["task"])].append(r)
    out = {}
    for k, v in by.items():
        if len(v) < 2:
            continue
        x = np.log([r["requested_s"] for r in v])
        y = np.log([r["worked_s"] for r in v])
        x, y = x - x.mean(), y - y.mean()
        out[k] = (float((x * y).sum()), float((x * x).sum()))
    return out


def within_slope(rs):
    """Within-task slope: the pooled slope after removing each task's mean (longer requests went to harder tasks)."""
    p = within_parts(rs)
    return sum(a for a, _ in p.values()) / sum(b for _, b in p.values())


def task_bootstrap(rs, fn, n, seed=1):
    """95% percentile CI of fn, resampling tasks with replacement (tasks in first-appearance order)."""
    rng = random.Random(seed)
    tasks = collections.defaultdict(list)
    for r in rs:
        tasks[(r["benchmark"], r["task"])].append(r)
    keys = list(tasks)
    vals = [fn([r for k in (rng.choice(keys) for _ in keys) for r in tasks[k]]) for _ in range(n)]
    return [float(v) for v in np.percentile(vals, [2.5, 97.5])]
