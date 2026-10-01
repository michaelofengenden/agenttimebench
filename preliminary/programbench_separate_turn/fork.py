#!/usr/bin/env python3
"""Ask a finished execution how long it took and how well it did, in five arms.

  fork.py <parent_row_dir> <cell> <arm> <arm_dir> <parent_wall_seconds>     -> one JSON line (a forks.csv row)

native          fork the parent CLI session (Claude: --resume --fork-session; Codex: exec resume in a copied home)
context-only    same, with tools disabled
elapsed-oracle  same, plus an MCP tool that returns the parent's measured wall seconds
replay          rebuild the model-visible transcript from the session file and send it to the provider API
scrubbed        replay with temporal cues redacted (scrubber.py)

Every arm asks MINUTES_QUESTION, then SELF_SCORE_QUESTION in a second turn of the same conversation.
The parent row dir holds the parent's `home/` (CLI state) and `arena/tasks/<id>/workspace/`.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from execute import environment
from invocations import CELLS, expand
from oracle_tool import canonical_elapsed_seconds
from questions import MINUTES_QUESTION, SELF_SCORE_QUESTION, parse_minutes, parse_self_score
from scrubber import scrub_messages

def _plain(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                           separators=(",", ":"))


def _blocks(content):
    """Flatten content blocks to (text, tool-result framings). Thinking/reasoning blocks are dropped."""
    if not isinstance(content, list):
        return _plain(content), []
    chunks, framings = [], []
    for block in content:
        kind = str(block.get("type", "")).lower() if isinstance(block, dict) else None
        if kind in {"text", "input_text", "output_text"}:
            chunks.append(str(block.get("text", "")))
        elif kind in {"thinking", "redacted_thinking", "reasoning"}:
            continue
        elif kind in {"tool_use", "function_call", "custom_tool_call"}:
            payload = block.get("input", block.get("arguments", block.get("params", {})))
            ident = block.get("id", block.get("call_id", ""))
            chunks.append(f"[tool_call name={block.get('name', 'unknown')} id={ident}]\n{_plain(payload)}")
        elif kind in {"tool_result", "function_call_output", "custom_tool_call_output"}:
            ident = block.get("tool_use_id", block.get("call_id", block.get("id", "")))
            framings.append(str(block.get("framing", f"[tool_result id={ident}]")))
            chunks.append(_blocks(block.get("content", block.get("output", block.get("result", ""))))[0])
        else:
            chunks.append(_plain(block))
    return "\n".join(chunks), framings


def _message(role, content, framings=(), metadata=None):
    parts = ([f"[message_metadata {_plain(metadata)}]"] if metadata is not None else []) + \
            (["\n".join(framings)] if framings else [])
    return {"role": role, "content": "\n".join(parts + [content])}


def reconstruct(session_files, harness):
    """Model-visible messages from Claude Code session JSONL or a Codex rollout JSONL."""
    messages = []
    for path in session_files:
        for line in open(path, encoding="utf-8"):
            if not line.strip():
                continue
            record = json.loads(line)
            if harness == "claude":
                message, rtype = record.get("message"), str(record.get("type", "")).lower()
                if isinstance(message, dict) and rtype in {"user", "assistant", "system"}:
                    text, framings = _blocks(message.get("content", ""))
                    meta = message.get("metadata", record.get("model_visible_metadata"))
                    messages.append(_message(str(message.get("role", rtype)), text, framings, meta))
                continue
            payload = record.get("payload")
            if record.get("type") != "response_item" or not isinstance(payload, dict):
                continue
            kind = str(payload.get("type", "")).lower()
            ident = payload.get("call_id", payload.get("id", ""))
            if kind == "message":
                text, framings = _blocks(payload.get("content", ""))
                role = str(payload.get("role", "assistant"))
                messages.append(_message(role, text, framings, payload.get("metadata")))
            elif kind in {"function_call", "custom_tool_call"}:
                args = payload.get("arguments", payload.get("input", {}))
                call = f"[tool_call name={payload.get('name', 'unknown')} id={ident}]"
                messages.append({"role": "assistant", "content": f"{call}\n{_plain(args)}"})
            elif kind in {"function_call_output", "custom_tool_call_output"}:
                framing = str(payload.get("framing", f"[tool_result id={ident}]"))
                messages.append(_message("tool", _plain(payload.get("output", payload.get("content", ""))), [framing]))
    return messages


def session_files(home, harness):
    """The parent's session id and its JSONL file(s)."""
    for path in sorted(Path(home).rglob("*.jsonl")):
        for line in open(path, encoding="utf-8"):
            record = json.loads(line) if line.strip() else {}
            sid = record.get("sessionId") or record.get("session_id")
            if not sid and record.get("type") == "session_meta":
                sid = record["payload"].get("id")
            if sid:
                return sid, sorted(Path(home).glob(f"**/*{sid}*.jsonl"))
    raise FileNotFoundError("no session id in the parent home")


