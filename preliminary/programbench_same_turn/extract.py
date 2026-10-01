#!/usr/bin/env python3
"""Turn one agent log into a record, and a set of runs into records.csv (the input of analyze.py).

  extract.py record <claude|codex> <instance_id> <duration_seconds> <log>   -> one JSON record (run.sh)
  extract.py fill-input <claude log>                                        -> redacted transcript
  extract.py table <records_dir> <logs_dir> <evals_dir> [fills.json]        -> records.csv on stdout

Self-reports use the last match in the agent's visible text. Scores use the <id>.eval.json that the native
grader (`programbench eval runs/<agent>_run`, programbench 1.2.2) writes per task; <evals_dir>/<agent>/ holds
them at any depth (e.g. a link to that run dir). fills.json maps instance_id to the PERCEIVED_MINUTES value
(last match) in the out-of-context fill answer, for Claude runs that printed none (see fill_input).
"""
import csv
import json
import re
import sys
from pathlib import Path

ESTIMATE = r"ESTIMATE_MINUTES\s*=\s*([0-9.]+)"
PERCEIVED = r"PERCEIVED_MINUTES\s*=\s*([0-9.]+)"
SELF_SCORE = r"SELF_SCORE\s*=\s*([0-9.]+)"

# Redaction for the out-of-context PERCEIVED_MINUTES fill (perceived_fill_prompt.txt).
REDACTIONS = [
    (re.compile(r"(ESTIMATE_MINUTES|PERCEIVED_MINUTES|SELF_SCORE)\s*=?\s*[0-9.]+", re.I),
     "[SELF-REPORT REDACTED]"),
    (re.compile(r"\b\d+(\.\d+)?\s*(wall[- ]?clock\s*)?(minutes?|mins?|hours?|hrs?)\b", re.I),
     "[DURATION REDACTED]"),
    (re.compile(r"(finished[^|]{0,40}in\s+\d+\.?\d*\s*s\b|\breal\s+\d+m[\d.]+s?|\buser\s+\d+m[\d.]+s?"
                r"|\bsys\s+\d+m[\d.]+s?|\bin\s+\d+\.\d+\s*s(ec(onds)?)?\b|\d+\.\d+\s*sec(ond)?s?\b"
                r"|elapsed[^|]{0,14}\d[\d.:]*|\d+\s*ms\b|\bwall.?clock[^|]{0,16}\d[\d.:]*)", re.I),
     "[ELAPSED REDACTED]"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?Z?|\b\d{1,2}:\d{2}:\d{2}\b"
                r"|\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\b"
                r"|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}\s+(\d{1,2}:\d{2}|\d{4})\b"),
     "[TIMESTAMP REDACTED]"),
]
MAX_EVENTS, TARGET_CHARS = 1400, 110_000


def json_lines(path):
    for line in open(path, errors="replace"):
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict):
            yield d


def visible_text(path, agent):
    """Assistant text the regexes search: Claude text+thinking blocks, Codex message/reasoning items."""
    texts = []
    for d in json_lines(path):
        if agent == "claude" and d.get("type") == "assistant":
            for c in d.get("message", {}).get("content", []):
                if c.get("type") in ("text", "thinking"):
                    texts.append(c.get("text") or c.get("thinking") or "")
        elif agent == "codex" and d.get("type") == "item.completed":
            item = d.get("item")
            if isinstance(item, dict) and item.get("type") in ("agent_message", "reasoning", "agent_reasoning") \
                    and item.get("text"):
                texts.append(item["text"])
    return "\n".join(texts)


def last_value(pattern, text):
    found = re.findall(pattern, text)
    return float(found[-1]) if found else None


def record(agent, instance_id, duration_seconds, log):
    text = visible_text(log, agent)
    return {"instance_id": instance_id, "agent": agent,
            "actual_minutes": round(float(duration_seconds) / 60, 3),
            "estimate_minutes": last_value(ESTIMATE, text),
            "perceived_minutes": last_value(PERCEIVED, text),
            "self_score": last_value(SELF_SCORE, text)}


def events(path, agent):
    """Visible events in order (agent text, tool calls, tool outputs) as (prefix, text, line limit, full text)."""
    out = []
    for d in json_lines(path):
        if agent == "claude" and d.get("type") == "assistant":
            for c in d.get("message", {}).get("content", []):
                if c.get("type") == "text" and (c.get("text") or "").strip():
                    out.append(("[agent] ", c["text"], 300, c["text"]))
                elif c.get("type") == "tool_use":
                    inp = c.get("input", {})
                    arg = inp.get("command") or inp.get("file_path") or json.dumps(inp)[:120]
                    out.append((f"[{c.get('name', 'tool').lower()}] ", arg, 160, json.dumps(inp, ensure_ascii=False)))
        elif agent == "claude" and d.get("type") == "user":
            content = d.get("message", {}).get("content")
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    r = c.get("content", "")
                    if isinstance(r, list):
                        r = " ".join(x.get("text", "") for x in r if isinstance(x, dict))
                    out.append(("[out] ", r, 160, r))
        elif agent == "codex" and d.get("type") == "item.completed":
            item = d.get("item") or {}
            kind = item.get("type") or item.get("item_type")
            if kind == "agent_message":
                out.append(("[agent] ", item.get("text"), 300, item.get("text")))
            elif kind == "command_execution":
                out.append(("[bash] ", item.get("command"), 160, item.get("command")))
                output = item.get("aggregated_output") or item.get("output") or ""
                if output:
                    out.append(("[out] ", output, 160, output))
    return out


