"""R-oracle, R-native and R-context-only for GPT-5.6 Sol and GPT-6 Astra parents: `codex exec resume` of a copy of the
parent's Codex session, asking QUESTION once.

    python fork_codex.py --job RUNID__native__k1 --parents parents.csv --sessions DIR [--out results]

A fresh CODEX_HOME holds a copy of the parent rollout (noreq_rollout): hidden reasoning goes (reasoning items,
encrypted compaction summaries, reasoning events), request_removal.strip_request_obj removes the requested duration
and every restatement of it from all text leaves at once, emptied messages go, and API-issued item ids are dropped.
config.toml (write_config) keeps the parent's model name and reasoning effort and points Codex at codex_proxy.py;
R-oracle adds the stdio MCP server "elapsed" (docker/oracle_tool.py). In the agenttime-retro:1 container RUN_SH starts
the proxy as root, recreates the parent's cwd empty and runs Codex 0.156.1 as an unprivileged user with a clean
environment and the ARM_FLAGS of the condition. The answer is the last agent_message of the --json output, else the
last assistant message in the rollout tail.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import common
from request_removal import strip_request_obj

PROXY_PORT = 18080
TIMEOUT_S = 20 * 60
_ROLLOUT_NAME = re.compile(r"(rollout-(\d{4})-(\d{2})-(\d{2})T[0-9-]+-[0-9a-f-]{36}\.jsonl)$")
_SERVER_ID = re.compile(r"^[a-z]+_[0-9a-f]{30,}$")     # ids the OpenAI API issued (msg_/fc_/ctc_ + hex)
# String fields that identify, order or configure records: never prose, never edited.
_KEEP_KEYS = frozenset({
    "type", "id", "call_id", "timestamp", "thread_id", "turn_id", "session_id", "root_turn_id", "response_id",
    "window_id", "first_window_id", "previous_window_id", "status", "role", "name", "namespace", "phase",
    "cli_version", "originator", "source", "thread_source", "history_mode", "model_provider", "model", "comp_hash",
    "cwd", "workspace_roots", "current_date", "timezone", "effort", "reasoning_effort", "summary", "approval_policy",
    "approvals_reviewer", "personality", "multi_agent_version", "process_id", "connectorId", "linkId",
    "encrypted_content", "path"})
ARM_FLAGS = {"native": [], "oracle": [],
             "context-only": ["--disable", "shell_tool", "--disable", "unified_exec", "--disable", "goals",
                              "--disable", "view_image"]}

# Runs as root only so that the proxy holding the OpenRouter key is a process the agent user cannot inspect. Codex
# runs as the unprivileged user "agent" with a clean environment and never sees the key.
RUN_SH = r"""#!/bin/bash
set -u
PROXY_LOG=/job/proxy.jsonl python3 /job/codex_proxy.py 2>/job/proxy.err &
for i in $(seq 1 50); do
  python3 -c "import socket; socket.create_connection(('127.0.0.1', PORT), 0.2)" 2>/dev/null && break
  sleep 0.1
done
mkdir -p "CWD" && chown agent:agent "CWD"
d="CWD"; while [ "$d" != "/" ]; do chmod o+x "$d"; d=$(dirname "$d"); done
cd "CWD" || cd /workspace
date -u +%s.%N > /job/t_start
env -i CODEX_HOME=/job/codex-home HOME=/home/agent PATH="$PATH" TERM=dumb LANG=C.UTF-8 \
  setpriv --reuid=1001 --regid=1001 --init-groups -- \
  timeout INNER codex exec resume --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox \
  FLAGS SESSION_ID - < /job/question.txt > /job/stdout.jsonl 2> /job/stderr.txt
