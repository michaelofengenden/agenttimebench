"""Run every job (69 parents x 5 conditions x 2 answers = 690), the R-scrubbed re-ask, then write answers.csv.

    python run_all.py --parents parents.csv --sessions DIR [--out results] [--forks 10] [--replays 20] [--only native]

Resumable: a job whose record has status "ok" or "parse_fail" is skipped and "error" records are retried
(--retry-errors passes). Docker forks and API replays have separate concurrency limits. <out>/answers.csv has one row
per answer: run_id, agent, benchmark, task, condition, k, minutes, status, pressed (the input of analyze.py).
"""
import argparse
import csv
import json
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import common

ANSWER_COLUMNS = ("run_id", "agent", "benchmark", "task", "condition", "k", "minutes", "status", "pressed")


def runner_for(job, parents):
    """(script, concurrency class) for one job."""
    parent, condition, _ = common.parse_job(job, parents)
    if condition in common.FORK_CONDITIONS:
        fmt = common.AGENTS[parent["agent"]]["session_format"]
        return ("fork_claude.py" if fmt == "claude" else "fork_codex.py"), "docker"
    return "replay.py", "api"


def record(out, job):
    p = Path(out) / f"{job}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def write_answers(out, jobs):
    with open(Path(out) / "answers.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(ANSWER_COLUMNS)
        for job in jobs:
            r = record(out, job)
            w.writerow([r["run_id"], r["agent"], r["benchmark"], r["task"], r["condition"], r["k"],
                        "" if r["minutes"] is None else r["minutes"], r["status"],
                        "true" if r.get("pressed") else "false"])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--parents", required=True)
    ap.add_argument("--sessions", required=True)
    ap.add_argument("--out", default="results")
    ap.add_argument("--forks", type=int, default=10)
    ap.add_argument("--replays", type=int, default=20)
    ap.add_argument("--only", default="", help="comma-separated conditions")
    ap.add_argument("--retry-errors", type=int, default=1)
    args = ap.parse_args(argv)
    out, sessions, parents_csv = (str(Path(p).resolve()) for p in (args.out, args.sessions, args.parents))
    parents = common.load_parents(parents_csv)
    only = set(args.only.split(",")) if args.only else set(common.CONDITIONS)
    gates = {"docker": threading.Semaphore(args.forks), "api": threading.Semaphore(args.replays)}

    def run(job):
        script, kind = runner_for(job, parents)
        with gates[kind]:
            subprocess.run([sys.executable, str(common.HERE / script), "--job", job, "--parents", parents_csv,
                            "--sessions", sessions, "--out", out], cwd=common.HERE, timeout=40 * 60)
        print(f"{job}: {record(out, job).get('status')}", flush=True)

    for _ in range(1 + args.retry_errors):
        todo = [j for j in common.all_jobs(parents)
                if common.parse_job(j, parents)[1] in only and record(out, j).get("status") not in ("ok", "parse_fail")]
        if not todo:
            break
        with ThreadPoolExecutor(max_workers=args.forks + args.replays) as pool:
            for f in [pool.submit(run, j) for j in todo]:
                try:
                    f.result()
                except Exception as exc:          # one runner crash must not stop the others
                    print(f"runner exception: {exc!r}", flush=True)
    if "scrubbed" in only:
        subprocess.run([sys.executable, str(common.HERE / "press.py"), "--out", out], cwd=common.HERE)
    jobs = [j for j in common.all_jobs(parents) if record(out, j)]
    write_answers(out, jobs)
    print(Counter(record(out, j)["status"] for j in jobs))


if __name__ == "__main__":
    main()
