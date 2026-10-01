"""Tests for retrospective_forks on small synthetic inputs (no session logs, no results)."""
import csv
import hashlib
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "retrospective_forks"
_NAMES = ("common", "reconstruct", "request_removal", "scrub", "replay", "select_parents", "analyze", "fork_claude",
          "fork_codex", "press", "run_all", "codex_proxy")


def _import(names):
    """Import the module's files without leaving their generic names (common, ...) in sys.modules, and without
    codex_proxy taking OPENROUTER_API_KEY out of this process's environment."""
    saved = {n: sys.modules.pop(n) for n in names if n in sys.modules}
    key = os.environ.get("OPENROUTER_API_KEY")
    sys.path.insert(0, str(HERE))
    try:
        return {n: importlib.import_module(n) for n in names}
    finally:
        sys.path.remove(str(HERE))
        for n in names:
            sys.modules.pop(n, None)
        sys.modules.update(saved)
        if key is not None:
            os.environ["OPENROUTER_API_KEY"] = key


M = _import(_NAMES)
_spec = importlib.util.spec_from_file_location("retro_oracle_tool", HERE / "docker" / "oracle_tool.py")
M["oracle_tool"] = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M["oracle_tool"])
common, reconstruct, rr, scrub, replay = M["common"], M["reconstruct"], M["request_removal"], M["scrub"], M["replay"]
TEMPLATE = ("Please work on this task for a full {} minutes. Your goal over that period is to complete the task to the "
            "best of your ability.")


# --- answers
@pytest.mark.parametrize("text, minutes", [
    ("minutes = 12", 12.0), ("return minutes = 7.5", 7.5), ("Minutes=~1.5", 1.5), ("minutes = about 3", 3.0),
    ("minutes = 4\n...\nminutes = 9", 9.0), ("It took 5 minutes.", None), ("", None), ("totalminutes = 3", None)])
def test_parse_minutes(text, minutes):
    assert common.parse_minutes(text) == minutes


def test_elapsed_seconds():
    assert common.elapsed_seconds(15.5) == "930"
    assert common.elapsed_seconds(3.1234567) == "187.407"


# --- request removal
@pytest.mark.parametrize("R, text, expected", [
    (17, "Tally the rows in ledger.csv and print the total.\n\n" + TEMPLATE.format(17),
     "Tally the rows in ledger.csv and print the total."),
    (2.5, "Rename the photos by date.\n\n\n" + TEMPLATE.format(2.5), "Rename the photos by date."),
    (17, "Lint the parser for the full 17 minutes, then print the warnings.", "Lint the parser, then print the warnings."),
    (17, "Over the requested 17-minute window, compare both CSV exports.", "Compare both CSV exports."),
    (17, "Spend the rest of the period on the unit tests.", "Do the unit tests."),
    (17, "The export is written; roughly four minutes remain before I hand it over.", "The export is written."),
    (17, "The parquet check failed within the 1020-second budget, so I fell back to the CSV reader.",
     "The parquet check failed, so I fell back to the CSV reader."),
    (17, "I am pacing the work to use all 17 minutes, but the answer is already final.", "The answer is already final."),
    (17, "Seventeen minutes is a long time for this check. The checksum matches.", "The checksum matches."),
    (17, "I cannot stretch this further. The checksum matches.", "The checksum matches."),
    (17, "With 17 minutes requested, I ran the fuzz pass twice.", "I ran the fuzz pass twice."),
    (17, "Only 40 seconds have elapsed, short of the requested 17 minutes.", "40 seconds have elapsed."),
    (17, "cfg = {'requested_secs': 1020, 'retries': 3}", "cfg = {'retries': 3}"),
    (17, "log.update({'requested_period_s': 1020, 'rows': 88})", "log.update({'rows': 88})"),
    (17, "if elapsed < 1020:\n    time.sleep(5)\nprint('ok')", "print('ok')"),
    (17, 'secs=1020  # budget\ntimeout "${secs}s" make check', "make check"),
])
def test_request_removed(R, text, expected):
    out = rr.strip_request(text, R)
    assert out == expected
    assert rr.strip_request(out, R) == out


@pytest.mark.parametrize("text", [
    "The sweep stopped after 17 minutes 4 seconds.",              # a measurement, not the budget
    "Wall time 1020.0 seconds",
    "A 17-minute.json sidecar is written next to the report.",
    "Let the dough rise for 17 to 20 minutes.",
    "sleep 17",
    "I will take a moment to double-check the units.",
    "The run is near the 8-minute mark and the loss is falling.",
])
def test_left_alone(text):
    assert rr.strip_request(text, 17) is text


