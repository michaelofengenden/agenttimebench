"""R-oracle, R-native and R-context-only for Fable 5.1 parents: fork the parent's Claude Code session and ask QUESTION.

    python fork_claude.py --job RUNID__native__k1 --parents parents.csv --sessions DIR [--out results]

A copy of the parent session (prepare_copy) loses its thinking blocks (records left empty go, their children are
re-parented) and the requested duration with every restatement of it (request_removal.strip_request_obj over all
records at once). It sits in a fresh Claude config dir at cfg/projects/<slug of the recorded cwd>/<uuid>.jsonl.
Claude Code 2.1.280 resumes it in the agenttime-retro:1 container (docker/), in an empty directory at the parent's
recorded cwd, as an unprivileged user, with the parent's recorded model id and effort and the flags of build_argv.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import common
from request_removal import strip_request_obj

TIMEOUT_S = 20 * 60
ORACLE_TOOL = "mcp__elapsed__elapsed_seconds"
_UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
_THINKING = ("thinking", "redacted_thinking")
_PARENT_LINKS = ("parentUuid", "logicalParentUuid", "leafUuid", "sourceToolAssistantUUID")

# Root-side setup inside the container: create the recorded cwd empty, make it reachable and owned by the
# unprivileged "agent" user, then drop to that user for the claude process.
ENTRY = r'''set -e
d="$RETRO_CWD"
mkdir -p "$d"
p="$d"; while [ "$p" != "/" ]; do p=$(dirname "$p"); chmod o+x "$p" 2>/dev/null || true; done
chown -R agent:agent "$d" /cfg
cd "$d"
exec setpriv --reuid=1001 --regid=1001 --init-groups --inh-caps=-all --bounding-set=-all \
     env HOME=/home/agent USER=agent LOGNAME=agent "$@"
'''


def project_slug(path):
    """Claude Code's project directory name for a cwd: '/', '_' and '.' all become '-'."""
    return str(path).replace("/", "-").replace("_", "-").replace(".", "-")


def session_facts(path):
    """uuid (from the file name), recorded cwd, effort and model id."""
    m = _UUID.search(Path(path).name)
    if not m:
        raise ValueError(f"cannot find the session uuid in {path}")
    cwd, efforts, models = None, Counter(), Counter()
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        cwd = cwd or rec.get("cwd")
        if rec.get("type") == "assistant":
            if rec.get("effort"):
                efforts[str(rec["effort"])] += 1
            model = (rec.get("message") or {}).get("model")
            if model and model != "<synthetic>":
                models[model] += 1
    if cwd is None:
        raise ValueError("the session records no cwd")
    return {"uuid": m.group(1), "cwd": cwd, "effort": efforts.most_common(1)[0][0],
            "model": models.most_common(1)[0][0]}


def prepare_copy(src, dst, request_min):
    """Write the session copy the fork resumes: no thinking blocks, no requested duration."""
    dropped, kept = {}, []                             # uuid of a dropped record -> its parent
    for line in open(src, encoding="utf-8"):
        if not line.strip():
            continue
        rec = json.loads(line)
        msg = rec.get("message")
        if isinstance(msg, dict) and isinstance(msg.get("content"), list):
            new = [b for b in msg["content"] if not (isinstance(b, dict) and b.get("type") in _THINKING)]
            if not new:
                dropped[rec.get("uuid")] = rec.get("parentUuid")
                continue
            msg["content"] = new
        kept.append(rec)

    def resolve(u):
        seen = set()
        while u in dropped and u not in seen:
            seen.add(u)
            u = dropped[u]
        return u

    with open(dst, "w", encoding="utf-8") as f:
        for rec in strip_request_obj(kept, request_min):  # over all records at once
            for k in _PARENT_LINKS:
                if rec.get(k) in dropped:
                    rec[k] = resolve(rec[k])
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def build_argv(condition, facts):
    argv = ["claude", "-p", common.QUESTION, "--resume", facts["uuid"], "--fork-session", "--model", facts["model"],
            "--output-format", "stream-json", "--verbose", "--strict-mcp-config", "--effort", facts["effort"]]
    return argv + {"native": ["--dangerously-skip-permissions"],
                   "context-only": ["--tools", ""],
                   "oracle": ["--dangerously-skip-permissions", "--mcp-config", "/job/mcp.json",
                              "--allowedTools", ORACLE_TOOL]}[condition]


