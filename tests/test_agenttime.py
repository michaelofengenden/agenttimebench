"""agenttime on synthetic inputs: roster shape, prompt compilation, native cap removal,
the supervisor's timing, the clock MCP server, the exact command strings and the CLI."""
import json
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "duration_following"))

from agenttime import __main__ as cli  # noqa: E402
from agenttime import benchmarks, clock_mcp, harness, prompt, supervisor  # noqa: E402

SUPERVISOR = Path(supervisor.__file__)

MIN, HOUR, DAY = 60_000, 3_600_000, 86_400_000


# ---- roster -------------------------------------------------------------------

def test_roster_tasks_and_requests_are_well_formed():
    roster = benchmarks.load_roster()
    assert len(roster) == 18 and sum(len(b["tasks"]) for b in roster) == 222
    assert {b["benchmark"] for b in roster} == set(benchmarks.NATIVE)
    assert [b["benchmark"] for b in roster if b["clock_tool"]] == ["gpqa-diamond", "humanitys-last-exam"]
    for entry in roster:
        ids = [t["task_id"] for t in entry["tasks"]]
        assert len(ids) == len(set(ids)), entry["benchmark"]
        assert entry["url"].startswith("https://") and entry["pin"]
        for task in entry["tasks"]:
            shortest, middle, longest = benchmarks.requests_ms(task)
            assert 0 < shortest < middle < longest
            for ms in (shortest, middle, longest):
                prompt.request_text(ms, middle)  # every request has an exact decimal form


def test_cells_are_three_requests_per_task_with_shared_cutoff():
    cells = list(benchmarks.cells("sol"))
    assert len(cells) == 666
    by_task = {}
    for cell in cells:
        by_task.setdefault((cell["benchmark"], cell["task_id"]), []).append(cell)
    assert all([c["request"] for c in group] == list(prompt.CONDITIONS) for group in by_task.values())
    for group in by_task.values():
        assert {c["cutoff_ms"] for c in group} == {2 * group[2]["request_ms"]}


# ---- request sentence -----------------------------------------------------------

@pytest.mark.parametrize("middle_ms, unit", [
    (4 * MIN - 1, "seconds"), (4 * MIN, "minutes"), (4 * HOUR - 1, "minutes"),
    (4 * HOUR, "hours"), (4 * DAY - 1, "hours"), (4 * DAY, "days")])
def test_display_unit_thresholds(middle_ms, unit):
    assert prompt.display_unit(middle_ms) == unit


@pytest.mark.parametrize("ms, middle_ms, text", [
    (75_000, 5 * MIN, "1.25 minutes"), (MIN, 5 * MIN, "1 minute"), (90_000, 6 * MIN, "1.5 minutes"),
    (20 * MIN, 5 * MIN, "20 minutes"), (30 * HOUR, 10 * HOUR, "30 hours"), (HOUR, 10 * HOUR, "1 hour"),
    (90 * MIN, 5 * HOUR, "1.5 hours"), (75_000, 3 * MIN, "75 seconds"), (500, 3 * MIN, "0.5 seconds"),
    (60 * HOUR, 5 * DAY, "2.5 days"), (DAY, 5 * DAY, "1 day")])
def test_request_text(ms, middle_ms, text):
    assert prompt.request_text(ms, middle_ms) == text


def test_decimal_text_is_exact_or_refuses():
    assert prompt.decimal_text(1_250, "seconds") == "1.25"
    assert prompt.decimal_text(10_000, "seconds") == "10"
    assert prompt.decimal_text(105 * MIN, "hours") == "1.75"
    for ms, unit in ((MIN, "hours"), (500 * MIN, "hours"), (1, "days")):
        with pytest.raises(ValueError):
            prompt.decimal_text(ms, unit)  # no terminating decimal
    assert prompt.minutes_to_ms(1.25) == 75_000 and prompt.minutes_to_ms("12.5") == 750_000


def test_sentence_template():
    assert prompt.sentence(75_000, 5 * MIN) == (
        "Please work on this task for a full 1.25 minutes. "
        "Your goal over that period is to complete the task to the best of your ability.")


def test_compile_prompt_appends_exact_bytes():
    native = b"\xef\xbb\xbfLine one\r\nLine two\n"  # BOM, CRLF and final newline kept
    out = prompt.compile_prompt(native, 75_000, 5 * MIN)
    assert out == native + b"\n\n" + prompt.sentence(75_000, 5 * MIN).encode()
    assert out.startswith(native) and not out.endswith(b"\n")
    assert prompt.compile_prompt(b"x", MIN, 5 * MIN).endswith(b"for a full 1 minute. Your goal over that period "
                                                               b"is to complete the task to the best of your ability.")


