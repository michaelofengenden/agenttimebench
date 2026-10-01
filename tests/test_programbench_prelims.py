"""Tests for preliminary/programbench_same_turn and programbench_separate_turn."""
import csv
import hashlib
import importlib
import importlib.util
import io
import json
import math
import os
import statistics
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "preliminary"
SAME = ROOT / "programbench_same_turn"
SEP = ROOT / "programbench_separate_turn"
_NAMES = ("execute", "fork", "invocations", "oracle_tool", "questions", "scrubber")


def _import_separate_turn():
    """Import the separate-turn modules without leaving their generic names in sys.modules or sys.path."""
    saved = {n: sys.modules.pop(n) for n in _NAMES if n in sys.modules}
    sys.path.insert(0, str(SEP))
    try:
        return {n: importlib.import_module(n) for n in _NAMES}
    finally:
        sys.path.remove(str(SEP))
        for n in _NAMES:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


execute, fork, invocations, oracle_tool, questions, scrubber = _import_separate_turn().values()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract = load("same_turn_extract", SAME / "extract.py")
same_analyze = load("same_turn_analyze", SAME / "analyze.py")
sep_analyze = load("separate_turn_analyze", SEP / "analyze.py")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cli(script, *args):
    return subprocess.run([sys.executable, "-B", str(script), *map(str, args)], capture_output=True, text=True,
                          check=True).stdout


def write_csv(path, rows, fields=None):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fields or list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


# ---------------------------------------------------------------- same turn

def test_same_turn_prompts_and_driver_are_the_ones_that_ran():
    assert sha(SAME / "prompts/system.md") == "5a81f61d977fda4fd72db3c71a8021deb17714e5e3629be6d4d7276dd26d41e4"
    assert sha(SAME / "prompts/task.txt") == "43b3bd8cc3b1c7ddfdac23c5886d485d76b9c0090c9a7587baa183a38dd47058"
    assert sha(SAME / "pb") == "2a979b15c3b6c9f4dd257e2fdbac3eac1f3ece3532415e1be860242e6ec756b4"
    assert sha(SEP / "pb") == sha(SAME / "pb")  # the separate-turn run used the same driver
    # also in Claude's same-turn arena: ProgramBench's PROMPT.md (the separate-turn prompt) and the arena README
    assert sha(SAME / "claude_arena/PROMPT.md") == sha(SEP / "PROMPT.md")
    assert sha(SAME / "claude_arena/ARENA_README.md") == "325bb0b0a179ceeb8a4891c55872e8926d77ac75d9038d7148fe1466b053ca1c"


STAND_IN_AGENT = r"""#!/usr/bin/env python3
import json, os, re, sys
name, stdin = os.path.basename(sys.argv[0]), sys.stdin.read()
calls = sys.argv[0] + ".calls"
first = not os.path.exists(calls)
open(calls, "a").close()
if name == "claude" and first:  # the first Claude attempt hits a usage limit before doing any work
    print("usage limit reached")
    sys.exit(1)
keep = ("BASH_MAX_TIMEOUT_MS", "ANTHROPIC_API_KEY")
json.dump({"argv": sys.argv[1:], "stdin": stdin, "env": {k: os.environ.get(k) for k in keep},
           "cwd": sorted(os.listdir("."))}, open(sys.argv[0] + ".json", "w"))
ws = "tasks/%s/workspace" % re.search(r"ProgramBench task: (\S+)\.", stdin).group(1)
text = "ESTIMATE_MINUTES=5 saw " + ",".join(sorted(os.listdir(ws)))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}} if name == "claude"
                 else {"type": "item.completed", "item": {"type": "agent_message", "text": text}}))
open(f"{ws}/{name}_was_here", "w").close()
"""


