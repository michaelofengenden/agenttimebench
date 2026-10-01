"""Shared definitions for the retrospective-fork runners: agents, conditions, the question, parents, records.

One job is one answer: (parent run, condition, k), k in {1, 2}, job id f"{run_id}__{condition}__k{k}". Each runner is
a CLI `python <runner>.py --job JOB --parents parents.csv --sessions DIR [--out results]` that writes one JSON record,
<out>/<job>.json. DIR holds each parent's session log as DIR/<run_id>/<file>.jsonl.

parents.csv columns: run_id, agent (claude-fable-5-1, gpt-5.6-sol or gpt-6-astra, as in duration_following RUNS),
benchmark, task, request_min (the requested duration, minutes), truth_min (the parent's recorded runtime, minutes).
"""
import argparse
import csv
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONDITIONS = ("oracle", "native", "context-only", "replay", "scrubbed")    # R-oracle ... R-scrubbed
FORK_CONDITIONS = ("oracle", "native", "context-only")
K = (1, 2)
QUESTION = "How long did it take you to complete this task?\nreturn minutes = <Number of minutes>"
DOCKER_IMAGE = "agenttime-retro:1"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Model ids as the parent sessions recorded them, OpenRouter slugs, and the provider every request is pinned to
# (the model's own lab, no fallbacks).
AGENTS = {
    "claude-fable-5-1": {"model": "claude-fable-5-1", "openrouter": "anthropic/claude-fable-5.1",
                         "session_format": "claude", "provider": "Anthropic"},
    "gpt-5.6-sol": {"model": "gpt-5.6-sol", "openrouter": "openai/gpt-5.6-sol", "session_format": "codex",
                    "provider": "OpenAI"},
    "gpt-6-astra": {"model": "gpt-6-astra", "openrouter": "openai/gpt-6-astra", "session_format": "codex",
                    "provider": "OpenAI"},
}

_MINUTES = re.compile(r"(?i)(?<![a-z])minutes\s*=\s*(?:~|≈|about\s+)?\s*([0-9]+(?:\.[0-9]+)?)")


def parse_minutes(text):
    """The last `minutes = N` in an answer (decimals allowed, "~"/"≈"/"about" accepted), else None."""
    hits = _MINUTES.findall(text or "")
    return float(hits[-1]) if hits else None


def load_parents(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["request_min"], r["truth_min"] = float(r["request_min"]), float(r["truth_min"])
    return {r["run_id"]: r for r in rows}


def all_jobs(parents):
    return [f"{rid}__{c}__k{k}" for rid in parents for c in CONDITIONS for k in K]


def parse_job(job, parents):
    run_id, condition, k = job.rsplit("__", 2)
    assert condition in CONDITIONS and k in ("k1", "k2"), job
    return parents[run_id], condition, int(k[1:])


def session_file(sessions_dir, run_id):
    files = sorted((Path(sessions_dir) / run_id).glob("*.jsonl"))
    if len(files) != 1:
        raise FileNotFoundError(f"expected one session .jsonl in {Path(sessions_dir) / run_id}, found {len(files)}")
    return files[0]


def elapsed_seconds(truth_min):
    """The oracle tool's value: the parent's runtime in seconds, 3 decimals, trailing zeros dropped."""
    return f"{truth_min * 60:.3f}".rstrip("0").rstrip(".")


def openrouter_key():
    return os.environ["OPENROUTER_API_KEY"]


def runner_args(argv, what):
    ap = argparse.ArgumentParser(description=what)
    ap.add_argument("--job", required=True)
    ap.add_argument("--parents", required=True, help="parents.csv")
    ap.add_argument("--sessions", required=True, help="DIR/<run_id>/<session>.jsonl")
    ap.add_argument("--out", default="results")
    args = ap.parse_args(argv)
    parent, condition, k = parse_job(args.job, load_parents(args.parents))
    return args, parent, condition, k


def write_record(out_dir, job, parent, condition, k, **fields):
    """fields: status ('ok' | 'parse_fail' | 'error'), minutes, final_text, error, plus runner-specific extras."""
    rec = {"job": job, "run_id": parent["run_id"], "agent": parent["agent"], "benchmark": parent["benchmark"],
           "task": parent["task"], "condition": condition, "k": k, "request_min": parent["request_min"],
           "truth_min": parent["truth_min"], **fields}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / f".{job}.tmp"
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False))
    tmp.replace(out / f"{job}.json")
    return rec


def answer(text, error):
    """status, minutes and final_text of one reply."""
    minutes = None if error else parse_minutes(text)
    status = "error" if error else ("ok" if minutes is not None else "parse_fail")
    return {"status": status, "minutes": minutes, "final_text": text, "error": error}