def replay_payload(cell, messages):
    """Request body: the frozen request fields, the model, and the messages with roles mapped. The Anthropic
    role map sends system/developer text as user turns, so the body never has a `system` field."""
    spec = CELLS[cell]["replay"]
    payload = {**json.loads(json.dumps(spec["fields"])), "model": spec["model"]}
    payload["messages"] = [{"role": spec["role_map"].get(m["role"], m["role"]), "content": m["content"]}
                           for m in messages]
    return payload


def post(cell, messages):
    spec = CELLS[cell]["replay"]
    key = Path(os.environ["API_KEY_FILE"]).read_text().strip()
    headers = {"Content-Type": "application/json"}
    headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"} if spec["format"] == "anthropic"
                   else {"Authorization": f"Bearer {key}"})
    body = json.dumps(replay_payload(cell, messages), ensure_ascii=False, separators=(",", ":")).encode()
    with urllib.request.urlopen(urllib.request.Request(spec["url"], body, headers, method="POST")) as resp:
        answer = json.loads(resp.read())
    if "choices" in answer:
        return answer["choices"][0]["message"]["content"]
    return "\n".join(b["text"] for b in answer["content"] if b.get("type") in {"text", "output_text"})


def replay_arm(parent_dir, cell, scrubbed):
    harness = "claude" if cell == "opus-claude" else "codex"
    _, files = session_files(Path(parent_dir) / "home", harness)
    messages = reconstruct(files, harness)
    if scrubbed:
        messages = scrub_messages(messages)
    first = messages + [{"role": "user", "content": MINUTES_QUESTION}]
    a1 = post(cell, first)
    a2 = post(cell, first + [{"role": "assistant", "content": a1},
                             {"role": "user", "content": SELF_SCORE_QUESTION}])
    return a1, a2


def project_slug(path):
    """Claude Code keeps sessions under projects/<cwd with '/', '_' and '.' replaced by '-'>."""
    return str(path).replace("/", "-").replace("_", "-").replace(".", "-")


def add_oracle(home, cell, elapsed):
    """Register the oracle MCP server in the arm's copied CLI config."""
    tool = home / ".timeablations-tools" / "oracle_tool.py"   # verbatim: the CLI's MCP config names this path
    tool.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(Path(__file__).with_name("oracle_tool.py"), tool)
    tool.chmod(0o500)
    relative, shape = CELLS[cell]["oracle_config"]
    values = {"python": sys.executable, "oracle_tool": str(tool.resolve()),
              "elapsed_seconds": canonical_elapsed_seconds(str(elapsed))}
    shape = json.loads(expand([json.dumps(shape)], values)[0])
    config = home / relative
    config.parent.mkdir(parents=True, exist_ok=True)
    if relative.endswith(".json"):
        data = json.loads(config.read_text()) if config.exists() else {}
        data.setdefault("mcpServers", {}).update(shape["mcpServers"])
        config.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:  # Codex config.toml: append the server table
        server = shape["mcp_servers"]["elapsed"]
        with open(config, "a") as f:
            f.write(f'\n[mcp_servers.elapsed]\ncommand = {json.dumps(server["command"])}\n'
                    f'args = {json.dumps(server["args"])}\n\n[mcp_servers.elapsed.env]\n'
                    f'ELAPSED_SECONDS = {json.dumps(server["env"]["ELAPSED_SECONDS"])}\n')