echo $? > /job/rc
date -u +%s.%N > /job/t_end
"""


def read_jsonl(path):
    out = []
    if Path(path).exists():
        for line in open(path, encoding="utf-8", errors="replace"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _is_reasoning(item):
    return isinstance(item, dict) and (item.get("type") in ("reasoning", "compaction") or "encrypted_content" in item)


def _text_slots(obj, out):
    """(container, key) of every string leaf not under a structural key."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in _KEEP_KEYS:
                continue
            if isinstance(v, str):
                out.append((obj, k))
            else:
                _text_slots(v, out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                out.append((obj, i))
            else:
                _text_slots(v, out)


def _drop_empty_parts(content):
    """Message content without the text parts the removal emptied; None if nothing is left."""
    if not isinstance(content, list):
        return content if (not isinstance(content, str) or content.strip()) else None
    kept = [c for c in content if not (isinstance(c, dict) and isinstance(c.get("text"), str) and not c["text"].strip()
                                       and set(c) <= {"type", "text", "annotations"})]
    return kept or None


def noreq_rollout(path, request_min):
    """Rewrite the rollout copy at `path` in place: no hidden reasoning, no requested duration, no API item ids."""
    kept = []
    for r in read_jsonl(path):                         # 1. hidden reasoning
        pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
        if r.get("type") == "response_item" and _is_reasoning(pl):
            continue
        if r.get("type") == "event_msg" and (pl.get("type") in ("agent_reasoning", "agent_reasoning_raw_content",
                                                                 "agent_reasoning_section_break")
                                             or (pl.get("type") == "item_completed"
                                                 and (pl.get("item") or {}).get("type") == "Reasoning")):
            continue
        if r.get("type") == "compacted" and isinstance(pl.get("replacement_history"), list):
            pl["replacement_history"] = [it for it in pl["replacement_history"] if not _is_reasoning(it)]
        kept.append(r)
    slots = []                                         # 2. the request, over every text leaf of every record at once
    for r in kept:
        _text_slots(r, slots)
    before = [c[k] for c, k in slots]
    for (c, k), old, new in zip(slots, before, strip_request_obj(before, request_min)):
        if new is not old and new != old:
            c[k] = new
    dropped_ids, out = set(), []                       # 3. emptied messages go (tool calls and outputs never do)
    for r in kept:
        pl = r.get("payload") or {}
        if r.get("type") == "response_item" and pl.get("type") == "message":
            content = _drop_empty_parts(pl.get("content"))
            if content is None:
                dropped_ids.add(pl.get("id"))
                continue
            pl["content"] = content
        if r.get("type") == "compacted" and isinstance(pl.get("replacement_history"), list):
            hist = []
            for it in pl["replacement_history"]:
                if isinstance(it, dict) and it.get("type") == "message":
                    content = _drop_empty_parts(it.get("content"))
                    if content is None:
                        continue
                    it["content"] = content
                hist.append(it)
            pl["replacement_history"] = hist
        out.append(r)
    with open(path, "w", encoding="utf-8") as f:
        for r in out:
            pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            it = (pl.get("item") or {}) if r.get("type") == "event_msg" else {}
            if it.get("type") in ("AgentMessage", "UserMessage"):    # the events of emptied messages
                content = _drop_empty_parts(it.get("content"))
                if content is None or it.get("id") in dropped_ids:
                    continue
                it["content"] = content
            # 4. the API-issued item ids (their reasoning is gone)
            items = [pl] if r.get("type") == "response_item" else \
                (pl.get("replacement_history") or []) if r.get("type") == "compacted" else []
            for it in items:
                if isinstance(it, dict) and isinstance(it.get("id"), str) and _SERVER_ID.match(it["id"]):
                    del it["id"]
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")


def write_config(home, model, effort, condition, elapsed_seconds):
    lines = [f"model = {json.dumps(model)}", 'model_provider = "openrouter"',
             f"model_reasoning_effort = {json.dumps(effort)}"]
    if condition == "context-only":
        lines.append('web_search = "disabled"')
    lines += ["", "[model_providers.openrouter]", 'name = "OpenRouter"',
              f'base_url = "http://127.0.0.1:{PROXY_PORT}/api/v1"', 'wire_api = "responses"',
              "stream_idle_timeout_ms = 900000", "request_max_retries = 2", "stream_max_retries = 2"]
    if condition == "oracle":
        lines += ["", "[mcp_servers.elapsed]", 'command = "python3"', 'args = ["/opt/retro/oracle_tool.py"]',
                  f"env = {{ ELAPSED_SECONDS = {json.dumps(elapsed_seconds)} }}", 'omit_tools_from = ["code_mode"]',
                  "startup_timeout_sec = 30"]
    (home / "config.toml").write_text("\n".join(lines) + "\n")


def prepare(tmp, src, parent, condition):
    """Lay out the job dir mounted at /job. Returns (rollout path inside CODEX_HOME, lines in the copy)."""
    recs = read_jsonl(src)
    meta = next(r["payload"] for r in recs if r.get("type") == "session_meta")
    contexts = [r.get("payload") or {} for r in recs if r.get("type") == "turn_context"]
    m = _ROLLOUT_NAME.search(Path(src).name)
    rel = Path("sessions", m.group(2), m.group(3), m.group(4), m.group(1))
    home = tmp / "codex-home"
    dst = home / rel
    dst.parent.mkdir(parents=True)
    shutil.copyfile(src, dst)
    noreq_rollout(dst, parent["request_min"])
    model = next(c["model"] for c in reversed(contexts) if c.get("model"))       # the last recorded model and effort
    effort = next(c["effort"] for c in reversed(contexts) if c.get("effort"))
    write_config(home, model, effort, condition, common.elapsed_seconds(parent["truth_min"]))
    shutil.copyfile(common.HERE / "codex_proxy.py", tmp / "codex_proxy.py")
    (tmp / "question.txt").write_text(common.QUESTION)
    (tmp / "run.sh").write_text(RUN_SH.replace("PORT", str(PROXY_PORT)).replace("CWD", meta["cwd"])
                                .replace("INNER", str(TIMEOUT_S - 30)).replace("FLAGS", " ".join(ARM_FLAGS[condition]))
                                .replace("SESSION_ID", meta["id"]))
    os.chmod(tmp, 0o777)
    for p in tmp.rglob("*"):
        os.chmod(p, 0o777 if p.is_dir() else 0o666)
    return rel, sum(1 for _ in open(dst, encoding="utf-8"))


def run_container(tmp, parent, condition):
    """Returns True on timeout."""
    name = f"retro-codex-{uuid.uuid4().hex[:12]}"
    agent = common.AGENTS[parent["agent"]]
    env = {**os.environ, "OPENROUTER_API_KEY": common.openrouter_key(), "PROXY_PROVIDER": agent["provider"],
           "PROXY_MODEL": agent["openrouter"], "PROXY_STRIP_TOOLS": "1" if condition == "context-only" else "0"}
    cmd = ["docker", "run", "--rm", "--name", name, "--user", "root", "--memory", "4g", "--cpus", "2",
           "--pids-limit", "1024", "--security-opt", "no-new-privileges",
           "-e", "OPENROUTER_API_KEY", "-e", "PROXY_PROVIDER", "-e", "PROXY_MODEL", "-e", "PROXY_STRIP_TOOLS",
           "-e", f"PROXY_PORT={PROXY_PORT}", "-v", f"{tmp}:/job", common.DOCKER_IMAGE, "bash", "/job/run.sh"]
    try:
        subprocess.run(cmd, env=env, capture_output=True, timeout=TIMEOUT_S)
        return False
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "kill", name], capture_output=True)
        return True


