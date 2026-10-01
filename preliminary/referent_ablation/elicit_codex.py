"""One GPT-5.6 Sol forecast session: `codex exec` with the prompt on stdin, in a fresh empty temporary
directory with a read-only sandbox, user config ignored and no session saved.

Writes <out_dir>/<safe_id>.<arm>.sol.r<rep>.a<attempt>.json unless it exists.
Exit code: 0 ok, 3 parse_fail, 4 infra_fail (any non-zero CLI exit, including a refusal).
Usage: python elicit_codex.py <instructions_file> <source> <task_id> <out_dir> <arm> <rep> [attempt]
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from strip_anchors import safe_id

HERE = Path(__file__).resolve().parent
CODEX = os.environ.get("CODEX_BIN", "codex")
MODEL, EFFORT = "gpt-5.6-sol", "xhigh"
REASON = re.compile(r"^\s*REASON\s*=\s*(.+?)\s*$", re.M)


def preflight():
    """Codex >= 0.145.0 and an empty or absent ~/.codex/AGENTS.md; returns the Codex version."""
    found = re.search(r"\d+\.\d+\.\d+", subprocess.run([CODEX, "--version"], capture_output=True, text=True).stdout)
    if not found or tuple(map(int, found[0].split("."))) < (0, 145, 0):
        sys.exit(f"Codex >= 0.145.0 is required, found {found and found[0]}")
    agents = Path.home() / ".codex" / "AGENTS.md"
    if agents.exists() and agents.stat().st_size:
        sys.exit("~/.codex/AGENTS.md must be absent or empty")
    return found[0]


def main(instructions, source, task_id, out_dir, arm, rep, attempt="1"):
    prompts = json.loads((HERE / "prompts.json").read_text(encoding="utf-8"))
    if arm not in prompts["arms"]:
        sys.exit(f"unknown arm {arm}")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    record_path = Path(out_dir).resolve() / f"{safe_id(source, task_id)}.{arm}.sol.r{rep}.a{attempt}.json"
    if record_path.exists():
        return 0
    version = preflight()
    text = Path(instructions).read_bytes()
    text.decode("utf-8")  # must be valid UTF-8; the bytes are sent unchanged
    prompt = (prompts["arms"][arm] + prompts["fence_prefix"]).encode() + text + prompts["fence_suffix"].encode()
    # Directory name prefix as in the runs (the agent's context can include its working directory).
    cwd = tempfile.mkdtemp(prefix="dbyh-sol.", dir=os.path.realpath(os.environ.get("TMPDIR", "/tmp")))
    try:
        for d in (Path(cwd), *Path(cwd).parents):
            for name in ("CLAUDE.md", "AGENTS.md"):
                if (d / name).exists():
                    sys.exit(f"instruction file on cwd walk-up: {d / name}")
        ts, t0 = datetime.datetime.now(datetime.timezone.utc).isoformat(), time.monotonic()
        cli = subprocess.run([CODEX, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                              "--ignore-user-config", "-C", cwd, "-c", f"model={MODEL}",
                              "-c", f"model_reasoning_effort={EFFORT}", "-c", "personality=none", "-"],
                             input=prompt, capture_output=True)
        session_sec = time.monotonic() - t0
    finally:
        shutil.rmtree(cwd, ignore_errors=True)

    raw = cli.stdout.decode("utf-8", errors="replace")
    parsed, parser_rc = {}, 0
    if cli.returncode == 0 and cli.stdout.strip():
        parser = subprocess.run(["node", str(HERE / "parse_estimate.mjs")], input=cli.stdout, capture_output=True)
        parser_rc = parser.returncode
        try:
            parsed = json.loads(parser.stdout)
        except ValueError:
            pass
    estimate = parsed.get("estimate_min")
    if cli.returncode != 0:
        outcome, error = "infra_fail", f"Codex CLI exited {cli.returncode}"
    elif not cli.stdout.strip():
        outcome, error = "infra_fail", "empty answer"
    elif parser_rc != 0 or parsed.get("outcome") not in ("ok", "parse_fail"):
        outcome, error = "infra_fail", "parser failed"
    elif parsed["outcome"] == "ok" and (isinstance(estimate, bool) or not isinstance(estimate, (int, float))
                                        or not 0 < estimate < float("inf")):
        outcome, error = "infra_fail", "parser returned an invalid number"
    else:
        outcome, error = parsed["outcome"], parsed.get("parser_error")
    reason = REASON.search(raw)
    record = {"source": source, "task_id": task_id, "subject": "sol", "arm": arm, "rep": int(rep),
              "attempt": int(attempt), "model": MODEL, "effort": EFFORT,
              "prompt_sha256": hashlib.sha256(prompt).hexdigest(), "ts_utc": ts, "outcome": outcome,
              "estimate_min": estimate if outcome == "ok" else None, "parse_method": parsed.get("parse_method", "none"),
              "reason_text": reason[1] if reason else None, "raw_text": raw,
              "raw_stderr": cli.stderr.decode("utf-8", errors="replace"), "codex_version": version,
              "exit_code": cli.returncode, "session_sec": session_sec, **({"error": error} if error else {})}
    record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"sol/{arm}/r{rep} {source}/{task_id}: {outcome}")
    return {"ok": 0, "infra_fail": 4}.get(outcome, 3)


if __name__ == "__main__":
    if not 7 <= len(sys.argv) <= 8:
        sys.exit(__doc__.rsplit("Usage: ", 1)[1].strip())
    sys.exit(main(*sys.argv[1:]))