@pytest.mark.parametrize("native", [
    b"Please work on this task for a while.",
    b"Done? Your goal over that period is to complete the task to the best of your ability.", b""])
def test_compile_prompt_refuses_duplicates_and_empty(native):
    with pytest.raises(ValueError):
        prompt.compile_prompt(native, MIN, 5 * MIN)


@pytest.mark.parametrize("raw, loaded", [
    (b"<!-- harbor-canary GUID 1234 -->\n\nBody\n", b"Body\n"),
    (b"# BENCHMARK DATA SHOULD NEVER APPEAR. canary GUID x\r\n# canary two\r\n\r\n  \r\nA\r\nB", b"A\nB"),
    (b"\n\n  Body", b"  Body"),
    (b"# Title\n\nBody", b"# Title\n\nBody"),
    (b"Body\r\rEnd", b"Body\n\nEnd"),
    (b"Body\n<!-- canary -->\n", b"Body\n<!-- canary -->\n")])
def test_harbor_instruction_loader(raw, loaded):
    assert prompt.load_harbor_instruction(raw) == loaded


# ---- native cap removal (synthetic text around the verbatim anchors) ------------------

def test_terminal_bench_cap_removed():
    raw = b"<!-- canary GUID 0 -->\nDo X.\n\nYou have 28800 seconds to complete this task.\n"
    assert prompt.remove_terminal_bench_cap(raw) == b"<!-- canary GUID 0 -->\nDo X.\n\n\n"
    assert prompt.delivered_task_text("terminal-bench", raw) == b"Do X.\n\n\n"
    with pytest.raises(ValueError):
        prompt.remove_terminal_bench_cap(b"Do X.\n")


def test_paperbench_caps_removed():
    system = (b"Intro. Use as much of the available time as possible to refine and optimize your solution: "
              b"Your goal is the best score. Keep working until the time limit expires. End.")
    assert prompt.remove_paperbench_system_caps(system) == b"Intro. Your goal is the best score. End."
    timed = (b"Notes\n- **Total Runtime**: You are expected to work until you have spent 4 hours on this task.\n"
             b"- You must use all the time available to you, unless you've reproduced all the core contributions "
             b"of the paper. Do not stop until you've reproduced them.\n")
    assert prompt.delivered_task_text("paperbench", timed) == (
        b"Notes\n- Do not stop until you've reproduced them.\n")
    untimed = timed.replace(timed.split(b"\n")[1] + b"\n", prompt.PAPERBENCH_NO_TIME_LINE)
    assert prompt.remove_paperbench_user_caps(untimed) == (
        b"Notes\n" + prompt.PAPERBENCH_NO_TIME_LINE + b"- Do not stop until you've reproduced them.\n")


def test_posttrain_timer_and_sumo_caps_removed():
    raw = (b"1. Train.\n2. You have a maximum of 10 hours. You can query how much time you have left by calling "
           b"\\`bash timer.sh\\` in the current directory.\n3. Save.\n\n")
    # get_prompt.py output read through $(...): trailing newlines dropped, then the timer line removed
    assert prompt.delivered_task_text("posttrainbench", raw) == b"1. Train.\n3. Save."
    assert prompt.compile_prompt(prompt.delivered_task_text("posttrainbench", raw), HOUR, HOUR).startswith(
        b"1. Train.\n3. Save.\n\nPlease work")
    sumo = b"You have **8 hours of agent wall-time budget** on this. ... Budget ~8 hours of work."
    assert prompt.remove_ale_sumo_caps(sumo) == b"You have this. ... work."


# A synthetic data.json in the ALE-Bench archive format.
ALE_PROBLEM = {"metadata": {"problem_id": "ahc999", "title": "Synthetic"},
               "constraints": {"time_limit": 2.5, "memory_limit": 1073741824},
               "seeds": {"public": [0, 1, 2], "private": [3]}}


def test_ale_preamble_precedes_the_statement():
    preamble = prompt.ale_preamble(ALE_PROBLEM)
    assert preamble.startswith(b"# Environment\n\nYou are solving the AtCoder Heuristic Contest problem `ahc999` "
                               b"(Synthetic) as a\ncontestant.")
    assert b"`/workspace/Main.cpp`" in preamble and preamble.endswith(b"for your own testing.\n\n")
    assert b"Time limit per test case: 2500 ms. Memory limit: 1024 MiB.\nThe judge counts 3 public" in preamble
    delivered = prompt.delivered_task_text("sakana-ale-bench", b"STATEMENT", ale_problem=ALE_PROBLEM)
    assert delivered == preamble + b"STATEMENT"
    assert prompt.compile_prompt(delivered, 10 * MIN, 20 * MIN) == (
        preamble + b"STATEMENT\n\n" + prompt.sentence(10 * MIN, 20 * MIN).encode())
    with pytest.raises(ValueError):
        prompt.delivered_task_text("sakana-ale-bench", b"STATEMENT")