def test_deadlines_across_leaves():
    # an end exactly the budget after a start is a deadline, in every spelling; a clock reading keeps its value
    out = rr.strip_request_obj(["job_start=2031-05-04T10:00:00Z job_end=2031-05-04T10:17:00Z",
                                "The job must stop at 10:17:00 UTC.", '{"now": "2031-05-04T10:17:00Z"}'], 17)
    assert out == ["", "", '{"now": "2031-05-04T10:17:00Z"}']
    assert rr.strip_request_obj(["deadline_epoch: 1951637820", "The sweep must finish before 1951637820."], 17) == ["", ""]


def test_object_walk_keeps_keys_and_untouched_leaves():
    obj = {"content": [{"type": "input_text", "text": "Q.\n\n" + TEMPLATE.format(17)}, {"text": "unrelated", "n": 1020}],
           "requested 17 minutes": ["Lint the parser for the full 17 minutes."]}
    out = rr.strip_request_obj(obj, 17)
    assert out == {"content": [{"type": "input_text", "text": "Q."}, {"text": "unrelated", "n": 1020}],
                   "requested 17 minutes": ["Lint the parser."]}
    assert out["content"][1]["text"] is obj["content"][1]["text"]
    assert "Please work" in obj["content"][0]["text"]                 # the input is not modified


# --- scrub
def test_scrub_messages():
    msgs = [
        {"role": "user", "content": "Sum column B of sales.tsv.\n\n" + TEMPLATE.format(17)},
        {"role": "assistant", "content": "First I read the clock. The file is tab separated."},
        {"role": "assistant", "content": "[tool_call name=mcp__clock__current_time id=k1]\n{}"},
        {"role": "user", "content": '[tool_result id=k1]\n{"utc":"2031-05-04T10:00:00Z"}'},
        {"role": "assistant", "content": '[tool_call name=Bash id=k2]\n{"command":"cut -f2 sales.tsv | paste -sd+ | bc; sleep 20"}'},
        {"role": "user", "content": "[tool_result id=k2]\n7310"},
        {"role": "assistant", "content": '[tool_call name=Bash id=k3]\n{"command":"ls -l --full-time sales.tsv"}'},
        {"role": "user", "content": "[tool_result id=k3]\n-rw-rw-r-- 1 dev dev 2048 2031-05-04 10:00:41.000000000 +0000 sales.tsv"},
        {"role": "assistant", "content": '[tool_call name=Bash id=k4]\n{"command":"python3 check.py"}'},
        {"role": "user", "content": "[tool_result id=k4]\nchecked in 0.8s\nsum=7310"},
        {"role": "assistant", "content": '[tool_call name=sleep id=k5]\n{"seconds":90}'},
        {"role": "user", "content": "[tool_result id=k5]\nok"},
        {"role": "assistant", "content": "Column B sums to 7310. All of that took about four minutes. Done."},
    ]
    out = scrub.scrub_messages(msgs, 17, "2031")
    assert [m["content"] for m in out] == [
        "Sum column B of sales.tsv.", "The file is tab separated.", "", "",
        '[tool_call name=Bash id=k2]\n{"command":"cut -f2 sales.tsv | paste -sd+ | bc"}', "[tool_result id=k2]\n7310",
        '[tool_call name=Bash id=k3]\n{"command":"ls -l sales.tsv"}', "[tool_result id=k3]\n-rw-rw-r-- 1 dev dev sales.tsv",
        '[tool_call name=Bash id=k4]\n{"command":"python3 check.py"}', "[tool_result id=k4]\nchecked\nsum=7310", "", "",
        "Column B sums to 7310. Done."]
    assert [m["role"] for m in out] == [m["role"] for m in msgs]


@pytest.mark.parametrize("command, output, kept", [
    ("python3 a.py", "began 1951642800 at 10:00:00 UTC, eta: 3m", ""),
    ("python3 a.py", "stage 2 ended at 2031-05-04 10:00:00 UTC (took 9.1s)", "stage 2 ended\n"),
    ("date -u; python3 a.py", "Sun May  4 10:00:00 UTC 2031", ""),
])
def test_scrub_tool_output_lines(command, output, kept):
    msgs = [{"role": "assistant", "content": "[tool_call name=Bash id=x]\n" + json.dumps({"command": command}, separators=(",", ":"))},
            {"role": "user", "content": f"[tool_result id=x]\n{output}\nanswer=11"}]
    assert [m["content"] for m in scrub.scrub_messages(msgs, 17, "2031")] == [
        '[tool_call name=Bash id=x]\n{"command":"python3 a.py"}', f"[tool_result id=x]\n{kept}answer=11"]


