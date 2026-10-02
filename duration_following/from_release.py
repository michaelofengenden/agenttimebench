"""Write the RUNS and SCORES inputs of analyze.py and figures.py from the website's data release.

    curl -O https://agenttimebench.com/downloads/2026-09-26-1311Z/agenttime-runs.json
    python from_release.py agenttime-runs.json OUT/      # writes OUT/runs.csv and OUT/scores.csv
    python analyze.py OUT/runs.csv OUT/scores.csv

Keeps the three agents' runs with in_default true (the 1,991 runs of Table 3). task is the benchmark's own id
(task_raw_id); score is score.value; score_raw is grade.value, or for PPTArena (instruction following + visual
quality) / 10 from score.parts; no_output is true where score.source is no_output_zero.

The bootstrap CIs draw tasks in the order they first appear, so the rows follow release_order.txt (run ids only).
Its first block has each agent's first run of each task, in the paper's RUNS order; its second block, after a blank
line, has one run per suite task, in the order the suite row's bootstrap met them in the paper's SCORES. Rows of
tasks that are not listed come last, in release order.
"""
import argparse
import csv
import json
from pathlib import Path

AGENTS = {"claude-fable-5-1": "claude-fable-5-1", "gpt-5-6-sol": "gpt-5.6-sol", "gpt-6-astra": "gpt-6-astra"}
ORDER = Path(__file__).with_name("release_order.txt")


def row(r):
    s = r["score"]
    raw = r["grade"]["value"]
    if r["benchmark"] == "pptarena" and s["value"] is not None:
        raw = (s["parts"]["instruction_following"] + s["parts"]["visual_quality"]) / 10
    return {"run_id": r["id"], "agent": AGENTS[r["agent"]], "benchmark": r["benchmark"], "task": r["task_raw_id"],
            "request": r["request"], "requested_s": r["requested_s"], "worked_s": r["worked_s"],
            "score": "" if s["value"] is None else s["value"], "score_raw": "" if s["value"] is None else raw,
            "no_output": str(s["source"] == "no_output_zero").lower()}


def ordered(rows, ids, key):
    """rows sorted by the position of their key among the listed runs; unlisted keys last, in their own order."""
    by_id = {r["run_id"]: r for r in rows}
    rank = {key(by_id[i]): n for n, i in enumerate(ids) if i in by_id}
    return sorted(rows, key=lambda r: rank.get(key(r), len(ids)))


def write(path, rows, columns):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, columns, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("release", help="agenttime-runs.json of the data release")
    ap.add_argument("out", help="output directory")
    ap.add_argument("--order", default=ORDER, help="row order file (default: release_order.txt next to this script)")
    args = ap.parse_args(argv)
    with open(args.release) as fh:
        rows = [row(r) for r in json.load(fh)["runs"] if r["in_default"] and r["agent"] in AGENTS]
    for_runs, for_scores = (b.split() for b in Path(args.order).read_text().split("\n\n"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write(out / "runs.csv", ordered(rows, for_runs, lambda r: (r["agent"], r["benchmark"], r["task"])),
          ["run_id", "agent", "benchmark", "task", "request", "requested_s", "worked_s"])
    write(out / "scores.csv", ordered(rows, for_scores, lambda r: (r["benchmark"], r["task"])),
          ["run_id", "score", "score_raw", "no_output"])
    print(f"wrote {out / 'runs.csv'} and {out / 'scores.csv'}: {len(rows)} runs")


if __name__ == "__main__":
    main()