def job_dir(src, facts, parent, condition):
    d = Path(tempfile.mkdtemp(prefix="retro-claude-"))
    proj = d / "cfg" / "projects" / project_slug(facts["cwd"])
    proj.mkdir(parents=True)
    (d / "job").mkdir()
    prepare_copy(src, proj / f"{facts['uuid']}.jsonl", parent["request_min"])
    if condition == "oracle":
        (d / "job" / "mcp.json").write_text(json.dumps({"mcpServers": {"elapsed": {
            "command": "python3", "args": ["/opt/retro/oracle_tool.py"],
            "env": {"ELAPSED_SECONDS": common.elapsed_seconds(parent["truth_min"])}}}}))
    return d


def run(d, argv, facts, provider, key):
    """The fork. Returns its stream-json events, or None on timeout."""
    name = f"retro-{os.getpid()}-{d.name[-8:]}"
    env = {**os.environ, "ANTHROPIC_AUTH_TOKEN": key}    # the container gets only the -e variables below
    cmd = ["docker", "run", "--rm", "--name", name, "--user", "0", "--network", "bridge",
           "--memory", "4g", "--pids-limit", "512", "-v", f"{d / 'cfg'}:/cfg", "-v", f"{d / 'job'}:/job:ro",
           "-e", "ANTHROPIC_AUTH_TOKEN",                  # value from the environment, never in argv
           "-e", "ANTHROPIC_BASE_URL=https://openrouter.ai/api", "-e", "ANTHROPIC_API_KEY=",
           "-e", "CLAUDE_CONFIG_DIR=/cfg", "-e", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
           "-e", "DISABLE_AUTOUPDATER=1", "-e", "DISABLE_AUTO_COMPACT=1",     # the fork must see the whole transcript
           "-e", "CLAUDE_CODE_EXTRA_BODY=" + json.dumps({"provider": {"order": [provider], "allow_fallbacks": False}}),
           "-e", f"RETRO_CWD={facts['cwd']}", common.DOCKER_IMAGE, "sh", "-c", ENTRY, "retro-entry", *argv]
    try:
        proc = subprocess.run(cmd, capture_output=True, env=env, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        return None
    events = []
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.strip().startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def main(argv=None):
    args, parent, condition, k = common.runner_args(argv, "Claude Code forks")
    agent = common.AGENTS[parent["agent"]]
    if agent["session_format"] != "claude" or condition not in common.FORK_CONDITIONS:
        sys.exit(f"{args.job}: fork_claude.py runs the fork conditions of Claude Code parents only")
    d = None
    try:
        src = common.session_file(args.sessions, parent["run_id"])
        facts = session_facts(src)
        d = job_dir(src, facts, parent, condition)
        events = run(d, build_argv(condition, facts), facts, agent["provider"], common.openrouter_key())
    except Exception as exc:
        common.write_record(args.out, args.job, parent, condition, k, **common.answer("", f"{type(exc).__name__}: {exc}"))
        return 1
    finally:
        if d:
            shutil.rmtree(d, ignore_errors=True)
    init = next((e for e in events or [] if e.get("type") == "system" and e.get("subtype") == "init"), None) or {}
    result = next((e for e in reversed(events or []) if e.get("type") == "result"), None) or {}
    error = None
    if events is None:
        error = f"timeout after {TIMEOUT_S}s"
    elif not result:
        error = "no result event"
    elif result.get("is_error") or result.get("subtype") != "success":
        error = f"result {result.get('subtype')}: {str(result.get('result') or result.get('errors'))[:600]}"
    elif condition == "context-only" and init.get("tools"):
        error = f"context-only fork was offered tools: {init.get('tools')}"
    elif condition == "oracle" and ORACLE_TOOL not in (init.get("tools") or []):
        error = "oracle tool missing from the fork's tools"
    common.write_record(args.out, args.job, parent, condition, k, **common.answer(result.get("result") or "", error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