def test_scrub_drops_codex_sleep_call_pairs():
    msgs = [{"role": "assistant", "content": "Next step."},
            {"role": "assistant", "content": '[tool_call name=exec id=x]\nconst done=tools.exec_command({cmd: "sleep 600"});'},
            {"role": "user", "content": "[tool_result id=x]\nok"}, {"role": "assistant", "content": "All set."}]
    assert [m["content"] for m in scrub.scrub_messages(msgs, 17, "2031")] == ["Next step.", "", "", "All set."]


# --- reconstruction and request bodies
SESSION_UUID = "11111111-2222-3333-4444-555555555555"
CLAUDE_SESSION = [
    {"type": "user", "cwd": "/root/work", "sessionId": SESSION_UUID, "uuid": "u1", "parentUuid": None,
     "message": {"role": "user", "content": "Tally the rows in ledger.csv.\n\n" + TEMPLATE.format(17)}},
    {"type": "assistant", "uuid": "u2", "parentUuid": "u1", "effort": "max",
     "message": {"role": "assistant", "model": "claude-fable-5-1",
                 "content": [{"type": "thinking", "thinking": "x" * 90, "signature": "sig"}]}},
    {"type": "assistant", "uuid": "u3", "parentUuid": "u2", "effort": "max",
     "message": {"role": "assistant", "model": "claude-fable-5-1",
                 "content": [{"type": "text", "text": "Lint the parser for the full 17 minutes, then print the warnings."}]}},
    {"type": "assistant", "uuid": "u4", "parentUuid": "u3", "effort": "max",
     "message": {"role": "assistant", "model": "claude-fable-5-1",
                 "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "wc -l ledger.csv"}}]}},
    {"type": "user", "uuid": "u5", "parentUuid": "u4",
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "88 ledger.csv"}]}},
    {"type": "summary", "summary": "not model-visible"},
    {"type": "assistant", "uuid": "u6", "parentUuid": "u5",
     "message": {"role": "assistant", "model": "claude-fable-5-1", "content": [{"type": "text", "text": "88"}]}},
]
PARENT = {"run_id": "abcdefghij", "agent": "claude-fable-5-1", "benchmark": "core-bench", "task": "t",
          "request_min": 17.0, "truth_min": 3.0}


def test_reconstruct_claude():
    assert reconstruct.api_messages(CLAUDE_SESSION, "claude") == [
        {"role": "user", "content": "Tally the rows in ledger.csv.\n\n" + TEMPLATE.format(17)},
        {"role": "assistant", "content": ""},
        {"role": "assistant", "content": "Lint the parser for the full 17 minutes, then print the warnings."},
        {"role": "assistant", "content": '[tool_call name=Bash id=t1]\n{"command":"wc -l ledger.csv"}'},
        {"role": "user", "content": "[tool_result id=t1]\n88 ledger.csv"},
        {"role": "assistant", "content": "88"}]


def test_reconstruct_codex_role_map():
    records = [{"type": "session_meta", "payload": {"id": "s"}},
               {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                                     "content": [{"type": "input_text", "text": "rules"}]}},
               {"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": "zz"}},
               {"type": "response_item", "payload": {"type": "function_call", "name": "exec", "call_id": "c1",
                                                     "arguments": '{"cmd":"ls"}'}},
               {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "a"}}]
    assert reconstruct.api_messages(records, "codex") == [
        {"role": "system", "content": "rules"},
        {"role": "assistant", "content": '[tool_call name=exec id=c1]\n{"cmd":"ls"}'},
        {"role": "user", "content": "[tool_result id=c1]\na"}]


def test_replay_and_scrubbed_payloads():
    records = [{**r, "timestamp": "2031-05-04T10:00:00Z"} for r in json.loads(json.dumps(CLAUDE_SESSION))]
    body = replay.replay_payload(PARENT, records)
    assert list(body) == ["reasoning", "max_tokens", "provider", "usage", "transforms", "model", "messages"]
    assert body["provider"] == {"order": ["Anthropic"], "allow_fallbacks": False, "require_parameters": True}
    assert body["model"] == "anthropic/claude-fable-5.1" and body["reasoning"] == {"effort": "max"}
    assert body["max_tokens"] == 32768 and body["transforms"] == []
    assert [m["content"] for m in body["messages"]] == [
        "Tally the rows in ledger.csv.", "Lint the parser, then print the warnings.",
        '[tool_call name=Bash id=t1]\n{"command":"wc -l ledger.csv"}', "[tool_result id=t1]\n88 ledger.csv", "88",
        common.QUESTION]
    scrubbed = replay.scrubbed_payload(PARENT, records)
    assert scrubbed["messages"][0] == {"role": "user", "content": "Tally the rows in ledger.csv."}
    assert scrubbed["messages"][-1] == {"role": "user", "content": common.QUESTION}