def test_run_sh_exact_command_lines_and_separate_arenas(tmp_path):
    """Exact stdin, argv and env per agent (composed as in the original per-agent scripts), one arena per
    agent (`pb add` keeps an existing workspace) with the top-level files each agent saw, and the retry of an
    attempt that hit a usage limit."""
    bin_dir, here, tid = tmp_path / "bin", tmp_path / "same_turn", "owner__repo.abc1234"
    bin_dir.mkdir()
    here.mkdir()
    for name in ("pb", "run.sh", "extract.py", "prompts", "claude_arena"):
        os.symlink(SAME / name, here / name)
    (bin_dir / "docker").write_text(  # stands in for pull/create/export/rm of the cleanroom image
        '#!/bin/sh\nif [ "$1" = create ]; then echo cid; fi\n'
        'if [ "$1" = export ]; then d=$(mktemp -d); mkdir "$d/workspace"; echo doc > "$d/workspace/README.md"; '
        'tar -cf - -C "$d" workspace; fi\n')
    for name in ("docker", "claude", "codex"):
        if name != "docker":
            (bin_dir / name).write_text(STAND_IN_AGENT)
        (bin_dir / name).chmod(0o755)
    env = {"PATH": f"{bin_dir}:{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": str(tmp_path),
           "STALL_SECONDS": "0"}
    for name in ("claude", "codex"):
        subprocess.run(["bash", str(here / "run.sh"), name, tid], env=env, check=True, capture_output=True)
    system = (SAME / "prompts/system.md").read_text()
    task = (SAME / "prompts/task.txt").read_text().replace("{{ID}}", tid).rstrip("\n")
    claude = json.loads((bin_dir / "claude.json").read_text())
    assert claude["stdin"] == task
    assert claude["argv"] == ["--print", "--model", "claude-opus-4-8", "--append-system-prompt", system.rstrip("\n"),
                              "--disallowedTools", "WebFetch,WebSearch", "--dangerously-skip-permissions",
                              "--effort", "max", "--output-format", "stream-json", "--verbose"]
    assert claude["env"] == {"BASH_MAX_TIMEOUT_MS": "36000000", "ANTHROPIC_API_KEY": ""}
    assert claude["cwd"] == ["PROMPT.md", "README.md", "pb", "tasks"]
    for name, source in (("PROMPT.md", "PROMPT.md"), ("README.md", "ARENA_README.md")):
        assert (here / "arena-claude" / name).read_bytes() == (SAME / "claude_arena" / source).read_bytes()
    codex = json.loads((bin_dir / "codex.json").read_text())
    assert codex["stdin"] == system + "\n\n" + task
    assert codex["argv"] == ["exec", "--json", "--dangerously-bypass-approvals-and-sandbox", "--skip-git-repo-check",
                             "-m", "gpt-5.5", "-c", "model_reasoning_effort=xhigh"]
    assert codex["env"] == {"BASH_MAX_TIMEOUT_MS": None, "ANTHROPIC_API_KEY": None}
    assert codex["cwd"] == ["pb", "tasks"]
    assert (here / f"logs/{tid}.claude.ratelimit.1.log").read_text() == "usage limit reached\n"
    codex_ws = here / f"arena-codex/tasks/{tid}/workspace"
    assert sorted(p.name for p in codex_ws.iterdir()) == ["README.md", "codex_was_here"]
    assert (here / f"arena-claude/tasks/{tid}/workspace/claude_was_here").exists()
    for name in ("claude", "codex"):
        pb = here / f"arena-{name}/pb"
        assert not pb.is_symlink() and pb.read_bytes() == (SAME / "pb").read_bytes()
        assert (here / f"arena-{name}/runs/{name}_run/{tid}/submission.tar.gz").exists()
        assert json.loads((here / f"records/{tid}.{name}.json").read_text())["estimate_minutes"] == 5.0


def write_lines(path, records):
    path.write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))
    return path