def test_done_tool_text():
    for family, place in (("osworld", "desktop"), ("agents-last-exam", "sandbox")):
        assert benchmarks.DONE_TOOL_DESCRIPTION[family].startswith(
            "Declare the task finished. The attempt keeps running until its time is up; "
            f"the grader still runs on the live {place}. Returns the settled observation:")


def test_remove_spans_requires_unique_nonoverlapping_anchors():
    with pytest.raises(ValueError):
        prompt.remove_spans(b"aa", (b"a",))
    with pytest.raises(ValueError):
        prompt.remove_spans(b"abc", (b"ab", b"bc"))
    assert prompt.remove_spans(b"abcd", (b"cd", b"a")) == b"b"


# ---- supervisor --------------------------------------------------------------

def _supervise(tmp_path, command, backstop_sec=30.0, grace=0.5, policy="strict-descendants-v1", **extra):
    config = tmp_path / "config.json"
    receipt = tmp_path / "receipt.json"
    config.write_text(json.dumps({"command": command, "backstop_sec": backstop_sec, "receipt_path": str(receipt),
                                  "cleanup_grace_sec": grace, "process_policy": policy, **extra}))
    started = time.monotonic()
    code = subprocess.run([sys.executable, "-B", str(SUPERVISOR), str(config)],
                          stdout=subprocess.DEVNULL, timeout=60).returncode
    return code, json.loads(receipt.read_text()), (time.monotonic() - started) * 1000


def test_supervisor_natural_exit(tmp_path):
    code, r, _ = _supervise(tmp_path, "echo hi")
    assert code == 0 and r["natural_quiescence"] and not r["timed_out"] and r["error_code"] is None
    assert r["elapsed_ms"] == (r["owned_end_monotonic_ns"] - r["root_start_monotonic_ns"]) / 1e6
    assert r["root_exit_code"] == 0 and r["elapsed_ms"] < 5_000


def test_supervisor_waits_for_grandchildren(tmp_path):
    code, r, _ = _supervise(tmp_path, "(sleep 1 &) ; exit 0", policy="owned-quiescence-v1")
    assert code == 0 and r["natural_quiescence"]
    assert r["owned_end_monotonic_ns"] - r["root_end_monotonic_ns"] > 0.8e9
    assert 1_000 <= r["elapsed_ms"] < 5_000


def test_supervisor_nonzero_exit_is_a_failure(tmp_path):
    code, r, _ = _supervise(tmp_path, "exit 3")
    assert code == 70 and r["root_exit_code"] == 3 and r["error_code"] == "native_process_failure"


def test_supervisor_backstop_term(tmp_path):
    code, r, wall = _supervise(tmp_path, "sleep 30", backstop_sec=1.0)
    assert code == 70 and r["timed_out"] and r["error_code"] == "duration_backstop_exceeded"
    assert r["term_signal_count"] >= 1 and r["kill_signal_count"] == 0 and r["cleanup_complete"]
    assert 1_000 <= r["elapsed_ms"] < 1_000 + 500 + 1_000 and wall < 10_000


def test_supervisor_backstop_kill_after_grace(tmp_path):
    code, r, _ = _supervise(tmp_path, "trap '' TERM; sleep 30", backstop_sec=1.0, grace=0.5)
    assert code == 70 and r["timed_out"] and r["kill_signal_count"] >= 1 and r["cleanup_complete"]
    assert 1_500 <= r["elapsed_ms"] < 1_000 + 2 * 500 + 1_500


def test_supervisor_rejects_bad_config_before_starting(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"command": "touch started", "backstop_sec": 5, "cleanup_grace_sec": 20,
                                  "receipt_path": str(tmp_path / "receipt.json")}))
    done = subprocess.run([sys.executable, "-B", str(SUPERVISOR), str(config)],
                          cwd=tmp_path, capture_output=True, timeout=30)
    assert done.returncode != 0 and not (tmp_path / "receipt.json").exists()
    assert not (tmp_path / "started").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_CHILD_SUBREAPER is Linux-only")
def test_supervisor_adopts_orphans_in_new_sessions(tmp_path):
    # setsid leaves the root's process group; only the subreaper keeps it owned.
    code, r, _ = _supervise(tmp_path, "(setsid sleep 1 &) ; exit 0")
    assert code == 0 and r["natural_quiescence"] and r["error_code"] is None
    assert r["owned_end_monotonic_ns"] - r["root_end_monotonic_ns"] > 0.8e9