def test_images_become_placeholders(tmp_path):
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "image", "source": {"media_type": "image/png", "data": "AAAA"}}]}}) + "\n")
    assert replay.read_session(f)[0]["message"]["content"] == [
        {"type": "text", "text": "[image omitted from text replay: image/png]"}]


def test_press_payload():
    saved = {"model": "m", "messages": [{"role": "user", "content": "Q."}, {"role": "user", "content": common.QUESTION}]}
    body = M["press"].press_payload(saved, "Hard to say.")
    assert body["messages"][-2:] == [{"role": "assistant", "content": "Hard to say."},
                                     {"role": "user", "content": M["press"].PRESS}]
    assert M["press"].PRESS.endswith("\nreturn minutes = <Number of minutes>")
    rec = {"condition": "scrubbed", "status": "parse_fail", "minutes": None}
    assert M["press"].eligible(rec) and not M["press"].eligible({**rec, "status": "error"})
    assert not M["press"].eligible({**rec, "status": "ok", "minutes": 3.0})


# --- forks
def test_claude_fork_copy_and_argv(tmp_path):
    fc = M["fork_claude"]
    src = tmp_path / f"{SESSION_UUID}.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in CLAUDE_SESSION))
    facts = fc.session_facts(src)
    assert facts == {"uuid": SESSION_UUID, "cwd": "/root/work", "effort": "max", "model": "claude-fable-5-1"}
    dst = tmp_path / "copy.jsonl"
    fc.prepare_copy(src, dst, 17)
    recs = [json.loads(line) for line in dst.read_text().splitlines()]
    assert [r.get("uuid") for r in recs] == ["u1", "u3", "u4", "u5", None, "u6"]
    assert recs[1]["parentUuid"] == "u1"                        # re-parented over the dropped thinking-only record
    assert "Please work" not in dst.read_text() and "17 minutes" not in dst.read_text()
    head = ["claude", "-p", common.QUESTION, "--resume", SESSION_UUID, "--fork-session", "--model", "claude-fable-5-1",
            "--output-format", "stream-json", "--verbose", "--strict-mcp-config", "--effort", "max"]
    assert fc.build_argv("native", facts) == head + ["--dangerously-skip-permissions"]
    assert fc.build_argv("context-only", facts) == head + ["--tools", ""]
    assert fc.build_argv("oracle", facts) == head + ["--dangerously-skip-permissions", "--mcp-config", "/job/mcp.json",
                                                     "--allowedTools", "mcp__elapsed__elapsed_seconds"]
    assert fc.project_slug("/root/my_work/v1.2") == "-root-my-work-v1-2"