def test_claude_record_last_match_wins_and_events(tmp_path):
    log = write_lines(tmp_path / "c.log", [
        {"type": "system", "subtype": "init", "model": "claude-opus-4-8"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "ESTIMATE_MINUTES=90\nstart"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash",
                                                       "input": {"command": "./pb probe x --help"}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "usage: x"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": "SELF_SCORE=50"},
            {"type": "text", "text": "PERCEIVED_MINUTES=40\nSELF_SCORE=85"}]}},
        {"type": "result", "is_error": False},
    ])
    rec = extract.record("claude", "x", "600", log)
    assert rec == {"instance_id": "x", "agent": "claude", "actual_minutes": 10.0, "estimate_minutes": 90.0,
                   "perceived_minutes": 40.0, "self_score": 85.0}
    assert extract.length_measures(log, "claude")["visible_event_count"] == 4
    assert extract.model_of(log, "claude") == "claude-opus-4-8"
    assert not extract.is_cutoff(log, "claude")
    with open(log, "a") as f:
        f.write('{"type":"result","is_error":true}\n')
    assert extract.is_cutoff(log, "claude")


def test_codex_record_and_cutoff(tmp_path):
    log = write_lines(tmp_path / "x.log", [
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "ESTIMATE_MINUTES=60"}},
        {"type": "item.completed", "item": {"type": "command_execution", "command": "ls", "aggregated_output": "a"}},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "PERCEIVED_MINUTES=20 SELF_SCORE=80"}},
        {"type": "turn.completed"},
    ])
    rec = extract.record("codex", "x", 1200, log)
    assert (rec["estimate_minutes"], rec["perceived_minutes"], rec["self_score"]) == (60.0, 20.0, 80.0)
    assert extract.length_measures(log, "codex")["visible_event_count"] == 4
    assert extract.model_of(log, "codex") is None  # Codex logs do not record the model
    assert not extract.is_cutoff(log, "codex")
    with open(log, "a") as f:
        f.write(json.dumps({"type": "turn.started"}) + "\n")
    assert extract.is_cutoff(log, "codex")


def test_score_is_passed_over_passed_plus_failed(tmp_path):
    path = tmp_path / "t.eval.json"
    path.write_text(json.dumps({"test_results": [{"status": s} for s in
                                                  ["passed"] * 3 + ["failure", "skipped", "not_run"]]}))
    assert extract.score_of(path) == 75
    path.write_text(json.dumps({"test_results": []}))
    assert extract.score_of(path) == 0


def test_fill_redaction():
    text = extract.redact("ESTIMATE_MINUTES=90 took 12 minutes; Finished release in 3.21s at 2026-06-25 21:41:07")
    assert "90" not in text and "12 minutes" not in text and "3.21s" not in text and "21:41" not in text
    assert "[SELF-REPORT REDACTED]" in text and "[TIMESTAMP REDACTED]" in text


def test_table_fills_perceived_minutes_out_of_context(tmp_path):
    """evals/<agent>/ may hold the eval.json files flat or as `programbench eval` writes them (<run>/<id>/)."""
    for sub in ("records", "logs", "evals/claude", "codex_run/t"):
        (tmp_path / sub).mkdir(parents=True)
    os.symlink(tmp_path / "codex_run", tmp_path / "evals/codex")
    for agent, perceived, eval_json in (("claude", None, "evals/claude/t.eval.json"),
                                        ("codex", 20.0, "codex_run/t/t.eval.json")):
        (tmp_path / f"records/t.{agent}.json").write_text(json.dumps({
            "actual_minutes": 12.5, "estimate_minutes": 30.0, "perceived_minutes": perceived, "self_score": 90.0}))
        write_lines(tmp_path / f"logs/t.{agent}.log", [{"type": "turn.started"}, {"type": "turn.completed"}])
        (tmp_path / eval_json).write_text(json.dumps({"test_results": [{"status": "passed"}]}))
    (tmp_path / "fills.json").write_text(json.dumps({"t": 33}))
    out = cli(SAME / "extract.py", "table", *(tmp_path / d for d in ("records", "logs", "evals", "fills.json")))
    rows = list(csv.DictReader(io.StringIO(out)))
    assert [(r["agent"], r["perceived_minutes"], r["perceived_source"], r["test_pass_score"]) for r in rows] == [
        ("claude", "33", "transcript-recovered", "100"), ("codex", "20.0", "in-context", "100")]
    assert set(rows[0]) == set(extract.COLUMNS)