def read_outputs(tmp, rel, n_lines):
    """(final answer, tools offered to the model, CLI errors, whether the turn failed)."""
    events = read_jsonl(tmp / "stdout.jsonl")
    texts = [e["item"].get("text") or "" for e in events
             if e.get("type") == "item.completed" and (e.get("item") or {}).get("type") == "agent_message"]
    if not texts:                                      # fall back to the rollout tail
        tail = read_jsonl(tmp / "codex-home" / rel)[n_lines:]
        texts = ["".join(c.get("text", "") for c in pl.get("content") or []) for pl in
                 (r.get("payload") or {} for r in tail if r.get("type") == "response_item")
                 if pl.get("type") == "message" and pl.get("role") == "assistant"]
    offered = next((r["offered"] for r in read_jsonl(tmp / "proxy.jsonl") if "offered" in r), None)
    errors = [(e.get("item") or {}).get("message") or e.get("message") for e in events
              if e.get("type") in ("error", "turn.failed")
              or (e.get("type") == "item.completed" and (e.get("item") or {}).get("type") == "error")]
    failed = any(e.get("type") == "turn.failed" for e in events)
    return (texts[-1] if texts else ""), offered, [e for e in errors if e][:5], failed


def main(argv=None):
    args, parent, condition, k = common.runner_args(argv, "Codex forks")
    if common.AGENTS[parent["agent"]]["session_format"] != "codex" or condition not in common.FORK_CONDITIONS:
        sys.exit(f"{args.job}: fork_codex.py runs the fork conditions of Codex parents only")
    tmp = Path(tempfile.mkdtemp(prefix="retro-codex-"))
    try:
        rel, n_lines = prepare(tmp, common.session_file(args.sessions, parent["run_id"]), parent, condition)
        timed_out = run_container(tmp, parent, condition)
        final_text, offered, errors, failed = read_outputs(tmp, rel, n_lines)
        rc = (tmp / "rc").read_text().strip() if (tmp / "rc").exists() else None
    except Exception as exc:
        common.write_record(args.out, args.job, parent, condition, k,
                            **common.answer("", f"{type(exc).__name__}: {exc}"[:1000]))
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    error = None
    if timed_out:
        error = f"timeout after {TIMEOUT_S}s"
    elif rc != "0" or failed or not final_text:
        error = json.dumps({"codex_rc": rc, "errors": errors})[:3000]
    elif condition == "oracle" and "elapsed_seconds" not in json.dumps(offered):
        error = "oracle condition: the oracle tool was not in the model's tool list"
    elif condition == "context-only" and re.search(r"[A-Za-z_]{3,}", json.dumps(offered)):
        error = f"context-only condition: tools were offered: {json.dumps(offered)[:300]}"
    common.write_record(args.out, args.job, parent, condition, k, **common.answer(final_text, error))
    return 0


if __name__ == "__main__":
    sys.exit(main())