def codex_stderr_answer(stderr):
    """`codex exec resume` may print the pre-turn message on stdout; the new answer is the block after
    the last line reading `codex` in the stderr transcript, up to the `tokens used` footer."""
    lines = stderr.splitlines()
    marks = [i for i, line in enumerate(lines) if line.strip() == "codex"]
    if not marks:
        return None
    block = []
    for line in lines[marks[-1] + 1:]:
        if line.strip().lower().startswith("tokens used"):
            break
        block.append(line)
    return "\n".join(block).strip() or None


def _turn(argv, cwd, env):
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"fork turn exited with status {result.returncode}")
    return result


def _last_json(stdout):
    """The JSON envelope: the whole output, else the last line that parses."""
    for candidate in [stdout.strip(), *reversed(stdout.strip().splitlines())]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("no JSON in fork output")


def parent_workspace(parent_dir):
    """<parent_row_dir>/arena/tasks/<task_id>/workspace"""
    return next((Path(parent_dir) / "arena" / "tasks").glob("*/workspace"))


def native_arm(parent_dir, cell, arm, arm_dir, elapsed):
    parent_dir, arm_dir = Path(parent_dir), Path(arm_dir)
    home, workspace = arm_dir / "home", arm_dir / "workspace"
    shutil.copytree(parent_dir / "home", home, symlinks=True)
    shutil.copytree(parent_workspace(parent_dir), workspace, symlinks=True)
    projects = home / "projects"
    if projects.is_dir():  # re-key the copied Claude session to the arm's working directory
        (only,) = [p for p in projects.iterdir() if p.is_dir()]
        only.rename(projects / project_slug(workspace))
    if arm == "elapsed-oracle":
        add_oracle(home, cell, elapsed)
    parent_sid, _ = session_files(home, "claude" if cell == "opus-claude" else "codex")
    turn1, turn2 = CELLS[cell]["fork"][arm]
    env = environment(cell, home, arm_dir, TIMEABLATIONS_WORKSPACE=str(workspace),
                      TIMEABLATIONS_PARENT_ROW_ID=parent_dir.name)
    r1 = _turn(expand(turn1, {"prompt": MINUTES_QUESTION, "parent_session_id": parent_sid}), workspace, env)
    if cell == "opus-claude":  # --output-format json: answer in `result`, the fork's id in `session_id`
        out1 = _last_json(r1.stdout)
        a1, fork_sid = out1["result"], out1["session_id"]
    else:
        a1, fork_sid = r1.stdout, parent_sid
    r2 = _turn(expand(turn2, {"prompt": SELF_SCORE_QUESTION, "fork_session_id": fork_sid,
                              "parent_session_id": fork_sid}), workspace, env)
    a2 = _last_json(r2.stdout)["result"] if cell == "opus-claude" else r2.stdout
    if cell == "sol-codex":
        if parse_minutes(a1) is None and parse_minutes(codex_stderr_answer(r1.stderr) or "") is not None:
            a1 = codex_stderr_answer(r1.stderr)
        if parse_self_score(a2) is None and parse_self_score(codex_stderr_answer(r2.stderr) or "") is not None:
            a2 = codex_stderr_answer(r2.stderr)
    return a1, a2


def main(parent_dir, cell, arm, arm_dir, elapsed):
    if arm in ("replay", "scrubbed"):
        a1, a2 = replay_arm(parent_dir, cell, arm == "scrubbed")
    else:
        a1, a2 = native_arm(parent_dir, cell, arm, arm_dir, elapsed)
    minutes, score = parse_minutes(a1), parse_self_score(a2)
    status = "completed" if minutes is not None and score is not None else "parse_invalid"
    print(json.dumps({"task_id": parent_workspace(parent_dir).parent.name, "cell": cell, "arm": arm, "status": status,
                      "minutes": minutes, "self_score": score}))


if __name__ == "__main__":
    main(*sys.argv[1:6])