def same_turn_rows():
    """12 tasks per agent with estimate = 10 * actual**0.5 (Claude) or 4 * actual (Codex), self-score 20 points over
    the test-pass score. Claude's first run is interrupted (left out of the fit); task 1 is a self-score exclusion."""
    rows = []
    for i, actual in enumerate((10, 12, 15, 20, 24, 30, 40, 50, 60, 80, 100, 120)):
        task = "ogham__dog.721440b" if i == 1 else f"t{i}"
        for agent, estimate in (("claude", 10 * actual ** 0.5), ("codex", 4 * actual)):
            rows.append({"instance_id": task, "agent": agent, "actual_minutes": actual, "estimate_minutes": estimate,
                         "perceived_minutes": 2 * actual, "self_score": 30 + 5 * i, "test_pass_score": 10 + 5 * i,
                         "cutoff": 0, "visible_event_count": 3 * actual, "full_visible_chars": 1, "raw_log_bytes": 1,
                         "compacted_chars": 1})
    rows[0].update(estimate_minutes=900, cutoff=1)
    rows[2].update(self_score=0, test_pass_score=100)
    return rows


def test_same_turn_analysis_on_synthetic_runs(tmp_path):
    path = write_csv(tmp_path / "records.csv", same_turn_rows(), extract.COLUMNS)
    rows = same_analyze.load(path)
    c_rows = [r for r in rows if r["agent"] == "claude"]
    claude, codex = same_analyze.summary(c_rows), same_analyze.summary([r for r in rows if r["agent"] == "codex"])
    actual, estimate = [r["actual_minutes"] for r in c_rows], [r["estimate_minutes"] for r in c_rows]
    assert claude["n"] == 12 and claude["mean_actual"] == pytest.approx(statistics.fmean(actual))
    assert claude["ratio"] == pytest.approx(statistics.fmean(estimate) / statistics.fmean(actual))
    fit = claude["compression"]  # the interrupted run is left out of the fit
    assert fit["n"] == 11 and fit["beta"] == pytest.approx(0.5) and fit["alpha"] == pytest.approx(math.log(10))
    assert fit["ci"] == pytest.approx((0.5, 0.5))
    assert codex["compression"]["beta"] == pytest.approx(1.0) and codex["ratio"] == pytest.approx(4.0)
    assert codex["within_1_5"] == 0 and claude["self_bias"] == pytest.approx(20.0) and claude["self_n"] == 11
    assert claude["r_log_after_run"] == pytest.approx(1.0) and claude["r_log_events"] == pytest.approx(1.0)
    out = cli(SAME / "analyze.py", path)
    assert "compression exponent 0.50 (95% CI 0.50-0.50, alpha 2.30, n = 11 non-interrupted runs)" in out
    short = estimate[:5], actual[:5]
    assert (f"Claude runs under 25 min (n = 5): Claude calibration ratio {sum(short[0]) / sum(short[1]):.2f}x; "
            "Codex on the same tasks 4.00x, Codex overall 4.00x") in out


# ---------------------------------------------------------------- separate turn

def test_separate_turn_prompt_and_question_hashes_match_the_run():
    assert sha(SEP / "PROMPT.md") == "41f80cc17e81c4d3247b6c081e90754ea0a811690f1e202cac99b5c0ae733b5e"
    statement = questions.extract_task_statement(SEP / "PROMPT.md", "ekzhang__bore.8e059cd")
    assert "mini-swe-agent" not in statement and statement.startswith("# ProgramBench task statement")
    want = {"text": "92005a188138de2883ef034d447ecc07eb9799197fa390b7139ae3672481fde9",
            "docs": "448c10f02358a4068781eb790ad2dec6b1ed1b57ce93c7d9f8fb32fd549d71b0",
            "probe": "09328e987c7bdd820ddf80fcf9c1ae445104797c450de830c96a36d230d8134f"}
    for surface, digest in want.items():
        question = questions.prospective_question(surface, statement)
        assert hashlib.sha256(question.encode()).hexdigest() == digest
    both = questions.MINUTES_QUESTION + "\0" + questions.SELF_SCORE_QUESTION
    assert hashlib.sha256(both.encode()).hexdigest() == \
        "7125f7cc05d872314c343264127a11b7131751dcae323b8e6772a1f551b61be1"


