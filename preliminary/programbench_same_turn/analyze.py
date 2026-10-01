#!/usr/bin/env python3
"""Print the same-turn ProgramBench numbers (paper: the two same-turn tables, the figure callouts,
Appendix "Ranking task-length versus a simple baseline" and the same-turn self-score numbers).

  python analyze.py records.csv

records.csv (written by `extract.py table`) has one row per run; the columns used are
  instance_id, agent (claude | codex), actual_minutes, estimate_minutes, perceived_minutes, self_score,
  test_pass_score (0-100), cutoff (1 = stopped by a usage limit after work began, so the runtime is a
  lower bound), visible_event_count, full_visible_chars, raw_log_bytes, compacted_chars.
Empty cells are missing values.
"""
import math
import random
import sys
from csv import DictReader

AGENTS = {"claude": "Claude Code", "codex": "Codex"}
# Re-runs of these two tasks hung the test harness, so their test-pass score is from the
# interrupted first run. The original analysis dropped them from self-score comparisons (both agents).
SELF_SCORE_EXCLUDED = {"ogham__dog.721440b", "svenstaro__miniserve.8449e8b"}
N_BOOT, SEED = 2000, 0


def load(path):
    rows = []
    for r in DictReader(open(path)):
        for key in ("actual_minutes", "estimate_minutes", "perceived_minutes", "self_score", "test_pass_score"):
            r[key] = float(r[key]) if r[key] != "" else None
        for key in ("visible_event_count", "full_visible_chars", "raw_log_bytes", "compacted_chars"):
            r[key] = int(r[key])
        r["cutoff"] = r["cutoff"] == "1"
        rows.append(r)
    return rows


def mean(xs):
    return sum(xs) / len(xs)


def pearson(xs, ys):
    mx, my = mean(xs), mean(ys)
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def percentile(values, p):
    """Linear interpolation between order statistics."""
    s = sorted(values)
    pos = (len(s) - 1) * p
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def log_slope(rows):
    """Compression exponent: OLS slope of log(estimate) on log(actual), with intercept."""
    xs = [math.log(r["actual_minutes"]) for r in rows]
    ys = [math.log(r["estimate_minutes"]) for r in rows]
    mx, my = mean(xs), mean(ys)
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return beta, my - beta * mx


def compression(rows):
    """Beta and its task-bootstrap 95% CI. Interrupted runs (runtime is a lower bound) are excluded."""
    fit = [r for r in rows if not r["cutoff"]]
    rng = random.Random(SEED)
    boots = [log_slope([fit[rng.randrange(len(fit))] for _ in fit])[0] for _ in range(N_BOOT)]
    beta, alpha = log_slope(fit)
    return {"beta": beta, "alpha": alpha, "n": len(fit),
            "ci": (percentile(boots, 0.025), percentile(boots, 0.975))}


def ratio(rows):
    """Calibration ratio: mean forecast over mean runtime."""
    return mean([r["estimate_minutes"] for r in rows]) / mean([r["actual_minutes"] for r in rows])


def summary(rows):
    act = [r["actual_minutes"] for r in rows]
    est = [r["estimate_minutes"] for r in rows]
    scored = [r for r in rows if r["self_score"] is not None and r["instance_id"] not in SELF_SCORE_EXCLUDED]
    log_act = [math.log(a) for a in act]
    return {
        "n": len(rows), "mean_actual": mean(act), "min": min(act), "max": max(act),
        "mean_score": mean([r["test_pass_score"] for r in rows]),
        "mean_estimate": mean(est), "ratio": ratio(rows),
        "within_1_5": sum(2 / 3 <= e / a <= 1.5 for e, a in zip(est, act)) / len(rows),
        "r_raw": pearson(est, act),
        "compression": compression(rows),
        "self_n": len(scored),
        "self_bias": mean([r["self_score"] - r["test_pass_score"] for r in scored]),
        "self_r": pearson([r["test_pass_score"] for r in scored], [r["self_score"] for r in scored]),
        "r_log_estimate": pearson([math.log(e) for e in est], log_act),
        "r_log_after_run": pearson([math.log(r["perceived_minutes"]) for r in rows], log_act),
        "r_log_events": pearson([math.log(r["visible_event_count"]) for r in rows], log_act),
    }


def main(path):
    rows = load(path)
    for agent, name in AGENTS.items():
        s = summary([r for r in rows if r["agent"] == agent])
        c = s["compression"]
        print(f"\n{name}  (n = {s['n']})")
        print(f"  mean duration {s['mean_actual']:.1f} min, range {s['min']:.0f}-{s['max']:.0f} min, "
              f"mean test-pass rate {s['mean_score']:.1f}%")
        print(f"  mean prediction / actual {s['mean_estimate']:.0f} / {s['mean_actual']:.1f} min, "
              f"calibration ratio {s['ratio']:.2f}x, within 1.5x {100 * s['within_1_5']:.0f}%, "
              f"Pearson r (minutes) {s['r_raw']:.2f}")
        print(f"  compression exponent {c['beta']:.2f} (95% CI {c['ci'][0]:.2f}-{c['ci'][1]:.2f}, "
              f"alpha {c['alpha']:.2f}, n = {c['n']} non-interrupted runs)")
        print(f"  self-score minus test-pass score {s['self_bias']:+.1f} pts (n = {s['self_n']}, "
              f"r = {s['self_r']:.2f})")
        print(f"  Pearson r on logs with runtime: before-run estimate {s['r_log_estimate']:.2f}, "
              f"after-run report {s['r_log_after_run']:.2f}, visible event count {s['r_log_events']:.2f}")
    claude = {r["instance_id"]: r for r in rows if r["agent"] == "claude"}
    codex = {r["instance_id"]: r for r in rows if r["agent"] == "codex"}
    short = [i for i, r in claude.items() if r["actual_minutes"] < 25]
    print(f"\nClaude runs under 25 min (n = {len(short)}): Claude calibration ratio "
          f"{ratio([claude[i] for i in short]):.2f}x; Codex on the same tasks {ratio([codex[i] for i in short]):.2f}x, "
          f"Codex overall {ratio(list(codex.values())):.2f}x")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