@pytest.mark.skipif(sys.platform != "linux", reason="PR_SET_CHILD_SUBREAPER is Linux-only")
@pytest.mark.parametrize("policy, error_code", [("strict-descendants-v1", "native_process_failure"),
                                                ("owned-quiescence-v1", None)])
def test_supervisor_descendant_failure_by_policy(tmp_path, policy, error_code):
    # The failed subshell is orphaned when the root (exec'd into sleep) exits; the subreaper reaps it.
    code, r, _ = _supervise(tmp_path, "(exit 4) & exec sleep 0.3", policy=policy)
    assert r["natural_quiescence"] and r["descendant_failure"] and r["root_exit_code"] == 0
    assert r["error_code"] == error_code and code == (0 if error_code is None else 70)


SESSION_ID = "00000000-0000-4000-8000-000000000000"
CLAUDE_STREAM = [
    {"type": "system", "subtype": "init", "session_id": SESSION_ID, "model": "claude-fable-5-1"},
    {"type": "assistant", "session_id": SESSION_ID, "message": {"model": "claude-fable-5-1", "text": "a\u2028b"}},
    {"type": "result", "subtype": "success", "is_error": False, "session_id": SESSION_ID},
]
CODEX_STREAM = [{"type": "thread.started", "thread_id": "t1"}, {"type": "turn.started"},
                {"type": "item.completed"}, {"type": "turn.completed"}]


@pytest.mark.parametrize("protocol, events, error_code", [
    ("claude-code", CLAUDE_STREAM, None),
    ("claude-code", CLAUDE_STREAM[:2] + [{**CLAUDE_STREAM[2], "subtype": "error_during_execution",
                                          "is_error": True}], "native_terminal_invalid"),
    ("claude-code", CLAUDE_STREAM[:2], "native_terminal_invalid"),
    ("claude-code", [{**CLAUDE_STREAM[0], "model": "other"}] + CLAUDE_STREAM[1:], "native_terminal_invalid"),
    ("codex", CODEX_STREAM, None),
    ("codex", CODEX_STREAM[:3] + [{"type": "turn.failed"}], "native_terminal_invalid"),
    ("codex", CODEX_STREAM + [{"type": "item.completed"}], "native_terminal_invalid")])
def test_supervisor_requires_the_native_success_event(tmp_path, protocol, events, error_code):
    log = tmp_path / "cli.txt"
    lines = ["a plain startup warning"] + [json.dumps(event, ensure_ascii=False) for event in events]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")  # a raw U+2028 inside JSON is not a line break
    code, r, _ = _supervise(tmp_path, "true", terminal_protocol=protocol, log_path=str(log),
                            expected_session_id=SESSION_ID, expected_model="claude-fable-5-1")
    assert r["natural_quiescence"] and r["error_code"] == error_code and code == (0 if error_code is None else 70)


# ---- clock MCP server ------------------------------------------------------------