def redact(text):
    for pattern, marker in REDACTIONS:
        text = pattern.sub(marker, text)
    return text


def compact(path, agent, omitted, middle):
    """One redacted line per event, capped at 1,400 events and 110k characters."""
    lines = [prefix + redact(" ".join(str(value or "").split()))[:limit]
             for prefix, value, limit, _ in events(path, agent)]
    if len(lines) > MAX_EVENTS:
        head = MAX_EVENTS * 2 // 3
        note = omitted.format(n=len(lines) - MAX_EVENTS, total=len(lines))
        lines = lines[:head] + [note] + lines[-(MAX_EVENTS - head):]
    body = "\n".join(lines)
    if len(body) > TARGET_CHARS:
        head = body[:TARGET_CHARS * 2 // 3]
        body = head + middle + body[-(TARGET_CHARS - len(head)):]
    return body


def fill_input(path):
    """Duration-stripped Claude transcript for runs that printed no PERCEIVED_MINUTES. perceived_fill_prompt.txt
    followed by this text went on stdin to
    `claude --print --model claude-opus-4-8 --disallowedTools "WebFetch,WebSearch,Bash,Read,Edit,Write,Glob,Grep"`."""
    return compact(path, "claude", "[... {n} events omitted for length; total events in run: {total} ...]",
                   "\n[... transcript middle omitted for length ...]\n")


def length_measures(path, agent):
    """Transcript-length baselines for the ranking comparison."""
    ev = events(path, agent)
    compacted = compact(path, agent, "[... {n} events omitted; total events: {total} ...]",
                        "\n[... transcript middle omitted ...]\n")
    return {"visible_event_count": len(ev),
            "full_visible_chars": sum(len(str(full)) for *_, full in ev),
            "raw_log_bytes": Path(path).stat().st_size,
            "compacted_chars": len(compacted)}


def model_of(path, agent):
    """Model id the Claude CLI reports in its stream-json init event. Codex logs do not record one."""
    if agent == "claude":
        for d in json_lines(path):
            if d.get("type") == "system" and d.get("subtype") == "init":
                return d.get("model")
    return None


def is_cutoff(path, agent):
    """Interrupted run: Claude's final result event is an error; Codex has an unfinished or failed turn."""
    if agent == "claude":
        result = None
        for line in open(path, errors="replace"):
            if '"type":"result"' in line:
                try:
                    result = json.loads(line)
                except json.JSONDecodeError:
                    pass
        return bool(result and result.get("is_error"))
    started = completed = failed = 0
    for line in open(path, errors="replace"):
        started += '"turn.started"' in line
        completed += '"turn.completed"' in line
        failed += '"turn.failed"' in line
    return completed < started or failed > 0 or completed == 0


def score_of(eval_json):
    """Test-pass rate: passed / (passed + failed) over the eval.json test results, in percent."""
    tests = json.load(open(eval_json)).get("test_results") or []
    passed = sum(t.get("status") == "passed" for t in tests)
    failed = sum(t.get("status") == "failure" for t in tests)
    return round(100 * passed / (passed + failed)) if passed + failed else 0


COLUMNS = ["instance_id", "agent", "model", "actual_minutes", "estimate_minutes", "perceived_minutes",
           "perceived_source", "self_score", "test_pass_score", "cutoff", "visible_event_count",
           "full_visible_chars", "raw_log_bytes", "compacted_chars"]


def table(records_dir, logs_dir, evals_dir, fills):
    """records/<id>.<agent>.json + logs/<id>.<agent>.log + evals/<agent>/**/<id>.eval.json -> CSV rows."""
    writer = csv.DictWriter(sys.stdout, COLUMNS, lineterminator="\n")
    writer.writeheader()
    for agent in ("claude", "codex"):
        for eval_json in sorted(Path(evals_dir, agent).rglob("*.eval.json"), key=lambda p: p.name):
            iid = eval_json.name.removesuffix(".eval.json")
            rec_path = Path(records_dir, f"{iid}.{agent}.json")
            if not rec_path.exists():
                continue
            rec, log = json.loads(rec_path.read_text()), Path(logs_dir, f"{iid}.{agent}.log")
            perceived, source = rec["perceived_minutes"], "in-context"
            if perceived is None:  # Claude runs without an in-context report: filled out of context
                perceived, source = fills.get(iid) if agent == "claude" else None, "transcript-recovered"
            writer.writerow({"instance_id": iid, "agent": agent, "model": model_of(log, agent),
                             "actual_minutes": rec["actual_minutes"],
                             "estimate_minutes": rec["estimate_minutes"], "perceived_minutes": perceived,
                             "perceived_source": source if perceived is not None else "",
                             "self_score": rec["self_score"], "test_pass_score": score_of(eval_json),
                             "cutoff": int(is_cutoff(log, agent)), **length_measures(log, agent)})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "record":
        print(json.dumps(record(*args)))
    elif cmd == "fill-input":
        print(fill_input(args[0]), end="")
    elif cmd == "table":
        table(*args[:3], json.loads(Path(args[3]).read_text()) if len(args) > 3 else {})
    else:
        sys.exit(__doc__)
