"""Run one batch of forecast sessions in its seeded order (design.py).

Fable 5 sessions run 8 at a time and GPT-5.6 Sol sessions 4 at a time. A session whose receipt exists is
skipped, so an interrupted batch can be resumed. With --attempt N > 1 only sessions whose attempt N-1 ended in
infra_fail are run: the 8 Sol infra_fail sessions were re-run once with --attempt 2 (analysis keeps the latest
attempt of each session; refusals were not re-run).

Usage: python run.py <main|ext|expert> --instructions DIR --out DIR [--attempt N]
DIR for --instructions holds <safe_id>.txt per task, already passed through strip_anchors.py.
"""
import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import design

HERE = Path(__file__).resolve().parent
CONCURRENCY = {"fable": 8, "sol": 4}
RUNNER = {"fable": ["node", str(HERE / "elicit_claude.mjs")], "sol": [sys.executable, str(HERE / "elicit_codex.py")]}
EXIT = {0: "ok", 3: "refusal_or_parse_fail", 4: "infra_fail"}


def pending(s, out, attempt):
    """True if this attempt has no receipt yet and, for a retry, the previous attempt ended in infra_fail."""
    name = f"{s['safe_id']}.{s['arm']}.{s['subject']}.r{s['rep']}.a{{}}.json"
    if (out / name.format(attempt)).exists():
        return False
    prev = out / name.format(attempt - 1)
    return attempt == 1 or (prev.exists() and json.loads(prev.read_text(encoding="utf-8"))["outcome"] == "infra_fail")


async def run_session(s, args, limits, counts):
    if not pending(s, args.out, args.attempt):
        counts["skipped"] += 1
        return
    async with limits[s["subject"]]:
        proc = await asyncio.create_subprocess_exec(*RUNNER[s["subject"]], str(args.instructions / f"{s['safe_id']}.txt"),
                                                    s["source"], s["task_id"], str(args.out), s["arm"], str(s["rep"]),
                                                    str(args.attempt))
        counts[EXIT.get(await proc.wait(), "error")] += 1


async def main():
    ap = argparse.ArgumentParser(description="Run one batch of forecast sessions.")
    ap.add_argument("batch", choices=design.BATCHES)
    ap.add_argument("--instructions", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--attempt", type=int, default=1)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    sessions = design.session_order(args.batch)
    limits = {subj: asyncio.Semaphore(n) for subj, n in CONCURRENCY.items()}
    counts = Counter()
    await asyncio.gather(*(run_session(s, args, limits, counts) for s in sessions))
    print(dict(counts))


if __name__ == "__main__":
    asyncio.run(main())