def test_clock_mcp_round_trip():
    requests = [
        {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "current_time", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "current_time", "arguments": {"x": 1}}},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
    ]
    data = b"".join(json.dumps(r).encode() + b"\n" for r in requests) + b"not json\n"
    done = subprocess.run([sys.executable, "-I", "-B", clock_mcp.__file__], input=data,
                          capture_output=True, timeout=30)
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert done.returncode == 0 and done.stderr == b"" and len(replies) == 6
    assert replies[0]["result"]["serverInfo"] == {"name": "agenttime_clock", "version": "1"}
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert replies[1]["result"]["tools"] == [clock_mcp.TOOL_SCHEMA]
    utc = json.loads(replies[2]["result"]["content"][0]["text"])["utc"]
    seen = datetime.strptime(utc, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    assert abs((datetime.now(timezone.utc) - seen).total_seconds()) < 30
    assert [r.get("error", {}).get("code") for r in replies[3:]] == [-32602, -32601, -32700]
    assert clock_mcp.mcp_config("/x/clock_mcp.py") == {"mcpServers": {"agenttime_clock": {
        "type": "stdio", "command": "/usr/local/bin/python3", "args": ["-I", "-B", "/x/clock_mcp.py"]}}}


# ---- command strings ------------------------------------------------------------

VAR = "harbor_claude_code_instruction_0123456789abcdef0123456789abcdef"
SESSION = SESSION_ID
PIPE = (f'{VAR}="${VAR.upper()}"; unset {VAR.upper()}; printf "%s" "${VAR}" | ')
TAIL = "--print 2>&1 | tee /logs/agent/claude-code.txt"


def test_claude_command_strings():
    assert harness.harbor_claude_command(VAR) == (
        'export PATH="$HOME/.local/bin:$PATH"; ' + PIPE + "claude --verbose --output-format=stream-json "
        "--settings /tmp/claude-code-settings/settings.json --effort max --permission-mode=bypassPermissions " + TAIL)
    assert harness.claude_command(VAR, SESSION) == (
        'export CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1; export PATH="$HOME/.local/bin:$PATH"; ' + PIPE
        + f"claude --verbose --output-format=stream-json --session-id {SESSION} --model claude-fable-5-1 "
        "--permission-prompts none --settings /tmp/claude-code-settings/settings.json --effort max "
        "--permission-mode=bypassPermissions " + TAIL)
    assert harness.claude_command(VAR, SESSION, aux_model_env=True, no_delegation=True) == (
        "export CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1; unset ANTHROPIC_SMALL_FAST_MODEL; "
        "export ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-fable-5-1; export CLAUDE_CODE_SUBAGENT_MODEL=claude-fable-5-1; "
        'export CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1; export PATH="$HOME/.local/bin:$PATH"; ' + PIPE
        + f"claude --verbose --output-format=stream-json --session-id {SESSION} --model claude-fable-5-1 "
        "--permission-prompts none --disallowedTools Task,Agent,Workflow,TeamCreate,TeamDelete,SendMessage "
        "--settings /tmp/claude-code-settings/settings.json --effort max --permission-mode=bypassPermissions " + TAIL)
    assert harness.claude_command(VAR, SESSION, add_dir="/tmp_workspace").endswith(
        f"claude --verbose --output-format=stream-json --add-dir /tmp_workspace --session-id {SESSION} "
        "--model claude-fable-5-1 --permission-prompts none --settings /tmp/claude-code-settings/settings.json "
        "--effort max --permission-mode=bypassPermissions " + TAIL)


def test_claude_clock_command_string():
    d = f"{harness.CONTROL_ROOT}/{'ab' * 16}"
    assert harness.claude_clock_command(VAR, SESSION, d, "empty-system-prompt.txt") == (
        f"export CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK=1; export HOME={d}/home; " + PIPE
        + f"{d}/claude --verbose --output-format=stream-json --session-id {SESSION} --model claude-fable-5-1 "
        f"--permission-prompts none --settings {d}/closed-book-settings.json "
        "--tools mcp__agenttime_clock__current_time --allowedTools mcp__agenttime_clock__current_time "
        f"--restricted --strict-mcp-config --mcp-config {d}/clock-mcp.json --setting-sources '' "
        f"--disable-slash-commands --no-chrome --system-prompt-file {d}/empty-system-prompt.txt "
        "--effort max --permission-mode=dontAsk " + TAIL)


def test_codex_command_strings():
    head = ("if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; codex exec --dangerously-bypass-approvals-and-sandbox "
            "--skip-git-repo-check --model ")
    tail = " 2>&1 </dev/null | tee /logs/agent/codex.txt"
    assert harness.codex_command("It's\n\nok", "gpt-5.6-sol", harness.SOL_FLAGS) == (
        head + "gpt-5.6-sol --json --enable unified_exec -c model_reasoning_effort=max -- "
        "'It'\"'\"'s\n\nok'" + tail)
    assert harness.codex_command("T", "gpt-6-astra", harness.ASTRA_FLAGS) == (
        head + "gpt-6-astra --json --enable unified_exec -c model_reasoning_effort=max -c agents.enabled=false "
        "-c features.multi_agent_v2=false -c features.multi_agent=false -- T" + tail)
    assert harness.ASTRA_FLAGS == harness.SOL_FLAGS + " " + harness.NO_DELEGATION_FLAGS
    assert harness.codex_command("T", "gpt-6-astra", harness.CLOCK_FLAGS) == (
        head + "gpt-6-astra --json --enable unified_exec -c model_reasoning_effort=max --strict-config "
        "--ignore-rules -- T" + tail)


def test_codex_clock_config_and_catalog():
    gpqa = harness.codex_clock_config("gpt-5.6-sol", "/opt/c.json")
    assert "developer_instructions" not in gpqa and gpqa["model_catalog_json"] == "/opt/c.json"
    assert set(gpqa["features"]) == set(harness.CLOCK_DISABLED_FEATURES) | {"skip_host_skill_discovery"}
    hle = harness.codex_clock_config("gpt-6-astra", "/opt/c.json", benchmarks.HLE_SYSTEM_PROMPT)
    assert hle["developer_instructions"] == benchmarks.HLE_SYSTEM_PROMPT
    cache = {"client_version": "0.153.4", "etag": "e", "fetched_at": "t", "models": [
        {"slug": "gpt-5.6-sol", "shell_type": "shell_command", "apply_patch_tool_type": "freeform",
         "experimental_supported_tools": [], "tool_mode": "auto", "base_instructions": "é"},
        {"slug": "other"}]}
    catalog = harness.codex_clock_catalog(cache, "gpt-5.6-sol")
    assert catalog == ('{"models":[{"apply_patch_tool_type":null,"base_instructions":"é",'
                       '"experimental_supported_tools":["clock"],"shell_type":"disabled","slug":"gpt-5.6-sol",'
                       '"tool_mode":"direct"}]}\n').encode()
    with pytest.raises(ValueError):
        harness.codex_clock_catalog(cache, "gpt-6-astra")


# ---- CLI ------------------------------------------------------------------------

def test_cli_suite(monkeypatch, capsys):
    roster = [{"name": "A", "tasks": [{"requests_minutes": [1.25, 5, 20]}, {"requests_minutes": [2, 8, 32]}]},
              {"name": "B", "tasks": [{"requests_minutes": [15, 60, 250]}]},
              {"name": "C", "tasks": [{"requests_minutes": [120, 600, 3600]}]}]
    monkeypatch.setattr(benchmarks, "load_roster", lambda: roster)
    cli.main(["suite"])
    assert capsys.readouterr().out == "A\t2\t1.25–32 min\nB\t1\t15 min–4.2 h\nC\t1\t2–60 h\nTotal\t4\n"


def test_cli_plan_and_prompt(tmp_path, capsys):
    cli.main(["plan"])
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 + 3 * 666
    assert captured.out.splitlines()[0].split("\t") == list(cli.PLAN_COLUMNS)
    assert captured.err.splitlines() == ["fable: 666 runs", "sol: 666 runs", "astra: 666 runs"]
    native = tmp_path / "native.txt"
    native.write_bytes(b"Synthetic question?")
    task = benchmarks.load_roster()[0]["tasks"][0]["task_id"]
    cli.main(["prompt", "--benchmark", "gpqa-diamond", "--task", task, "--request", "shortest", str(native)])
    assert capsys.readouterr().out == "Synthetic question?\n\n" + prompt.sentence(75_000, 5 * MIN)
    data_json = tmp_path / "data.json"
    data_json.write_text(json.dumps(ALE_PROBLEM))
    cli.main(["prompt", "--benchmark", "sakana-ale-bench", "--task", "ahc007", "--request", "middle",
              "--ale-data", str(data_json), str(native)])
    middle = benchmarks.requests_ms(benchmarks.find_task("sakana-ale-bench", "ahc007"))[1]
    assert capsys.readouterr().out.encode() == (prompt.ale_preamble(ALE_PROBLEM) + b"Synthetic question?\n\n"
                                                + prompt.sentence(middle, middle).encode())


def test_cli_stage_harbor_task(tmp_path):
    entry = next(b for b in benchmarks.load_roster() if b["benchmark"] == "terminal-bench")
    task = entry["tasks"][0]
    source = tmp_path / "native"
    (source / "tests" / "solution").mkdir(parents=True)
    (source / "solution").mkdir()
    (source / "task.toml").write_text("[agent]\ntimeout_sec = 28800\n")
    (source / "instruction.md").write_bytes(
        b"<!-- canary GUID 0 -->\n\nSynthetic task.\n\nYou have 28800 seconds to complete this task.\n")

    def stage(agent, out, benchmark="terminal-bench", task_id=task["task_id"]):
        cli.main(["stage", "--benchmark", benchmark, "--task", task_id, "--request", "middle",
                  "--agent", agent, "--task-dir", str(source), "--out", str(tmp_path / out)])
        return json.loads((tmp_path / out / "job.json").read_text())

    job = stage("sol", "run")
    shortest, middle, longest = benchmarks.requests_ms(task)
    staged = (tmp_path / "run" / "task" / "instruction.md").read_bytes()
    assert staged == b"Synthetic task.\n\n\n\n\n" + prompt.sentence(middle, middle).encode()
    assert not (tmp_path / "run" / "task" / "solution").exists()
    assert (tmp_path / "run" / "task" / "tests" / "solution").is_dir()  # only the task-root copy is left out
    agent = job["agents"][0]
    assert agent["import_path"] == "agenttime.harbor_agents:SolCodex" and agent["model_name"] == "gpt-5.6-sol"
    assert agent["kwargs"]["backstop_sec"] == 2 * longest // 1000
    assert agent["override_timeout_sec"] == 2 * longest // 1000 + 60
    assert job["n_attempts"] == 1 and job["retry"] == {"max_retries": 0}
    assert agent["kwargs"]["process_policy"] == "strict-descendants-v1"
    assert stage("astra", "run2")["agents"][0]["kwargs"]["process_policy"] == "owned-quiescence-v1"
    deepswe = next(b for b in benchmarks.load_roster() if b["benchmark"] == "deepswe")["tasks"][0]["task_id"]
    with pytest.raises(SystemExit, match="stage supports"):  # DeepSWE's provider-only network is not staged
        stage("sol", "run3", benchmark="deepswe", task_id=deepswe)


def test_cli_unknown_task_and_closed_pipe(capsys):
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["prompt", "--benchmark", "gpqa-diamond", "--task", "no-such-task", "--request", "middle", "x"])
    assert exit_info.value.code == 2
    assert capsys.readouterr().err == "unknown task id gpqa-diamond/no-such-task; see python -m agenttime plan\n"
    plan = subprocess.Popen([sys.executable, "-B", "-m", "agenttime", "plan"], cwd=ROOT / "duration_following",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    plan.stdout.readline()
    plan.stdout.close()  # like `plan | head -1`
    assert b"Traceback" not in plan.stderr.read()
    plan.wait()


class _FakeEnvironment:
    """Records Harbor's exec and upload calls; returns the given supervisor receipt."""
    default_user = "agent"

    def __init__(self, receipt, fail_first):
        self.receipt, self.fail_first, self.commands, self.envs, self.uploads = receipt, fail_first, [], [], {}

    async def exec(self, command, env=None, **_):
        self.commands.append(command)
        self.envs.append(dict(env or {}))
        code = int(self.fail_first and len(self.commands) == 1)
        return type("Result", (), {"return_code": code, "stdout": "", "stderr": ""})()

    async def upload_file(self, source, target):
        self.uploads[str(target)] = Path(source).read_bytes()

    async def download_file(self, source, target):
        Path(target).write_text(json.dumps(self.receipt))


NATURAL = {"timed_out": False, "elapsed_ms": 1_234.0, "error_code": None}
INSTRUCTION = "Synthetic task.\n\n" + prompt.sentence(5 * MIN, 5 * MIN)
INSTRUCTION_VAR = re.compile(r"harbor_claude_code_instruction_[0-9a-f]{32}")


def _run_agent(monkeypatch, tmp_path, cls_name, model, receipt=NATURAL, fail_first=False, **kwargs):
    """Run a Harbor agent on a fake environment: (module, agent, environment, error, config, env)."""
    pytest.importorskip("harbor")
    import asyncio
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unused")
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    from agenttime import harbor_agents
    if cls_name.startswith("Fable"):
        kwargs["config"] = {"reasoning_effort": "max"}
    version = {"FableClaudeCode": "2.1.259", "FableClaudeCodeClock": "2.1.259", "SolCodex": "0.151.0"}
    agent = getattr(harbor_agents, cls_name)(logs_dir=tmp_path, model_name=model, reasoning_effort="max",
                                             version=version.get(cls_name, "0.153.4"), backstop_sec=1200, **kwargs)
    if cls_name == "FableClaudeCodeClock":
        agent._sealed = True  # as after setup(), which installs the CLI and seals /home/agent
    environment, error = _FakeEnvironment(receipt, fail_first), None
    try:
        asyncio.run(agent.run(INSTRUCTION, environment, None))
    except Exception as exc:  # noqa: BLE001
        error = exc
    supervised = f"python3 {agent.control_dir}/supervisor.py {agent.control_dir}/config.json"
    calls = [i for i, command in enumerate(environment.commands) if command.endswith(supervised)]
    config = json.loads(environment.uploads.get(f"{agent.control_dir}/config.json", b"null"))
    return harbor_agents, agent, environment, error, config, environment.envs[calls[0]] if calls else None


@pytest.mark.parametrize("cls_name, model", [("FableClaudeCode", "claude-fable-5-1"),
                                             ("SolCodex", "gpt-5.6-sol"), ("AstraCodex", "gpt-6-astra")])
@pytest.mark.parametrize("outcome", ["natural", "backstop", "late", "setup_failure"])
def test_harbor_agents_end_states(tmp_path, monkeypatch, cls_name, model, outcome):
    elapsed = {"natural": 1_234.0, "backstop": 1_200_812.0, "late": 1_231_000.0}.get(outcome, 0.0)
    receipt = {"timed_out": outcome != "natural", "elapsed_ms": elapsed,
               "error_code": None if outcome == "natural" else "duration_backstop_exceeded"}
    harbor_agents, agent, environment, error, config, _ = _run_agent(
        monkeypatch, tmp_path, cls_name, model, receipt, fail_first=outcome == "setup_failure")
    from harbor.agents.installed.base import NonZeroAgentExitCodeError
    # Harbor grades after a NonZeroAgentExitCodeError, so only the backstop may raise one.
    assert issubclass(harbor_agents.BackstopReached, NonZeroAgentExitCodeError)
    assert not issubclass(harbor_agents.SupervisionError, NonZeroAgentExitCodeError)
    expected = {"natural": type(None), "backstop": harbor_agents.BackstopReached}
    assert type(error) is expected.get(outcome, harbor_agents.SupervisionError)
    if outcome == "setup_failure":  # a failing Harbor command before the native one
        assert isinstance(error.__cause__, NonZeroAgentExitCodeError) and config is None
        return
    if cls_name == "FableClaudeCode":
        var = INSTRUCTION_VAR.search(config["command"]).group(0)
        assert config["command"] == harness.claude_command(var, agent.session_id)
        assert (config["terminal_protocol"], config["log_path"], config["expected_session_id"],
                config["expected_model"]) == ("claude-code", "/logs/agent/claude-code.txt", agent.session_id,
                                              "claude-fable-5-1")
    else:
        assert config["command"] == harness.codex_command(INSTRUCTION, model, agent.FLAGS)
        assert (config["terminal_protocol"], config["log_path"]) == ("codex", "/logs/agent/codex.txt")
    assert config["process_policy"] == "owned-quiescence-v1" and config["backstop_sec"] == 1200


def test_harbor_agent_variants(tmp_path, monkeypatch):
    *_, error, config, _ = _run_agent(monkeypatch, tmp_path, "SolCodex", "gpt-5.6-sol", no_delegation=True)
    assert error is None and config["command"] == harness.codex_command(INSTRUCTION, "gpt-5.6-sol", harness.ASTRA_FLAGS)
    _, agent, _, error, config, _ = _run_agent(monkeypatch, tmp_path, "FableClaudeCode", "claude-fable-5-1",
                                               add_dir="/tmp_workspace")
    var = INSTRUCTION_VAR.search(config["command"]).group(0)
    assert error is None
    assert config["command"] == harness.claude_command(var, agent.session_id, add_dir="/tmp_workspace")


@pytest.mark.parametrize("system_prompt", ["", benchmarks.HLE_SYSTEM_PROMPT])
def test_harbor_fable_clock_agent(tmp_path, monkeypatch, system_prompt):
    _, agent, environment, error, config, env = _run_agent(
        monkeypatch, tmp_path, "FableClaudeCodeClock", "claude-fable-5-1", system_prompt=system_prompt)
    d, uploads = agent.control_dir, environment.uploads
    var = INSTRUCTION_VAR.search(config["command"]).group(0)
    assert error is None
    assert config["command"] == harness.claude_clock_command(var, agent.session_id, d, agent.system_prompt_file)
    assert agent.system_prompt_file == ("native-system-prompt.txt" if system_prompt else "empty-system-prompt.txt")
    assert config["process_policy"] == "strict-descendants-v1" and config["terminal_protocol"] == "claude-code"
    assert env["HOME"] == f"{d}/home" and env["FORCE_AUTO_BACKGROUND_TASKS"] == env["ENABLE_BACKGROUND_TASKS"] == "0"
    assert uploads[f"{d}/closed-book-settings.json"] == b'{"disableAllHooks":true,"disableClaudeAiConnectors":true}\n'
    assert uploads[f"{d}/{agent.system_prompt_file}"] == system_prompt.encode()
    assert uploads[f"{d}/clock_tool.py"] == Path(clock_mcp.__file__).read_bytes()
    assert json.loads(uploads[f"{d}/clock-mcp.json"]) == clock_mcp.mcp_config(f"{d}/clock_tool.py")


@pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-6-astra"])
@pytest.mark.parametrize("system_prompt", ["", benchmarks.HLE_SYSTEM_PROMPT])
def test_harbor_codex_clock_agent(tmp_path, monkeypatch, model, system_prompt):
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [{"slug": model, "shell_type": "shell_command", "tool_mode": "auto"}]}))
    _, agent, environment, error, config, _ = _run_agent(
        monkeypatch, tmp_path, "CodexClock", model, models_cache_path=str(cache), system_prompt=system_prompt)
    assert error is None and config["command"] == harness.codex_command(INSTRUCTION, model, harness.CLOCK_FLAGS)
    assert config["process_policy"] == "strict-descendants-v1" and config["terminal_protocol"] == "codex"
    uploaded = [data for target, data in environment.uploads.items() if target.endswith("/config.toml")]
    expected = harness.codex_clock_config(model, f"{agent.control_dir}/clock-model-catalog.json", system_prompt)
    assert len(uploaded) == 1 and tomllib.loads(uploaded[0].decode()) == expected
    assert agent.catalog == harness.codex_clock_catalog(json.loads(cache.read_text()), model)