@pytest.mark.parametrize("text,value", [
    ("minutes = 120", 120), ("MINUTES=45\n", 45), ("I think\nreturn minutes = 90", 90),
    ("minutes = 0", None), ("minutes = 90.5", None), ("minutes = 60 or minutes = 90", None),
    ("minutes = <Number of minutes>", None), ("about 2 hours", None), ("minutes = 30, total_minutes = 40", None),
])
def test_parse_minutes(text, value):
    assert questions.parse_minutes(text) == value


@pytest.mark.parametrize("text,value", [
    ("self_score = 85", 85), ("SELF SCORE=100", 100), ("self_score = 0", 0),
    ("self_score = 101", None), ("self_score = 85 self_score = 90", None), ("score: 85", None),
])
def test_parse_self_score(text, value):
    assert questions.parse_self_score(text) == value


def test_scrubber_classes_preserve_length():
    cases = ["2026-08-03T01:42:36Z", "Aug  3 20:05", "real\t0m12.345s", "took 3.2s", "Finished in 0.52s",
             "mtime: 1754180000", "ok 0.123s", "at 13:04:59"]
    for text in cases:
        out = scrubber.scrub_text(text)
        assert out != text and len(out) == len(text) and "<" in out, text
    assert scrubber.scrub_text("2 files changed, 10 tests passed") == "2 files changed, 10 tests passed"
    assert scrubber.scrub_text("at 13:04:59") == "at <CLOCK_R"
    assert scrubber.scrub_text("2026-08-03T01:42:36Z") == "<ISO8601_REDACTED>__"
    assert scrubber.scrub_messages([{"role": "tool", "content": "took 3s"}]) == [{"role": "tool", "content": "took <E"}]


def test_reconstruction_and_replay_payload(tmp_path):
    claude = write_lines(tmp_path / "abc.jsonl", [
        {"type": "user", "sessionId": "abc", "message": {"role": "user", "content": "do the task"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "ok"},
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "a.txt"}]}]}},
        {"type": "summary", "summary": "not model-visible"},
    ])
    msgs = fork.reconstruct([claude], "claude")
    assert msgs == [{"role": "user", "content": "do the task"},
                    {"role": "assistant", "content": 'ok\n[tool_call name=Bash id=t1]\n{"command":"ls"}'},
                    {"role": "user", "content": "[tool_result id=t1]\na.txt"}]
    assert fork.session_files(tmp_path, "claude") == ("abc", [claude])
    payload = fork.replay_payload("opus-claude", msgs + [{"role": "system", "content": "s"}])
    assert payload["model"] == "claude-opus-5" and payload["output_config"] == {"effort": "xhigh"}
    assert "system" not in payload and payload["messages"][-1]["role"] == "user"

    rollout = write_lines(tmp_path / "rollout.jsonl", [
        {"type": "session_meta", "payload": {"id": "sid"}},
        {"type": "response_item", "payload": {"type": "message", "role": "developer",
                                              "content": [{"type": "input_text", "text": "rules"}]}},
        {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
                                              "arguments": "{\"cmd\":\"ls\"}"}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": "a.txt"}},
    ])
    msgs = fork.reconstruct([rollout], "codex")
    assert [m["role"] for m in msgs] == ["developer", "assistant", "tool"]
    assert msgs[2]["content"] == "[tool_result id=c1]\na.txt"
    payload = fork.replay_payload("sol-codex", msgs)
    assert [m["role"] for m in payload["messages"]] == ["system", "assistant", "user"]
    assert payload["reasoning_effort"] == "xhigh" and payload["model"] == "gpt-5.6-sol"