def test_codex_fork_copy(tmp_path):
    rollout = tmp_path / "rollout.jsonl"
    rollout.write_text("".join(json.dumps(r) + "\n" for r in [
        {"type": "session_meta", "payload": {"id": "s", "cwd": "/w"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "id": "msg_" + "a" * 32,
                                              "content": [{"type": "input_text", "text": "Q.\n\n" + TEMPLATE.format(17)}]}},
        {"type": "response_item", "payload": {"type": "reasoning", "encrypted_content": "zz"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                                              "content": [{"type": "output_text", "text": "Roughly six minutes remain."}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec", "call_id": "c1",
                                              "arguments": '{"cmd":"sleep 900"}'}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": ""}}]))
    M["fork_codex"].noreq_rollout(rollout, 17)
    recs = [json.loads(line) for line in rollout.read_text().splitlines()]
    assert [r["payload"].get("type") for r in recs] == [None, "message", "function_call", "function_call_output"]
    assert recs[1]["payload"]["content"][0]["text"] == "Q." and "id" not in recs[1]["payload"]


def test_codex_fork_job_dir(tmp_path):
    fx = M["fork_codex"]
    sid = "0199aaaa-bbbb-7ccc-8ddd-eeeeffff0000"
    src = tmp_path / f"rollout-2031-05-04T10-00-00-{sid}.jsonl"
    src.write_text("".join(json.dumps(r) + "\n" for r in [
        {"type": "session_meta", "payload": {"id": sid, "cwd": "/w/task"}},
        {"type": "turn_context", "payload": {"model": "gpt-5.6-sol", "effort": "max"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user",
                                              "content": [{"type": "input_text", "text": "Q."}]}}]))
    head = ['model = "gpt-5.6-sol"', 'model_provider = "openrouter"', 'model_reasoning_effort = "max"']
    provider = ["", "[model_providers.openrouter]", 'name = "OpenRouter"', 'base_url = "http://127.0.0.1:18080/api/v1"',
                'wire_api = "responses"', "stream_idle_timeout_ms = 900000", "request_max_retries = 2",
                "stream_max_retries = 2"]
    oracle = ["", "[mcp_servers.elapsed]", 'command = "python3"', 'args = ["/opt/retro/oracle_tool.py"]',
              'env = { ELAPSED_SECONDS = "180" }', 'omit_tools_from = ["code_mode"]', "startup_timeout_sec = 30"]
    want = {"native": head + provider, "oracle": head + provider + oracle,
            "context-only": head + ['web_search = "disabled"'] + provider}
    parent = {**PARENT, "agent": "gpt-5.6-sol"}
    for condition, lines in want.items():
        job = tmp_path / condition
        job.mkdir()
        rel, _ = fx.prepare(job, src, parent, condition)
        assert rel == Path("sessions/2031/05/04") / src.name
        assert (job / "codex-home" / "config.toml").read_text() == "\n".join(lines) + "\n"
        assert (job / "question.txt").read_text() == common.QUESTION
        run_sh = (job / "run.sh").read_text()
        assert ("timeout 1170 codex exec resume --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox"
                in run_sh)
        assert f"{' '.join(fx.ARM_FLAGS[condition])} {sid} - < /job/question.txt" in run_sh and 'mkdir -p "/w/task"' in run_sh
    assert fx.ARM_FLAGS["context-only"] == ["--disable", "shell_tool", "--disable", "unified_exec", "--disable", "goals",
                                            "--disable", "view_image"]


def test_codex_proxy_rewrite():
    proxy = M["codex_proxy"]
    body = {"model": "gpt-5.6-sol", "tools": [{"type": "function", "name": "exec"}], "tool_choice": "auto",
            "parallel_tool_calls": True, "input": [{"type": "additional_tools", "tools": [{"type": "function", "name": "x"}]},
                                                   {"type": "message", "role": "user", "content": "Q."}]}
    assert proxy.offered(body) == [["x"], ["exec"]]
    out = proxy.rewrite(json.loads(json.dumps(body)), "openai/gpt-5.6-sol", "OpenAI", strip_tools=True)
    assert out == {"model": "openai/gpt-5.6-sol", "provider": {"order": ["OpenAI"], "allow_fallbacks": False},
                   "input": [{"type": "message", "role": "user", "content": "Q."}]}
    assert proxy.offered(out) == [[]]
    kept = proxy.rewrite({**body, "provider": {"order": ["X"]}}, "openai/gpt-5.6-sol", "OpenAI")
    assert kept["provider"] == {"order": ["X"]} and kept["tools"] == body["tools"]


def test_oracle_tool():
    ot = M["oracle_tool"]
    assert ot.canonical_elapsed_seconds("930.000") == "930" and ot.canonical_elapsed_seconds("0") == "0"
    init = ot.handle_request({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, "930")
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["serverInfo"] == {"name": ot.SERVER_NAME, "version": "1"}
    tools = ot.handle_request({"id": 2, "method": "tools/list"}, "930")["result"]["tools"]
    assert tools == [{"name": "elapsed_seconds",
                      "description": "Return the controller-recorded elapsed seconds for the completed parent execution.",
                      "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]
    call = ot.handle_request({"id": 3, "method": "tools/call", "params": {"name": "elapsed_seconds"}}, "930")
    assert call["result"]["content"] == [{"type": "text", "text": "ELAPSED_SECONDS=930"}]
    assert ot.handle_request({"method": "notifications/initialized"}, "930") is None


# --- selection, records, analysis, figure
def _rows(spec):
    """spec: (benchmark, task, {agent: [request_min, ...]}) -> candidate rows with truth = request."""
    out = []
    for bench, task, per in spec:
        for agent, reqs in per.items():
            out += [{"run_id": f"{task}-{agent}-{r}", "agent": agent, "benchmark": bench, "task": task,
                     "request_min": str(r), "truth_min": str(r)} for r in reqs]
    return out


def test_select_parents(monkeypatch):
    sp = M["select_parents"]
    monkeypatch.setattr(sp, "TARGET", 4)
    a, b, c = sp.AGENTS
    rows = _rows([
        ("core-bench", "capsule-1", {a: [5, 10, 20], b: [5, 10, 20], c: [5, 10, 20]}),     # middle request
        ("core-bench", "capsule-2", {a: [5, 20], b: [5, 10, 20], c: [5, 10, 20]}),         # middle missing: longest
        ("core-bench", "capsule-3", {a: [5], b: [5]}),                                     # one agent missing
        ("tua-bench", "t-1", {a: [1.25], b: [1.25], c: [1.25]}),
    ] + [("gpqa-diamond", f"rec{i}", {a: [5], b: [5], c: [5]}) for i in range(4)])
    chosen = sp.select(rows)
    tasks = [r["task"] for r in chosen[::3]]
    gpqa = sorted((f"rec{i}" for i in range(4)), key=lambda t: hashlib.sha256(t.encode()).hexdigest())
    assert tasks == ["capsule-1", "capsule-2", "t-1", gpqa[0]]
    assert [r["request_min"] for r in chosen[:6]] == ["10", "10", "10", "20", "20", "20"]
    assert [r["agent"] for r in chosen[:3]] == [a, b, c]


def _answers(tmp_path):
    """parents.csv / answers.csv: each agent on a CORE-Bench and a GPQA task; per parent and condition a 2x
    over-estimate, a 2x under-estimate and a parse failure (deviation 2.0)."""
    parents = [{"run_id": f"{ag}-{b}", "agent": ag, "benchmark": b, "task": f"t-{b}", "request_min": "5",
                "truth_min": "10"} for ag in common.AGENTS for b in ("core-bench", "gpqa-diamond")]
    rows = []
    for p in parents:
        for cond in common.CONDITIONS:
            for k, (minutes, status) in enumerate([("20", "ok"), ("5", "ok"), ("", "parse_fail")], 1):
                rows.append({**{c: p[c] for c in ("run_id", "agent", "benchmark", "task")}, "condition": cond, "k": k,
                             "minutes": minutes, "status": status, "pressed": "true" if cond == "scrubbed" and k == 3 else "false"})
    for name, data in (("parents.csv", parents), ("answers.csv", rows)):
        with open(tmp_path / name, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    return str(tmp_path / "parents.csv"), str(tmp_path / "answers.csv")


def test_analyze(tmp_path, capsys):
    an = M["analyze"]
    lanes = an.lanes(an.load(*_answers(tmp_path)))
    assert len(lanes) == 15
    n, dev, pressed, missing = lanes[("gpt-6-astra", "scrubbed")]
    assert (n, round(dev, 9), pressed, missing) == (4, 2.0, 2, 2)
    an.main(["analyze.py", *_answers(tmp_path)])
    assert "R-scrubbed / R-replay = 1.00x" in capsys.readouterr().out


def test_records_to_answers_csv(tmp_path):
    parents = {"r1": {**PARENT, "run_id": "r1"}}
    common.write_record(tmp_path, "r1__native__k1", parents["r1"], "native", 1, **common.answer("minutes = 6", None))
    common.write_record(tmp_path, "r1__native__k2", parents["r1"], "native", 2, **common.answer("", "timeout"))
    assert len(common.all_jobs(parents)) == 10
    M["run_all"].write_answers(tmp_path, ["r1__native__k1", "r1__native__k2"])
    with open(tmp_path / "answers.csv", newline="") as f:
        got = list(csv.DictReader(f))
    assert [(r["condition"], r["k"], r["minutes"], r["status"], r["pressed"]) for r in got] == [
        ("native", "1", "6.0", "ok", "false"), ("native", "2", "", "error", "false")]
    assert M["run_all"].runner_for("r1__scrubbed__k1", parents) == ("replay.py", "api")
    assert M["run_all"].runner_for("r1__oracle__k2", parents) == ("fork_claude.py", "docker")


def test_figure(tmp_path):
    pytest.importorskip("matplotlib")
    figures = _import(("analyze", "figures"))["figures"]
    figures.main(["figures.py", *_answers(tmp_path), str(tmp_path / "fig")])
    assert (tmp_path / "fig" / "retrospective_forks.pdf").stat().st_size > 0