def test_codex_stderr_answer():
    stderr = "user\nHow well did you do?\ncodex\nthinking...\ncodex\nself_score = 88\ntokens used\n1,234\n"
    assert fork.codex_stderr_answer(stderr) == "self_score = 88"
    assert fork.codex_stderr_answer("no marker") is None


def test_oracle_tool_returns_parent_wall_seconds():
    elapsed = oracle_tool.canonical_elapsed_seconds("5219.49260773600050")
    assert elapsed == "5219.4926077360005"
    reply = oracle_tool.handle_request({"id": 1, "method": "tools/call", "params": {"name": "elapsed_seconds"}},
                                       elapsed)
    assert reply["result"]["content"] == [{"type": "text", "text": "ELAPSED_SECONDS=5219.4926077360005"}]
    tools = oracle_tool.handle_request({"id": 2, "method": "tools/list"}, elapsed)["result"]["tools"]
    assert [t["name"] for t in tools] == ["elapsed_seconds"]


def test_add_oracle_configs(tmp_path):
    claude_home, codex_home = tmp_path / "c", tmp_path / "x"
    claude_home.mkdir()
    codex_home.mkdir()
    (claude_home / ".claude.json").write_text(json.dumps({"keep": 1}))
    fork.add_oracle(claude_home, "opus-claude", 61.5)
    config = json.loads((claude_home / ".claude.json").read_text())
    assert config["keep"] == 1 and config["mcpServers"]["elapsed"]["env"] == {"ELAPSED_SECONDS": "61.5"}
    tool = claude_home / ".timeablations-tools" / "oracle_tool.py"
    assert config["mcpServers"]["elapsed"]["args"] == [str(tool.resolve())]
    fork.add_oracle(codex_home, "sol-codex", 61.5)
    import tomllib
    toml = tomllib.loads((codex_home / ".codex/config.toml").read_text())
    assert toml["mcp_servers"]["elapsed"]["env"] == {"ELAPSED_SECONDS": "61.5"}


def test_invocations_pin_effort_and_tasks_json():
    for cell in invocations.CELLS.values():
        argvs = [cell["run"]] + [a for pair in cell["fork"].values() for a in pair]
        assert all(any("xhigh" in arg for arg in argv) for argv in argvs)
    cells = invocations.CELLS
    assert "claude-opus-5" in cells["opus-claude"]["run"] and "gpt-5.6-sol" in cells["sol-codex"]["run"]
    tasks = json.loads((SEP / "tasks.json").read_text())["tasks"]
    assert len(tasks) == 20 and "kaushiksrini__parqeye.8072121" in tasks
    assert "hairyhenderson__gomplate.05eb3aa" not in tasks  # substituted out (tasks.json says why)


def test_separate_turn_analysis_on_synthetic_data(tmp_path):
    """Forecasts at 2x the runtime, fork answers equal to the runtime and 10 points above the official score;
    rows that are not `completed` must not enter any average."""
    runs, scores, forecasts, forks = [], [], [], []
    for task, (minutes, score) in {"o__aa.1": (60, 90.0), "o__bb.2": (120, 50.0)}.items():
        for cell, m, sc in (("opus-claude", minutes, score), ("sol-codex", minutes // 2, score - 10)):
            key = {"task_id": task, "cell": cell}
            runs.append({**key, "wall_seconds": 60 * m})
            scores.append({**key, "score": sc})
            for surface in sep_analyze.SURFACES:
                forecasts += [{**key, "surface": surface, "status": "completed", "minutes": 2 * m},
                              {**key, "surface": surface, "status": "parse_invalid", "minutes": ""}]
            for arm in sep_analyze.ARMS:
                forks += [{**key, "arm": arm, "status": "completed", "minutes": m, "self_score": int(sc) + 10},
                          {**key, "arm": arm, "status": "interrupted", "minutes": 1, "self_score": 0}]
    for name, rows in (("runs", runs), ("scores", scores), ("forecasts", forecasts), ("forks", forks)):
        write_csv(tmp_path / f"{name}.csv", rows)
    out = cli(SEP / "analyze.py", tmp_path)
    for expected in ("median runtime 90.0 min (range 60-120); official score median 70.00%",
                     "median runtime 45.0 min (range 30-60); official score median 60.00%",
                     "forecasts 12, usable 6, above the measured runtime 6",
                     "P2 docs   mean |log(forecast/actual)| 0.69, median forecast 180 min  (n = 2)",
                     "scrubbed        mean |log(report/actual)| 0.000  self-score error median +10.0  (n = 2)",
                     "self-score error, all arms: median +10.0 pts, below official 0/10",
                     "bb          50.0% -> 60.0    40.0% -> 50.0"):
        assert expected in out


def fake_cli(bin_dir, name, body):
    path = bin_dir / name
    path.write_text("#!/usr/bin/env python3\nimport json, sys\nargs = sys.argv[1:]\n" + body)
    path.chmod(0o755)


def test_text_forecast_with_a_stand_in_cli(tmp_path, monkeypatch):
    bin_dir, home = tmp_path / "bin", tmp_path / "template"
    bin_dir.mkdir()
    home.mkdir()
    fake_cli(bin_dir, "codex", "import os\nopen(sys.argv[0] + '.args', 'w').write(json.dumps(args))\n"
                               "open(sys.argv[0] + '.env', 'w').write(json.dumps(dict(os.environ)))\n"
                               "print('Roughly two hours.\\nreturn minutes = 120')\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    out = execute.forecast("ekzhang__bore.8e059cd", "sol-codex", "text", tmp_path / "row", home)
    assert out == {"task_id": "ekzhang__bore.8e059cd", "cell": "sol-codex", "surface": "text",
                   "status": "completed", "minutes": 120}
    args = json.loads((bin_dir / "codex.args").read_text())
    statement = questions.extract_task_statement(SEP / "PROMPT.md", "ekzhang__bore.8e059cd")
    assert args[:-1] == invocations.CELLS["sol-codex"]["run"][1:-1]
    assert args[-1] == questions.prospective_question("text", statement)
    env = json.loads((bin_dir / "codex.env").read_text())
    surface = tmp_path / "row" / "surface"
    assert {k: v for k, v in env.items() if k.startswith("TIMEABLATIONS_")} == {
        "TIMEABLATIONS_ROW_HOME": str(tmp_path / "row" / "home"), "TIMEABLATIONS_ROW_ID": "row",
        "TIMEABLATIONS_SURFACE_ROOT": str(surface), "TIMEABLATIONS_TASK_ID": "ekzhang__bore.8e059cd"}
    assert sorted(p.name for p in surface.iterdir()) == ["TASK.md"]
    assert not surface.stat().st_mode & 0o222


def test_codex_native_fork_with_a_stand_in_cli(tmp_path, monkeypatch, capsys):
    parent = tmp_path / "parent"
    (parent / "home/sessions").mkdir(parents=True)
    write_lines(parent / "home/sessions/rollout-x-sid9.jsonl", [{"type": "session_meta", "payload": {"id": "sid9"}}])
    (parent / "arena/tasks/t/workspace").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_cli(bin_dir, "codex", "import os\nassert args[:2] == ['exec', 'resume'] and args[-2] == 'sid9'\n"
                               "assert os.environ['TIMEABLATIONS_PARENT_ROW_ID'] == 'parent'\n"
                               "assert os.environ['TIMEABLATIONS_ROW_ID'] == 'arm'\n"
                               "print('minutes = 31' if 'How long' in args[-1] else 'stdout echo', flush=True)\n"
                               "sys.stderr.write('codex\\nself_score = 82\\ntokens used\\n9\\n')\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    fork.main(parent, "sol-codex", "context-only", tmp_path / "arm", 1860.0)
    assert json.loads(capsys.readouterr().out) == {"task_id": "t", "cell": "sol-codex", "arm": "context-only",
                                                   "status": "completed", "minutes": 31, "self_score": 82}
