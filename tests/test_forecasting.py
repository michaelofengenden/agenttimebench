"""forecasting: prompt bytes, harness options and flags, the reply parser and the task list.

The runners are driven offline: a stub Claude Agent SDK module and a stub `codex` binary record what
they receive, so no model is called.
"""
import collections
import csv
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1] / "forecasting"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

FORECAST = ('How long will completing this task take you? Reply with MINUTES=<positive integer>.\n\n'
            'The task:\n"""\n{task}\n"""')
# Byte-identical to the preamble of the banked pre-27-July-2026 prompts (checked outside this repo).
LEGACY_FABLE = ('Complete the following task. Work carefully; you may use the tools available to compute or verify in\n'
                'your working directory. When finished, state your final answer on a line beginning "FINAL ANSWER:".\n\n'
                '--- TASK ---\n{task}\n--- END TASK ---')
LEGACY_SOL = ('Complete the following task. Work carefully; you may compute or verify using the shell in your\n'
              'working directory. When finished, state your final answer on a line beginning "FINAL ANSWER:".\n\n'
              '--- TASK ---\n{task}\n--- END TASK ---')
TASK = '  Placeholder task: compute 2 & 3, write "out.txt" {keep braces}.\n\n'

STUB_SDK = """import fs from 'node:fs'
export function query({ prompt, options }) {
  const { env, stderr, abortController, ...rest } = options
  fs.writeFileSync(process.env.STUB_LOG, JSON.stringify({ prompt, options: rest, env }))
  return (async function* () {
    yield { type: 'system', subtype: 'init', session_id: 's1' }
    yield { type: 'assistant', message: { model: options.model, content: [{ type: 'text', text: 'MINUTES=7' }] } }
    yield { type: 'result', subtype: 'success', is_error: false, result: 'MINUTES=7', total_cost_usd: 0.01,
            num_turns: 1, usage: {}, modelUsage: { [options.model]: {}, 'claude-haiku-4-5': {} },
            ...JSON.parse(process.env.STUB_RESULT || '{}') }
  })()
}
"""
STUB_CODEX = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("codex-cli 0.145.0"); sys.exit(0)
log = {"argv": args}
if args[-1] == "-":
    log["prompt"] = sys.stdin.buffer.read().decode()
    print("MINUTES=7")
else:
    log["prompt"], log["config"] = args[-1], open(os.environ["CODEX_HOME"] + "/config.toml").read()
    open(args[args.index("--output-last-message") + 1], "w").write("done")
open(os.environ["STUB_LOG"], "w").write(json.dumps(log))
sys.exit(int(os.environ.get("STUB_RC", "0")))
"""


@pytest.fixture
def env(tmp_path):
    (tmp_path / "sdk.mjs").write_text(STUB_SDK)
    codex = tmp_path / "codex"
    codex.write_text(STUB_CODEX)
    codex.chmod(0o755)
    (tmp_path / "task.txt").write_text(TASK)
    (tmp_path / "home").mkdir()
    return dict(os.environ, CLAUDE_AGENT_SDK=str(tmp_path / "sdk.mjs"), CODEX_BIN=str(codex),
                STUB_LOG=str(tmp_path / "stub.json"), ART_ROOT=str(tmp_path / "runs"), HOME=str(tmp_path / "home"),
                ANTHROPIC_API_KEY="sk-ant-api-offline-test", FAKE_API_KEY="secret")


def run(cmd, env, tmp_path):
    subprocess.run(cmd, cwd=tmp_path, env=env, check=True, capture_output=True)
    return json.loads((tmp_path / "stub.json").read_text())


def test_prompt_files_and_config():
    assert (HERE / "prompts/forecast.txt").read_text() == FORECAST
    assert (HERE / "prompts/legacy_wrapper_fable.txt").read_text() == LEGACY_FABLE
    assert (HERE / "prompts/legacy_wrapper_sol.txt").read_text() == LEGACY_SOL
    config = [l for l in (HERE / "codex_sol.toml").read_text().splitlines() if not l.startswith("#")]
    assert config == ['model = "gpt-5.6-sol"', 'model_reasoning_effort = "xhigh"', 'service_tier = "default"',
                      'approval_policy = "never"', 'sandbox_mode = "danger-full-access"']


@needs_node
def test_forecast_prompts_agree_and_harness_options(env, tmp_path):
    fable = run([NODE, HERE / "forecast_fable.mjs", "task.txt", "Src", "t/1", "out"], env, tmp_path)
    sol = run(["bash", HERE / "forecast_sol.sh", "task.txt", "Src", "t/1", "out"], env, tmp_path)
    expected = FORECAST.replace("{task}", TASK)
    assert fable["prompt"] == sol["prompt"] == expected
    options = dict(fable["options"])
    assert not os.path.exists(options.pop("cwd"))  # the clean room is removed afterwards
    assert options == {"model": "claude-fable-5", "effort": "xhigh", "settingSources": [], "persistSession": False,
                       "permissionMode": "bypassPermissions", "allowedTools": [],
                       "disallowedTools": ["Read", "Glob", "Grep", "Bash", "Write", "Edit", "WebFetch", "WebSearch"],
                       "settings": {"disableAllHooks": True, "disableBundledSkills": True}, "maxTurns": 1}
    assert set(fable["env"]) <= {"HOME", "PATH", "TMPDIR", "SHELL", "USER", "LOGNAME", "LANG", "LC_ALL", "TERM",
                                 "ANTHROPIC_API_KEY"}
    argv = sol["argv"]
    assert argv[:7] == ["exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "--ignore-user-config", "-C"]
    assert argv[8:] == ["-c", "model=gpt-5.6-sol", "-c", "model_reasoning_effort=xhigh", "-c", "personality=none", "-"]
    for arm in ("fable", "sol"):
        record = json.loads((tmp_path / f"out/Src__t_1.{arm}.json").read_text())
        assert (record["outcome"], record["estimate_min"], record["parse_method"]) == ("ok", 7, "regex")


@needs_node
@pytest.mark.parametrize("legacy", [False, True])
def test_no_request_runs(env, tmp_path, legacy):
    flag = ["--legacy-wrapper"] if legacy else []
    fable = run([NODE, HERE / "run_fable.mjs", *flag, "task.txt", "cfg", "Src", "t1"], env, tmp_path)
    sol = run(["bash", HERE / "run_sol.sh", *flag, "task.txt", "Src", "t1"], env, tmp_path)
    fable_text, sol_text = TASK.strip(), TASK.rstrip("\n")
    want_fable = LEGACY_FABLE.replace("{task}", fable_text) if legacy else fable_text
    want_sol = LEGACY_SOL.replace("{task}", sol_text) if legacy else sol_text
    assert fable["prompt"] == want_fable == (tmp_path / "runs/claude-fable-5/Src__t1/r1/prompt.txt").read_text()
    assert sol["prompt"] == want_sol == (tmp_path / "runs/gpt-5.6-sol/Src__t1/r1/prompt.txt").read_text()
    options = dict(fable["options"])
    assert options.pop("cwd").endswith("runs/claude-fable-5/Src__t1/r1/workspace")
    assert options == {"model": "claude-fable-5", "effort": "xhigh", "settingSources": [], "persistSession": True,
                       "permissionMode": "bypassPermissions", "allowDangerouslySkipPermissions": True,
                       "allowedTools": ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "Task", "TodoWrite",
                                        "NotebookEdit"],
                       "includePartialMessages": False,
                       "settings": {"disableAllHooks": True, "disableBundledSkills": True}}
    assert "FAKE_API_KEY" not in fable["env"] and "ANTHROPIC_API_KEY" not in fable["env"]
    assert fable["env"]["CLAUDE_CONFIG_DIR"] == "cfg"
    work = str(tmp_path / "runs/gpt-5.6-sol/Src__t1/r1/workspace")
    assert sol["argv"][:-1] == ["exec", "--json", "--skip-git-repo-check", "-s", "workspace-write",
                                "--dangerously-bypass-approvals-and-sandbox", "--disable", "web_search", "-C", work,
                                "--output-last-message", str(Path(work).parent / "final_output.txt")]
    assert sol["config"] == (HERE / "codex_sol.toml").read_text()
    for model in ("claude-fable-5", "gpt-5.6-sol"):
        meta = json.loads((tmp_path / f"runs/{model}/Src__t1/r1/meta.json").read_text())
        assert meta["answered"] and not meta["censored"] and meta["legacy_wrapper"] == legacy
    assert json.loads((tmp_path / "runs/claude-fable-5/Src__t1/r1/meta.json").read_text())["served_models"] == [
        "claude-fable-5"]


@needs_node
def test_web_only_for_open_web_tasks(env, tmp_path):
    fable = run([NODE, HERE / "run_fable.mjs", "task.txt", "cfg", "AssistantBench", "validation:0"], env, tmp_path)
    sol = run(["bash", HERE / "run_sol.sh", "task.txt", "AssistantBench", "validation:0"], env, tmp_path)
    assert fable["options"]["allowedTools"][-2:] == ["WebFetch", "WebSearch"]
    assert "--disable" not in sol["argv"]


@needs_node
@pytest.mark.parametrize("result, reason",[({"api_error_status": 429, "is_error": True}, "quota_429"),
                                            ({"subtype": "error_max_turns", "is_error": True}, "max_turns")])
def test_censoring(env, tmp_path, result, reason):
    run([NODE, HERE / "run_fable.mjs", "task.txt", "cfg", "Src", "t1"], dict(env, STUB_RESULT=json.dumps(result)),
        tmp_path)
    subprocess.run(["bash", HERE / "run_sol.sh", "task.txt", "Src", "t1"], cwd=tmp_path, env=dict(env, STUB_RC="142"))
    fable = json.loads((tmp_path / "runs/claude-fable-5/Src__t1/r1/meta.json").read_text())
    sol = json.loads((tmp_path / "runs/gpt-5.6-sol/Src__t1/r1/meta.json").read_text())
    assert (fable["censored"], fable["censor_reason"], fable["is_error"], fable["answered"]) == (True, reason, False, False)
    assert (sol["censored"], sol["answered"]) == (True, False)  # 142: the SIGALRM of a wall-clock cap


@needs_node
def test_fresh_run_truncates_logs_and_keeps_seed_symlinks(env, tmp_path):
    art = tmp_path / "runs/claude-fable-5/Src__t1/r1"
    art.mkdir(parents=True)
    (art / "events.jsonl").write_text("stale\n")  # from an attempt killed before it wrote meta.json
    (tmp_path / "seed/sub").mkdir(parents=True)
    (tmp_path / "seed/sub/a.txt").write_text("a")
    (tmp_path / "seed/link").symlink_to("sub/a.txt")
    run([NODE, HERE / "run_fable.mjs", "task.txt", "cfg", "Src", "t1", "1", "seed"], env, tmp_path)
    assert "stale" not in (art / "events.jsonl").read_text()
    with tarfile.open(art / "workspace.tar.gz") as tar:
        assert tar.getmember("workspace/link").linkname == "sub/a.txt"


STUB_HARBOR = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
trial = os.path.join(args[args.index("--trials-dir") + 1], os.path.basename(args[args.index("-p") + 1]) + "__x1")
os.makedirs(trial)
json.dump({"agent_execution": {"started_at": "2026-07-28T10:00:00Z", "finished_at": "2026-07-28T10:12:30Z"}},
          open(os.path.join(trial, "result.json"), "w"))
open(os.environ["STUB_LOG"], "w").write(json.dumps({"argv": args}))
"""


def test_harbor_command_and_clock(env, tmp_path):
    harbor = tmp_path / "harbor"
    harbor.write_text(STUB_HARBOR)
    harbor.chmod(0o755)
    (tmp_path / "tua-task").mkdir()
    (tmp_path / "tua-task/instruction.md").write_text("Placeholder.\n")
    env = dict(env, HARBOR=str(harbor), TRIALS=str(tmp_path / "trials"))
    argv = run(["bash", HERE / "run_harbor_sol.sh", "tua-task"], env, tmp_path)["argv"]
    assert argv == ["trial", "start", "-p", "tua-task", "--trials-dir", str(tmp_path / "trials"), "-a", "codex",
                    "-m", "openai/gpt-5.6-sol", "--agent-kwarg", "reasoning_effort=xhigh", "--agent-timeout", "86400",
                    "--agent-setup-timeout", "3600", "--environment-build-timeout-multiplier", "3.0"]
    row = json.loads((tmp_path / "trials/runtimes.jsonl").read_text())
    assert (row["task_id"], row["actual_min"], row["censored"]) == ("tua-task", 12.5, False)
    # Frontier-Bench's closing time-limit sentence is removed in place before the run.
    (tmp_path / "fb-task").mkdir()
    (tmp_path / "fb-task/instruction.md").write_text("Do X.\n\nYou have 3,600 seconds to complete this task.\n")
    assert run(["bash", HERE / "run_harbor_sol.sh", "fb-task"], env, tmp_path)["argv"][3] == "fb-task"
    assert (tmp_path / "fb-task/instruction.md").read_text() == "Do X.\n\n"
    # A cap the removal does not match still stops the run.
    (tmp_path / "tua-task/instruction.md").write_text("Placeholder. You have 3600 seconds to complete this task\n")
    assert subprocess.run(["bash", HERE / "run_harbor_sol.sh", "tua-task"], cwd=tmp_path, env=env).returncode == 92


@needs_node
def test_parse_estimate():
    cases = {"MINUTES=45": ("ok", 45, "regex", False), "  MINUTES = 45  ": ("ok", 45, "regex", False),
             "some context\nMINUTES=45\nthanks": ("ok", 45, "regex", False),
             "MINUTES=0": ("parse_fail", None, "regex", False), "I think MINUTES=45 maybe": ("parse_fail", None, "none", True),
             "MINUTES=45\nMINUTES=60": ("parse_fail", None, "none", True), "MINUTES=4.5": ("parse_fail", None, "none", True)}
    script = ("import { parseEstimateRegex, parseEstimate } from './parse_estimate.mjs'\n"
              f"const out = {json.dumps(list(cases))}.map(parseEstimateRegex)\n"
              "out.push(await parseEstimate('no number here'))\n"
              "console.log(JSON.stringify(out))")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    proc = subprocess.run([NODE, "--input-type=module", "-e", script], cwd=HERE, env=env, check=True,
                          capture_output=True, text=True)
    *got, fallback = json.loads(proc.stdout)
    assert [(r["outcome"], r["estimate_min"], r["parse_method"], r["needs_llm"]) for r in got] == list(cases.values())
    assert (fallback["outcome"], fallback["parse_method"]) == ("parse_fail", "none")
    assert "ANTHROPIC_API_KEY is required" in fallback["parser_error"]


def test_task_headers():
    sections = dict(s.split("\n", 1) for s in re.split(r"(?m)^== ", (HERE / "prompts/task_headers.txt").read_text())[1:])
    patterns = sections.pop("Budget lines removed (Python regular expressions)").splitlines()
    assert len(patterns) == 7 and all(re.compile(p) for p in patterns)
    assert set(sections) == {r["source"] for r in csv.DictReader((HERE / "tasks.csv").open())}
    assert all(body.count("{task}") == 1 for body in sections.values())
    assert sections["Humanity's Last Exam"] == "Humanity's Last Exam question. Answer the following.\n\n{task}\n"


def test_task_list():
    rows = list(csv.DictReader((HERE / "tasks.csv").open()))
    assert len(rows) == 235 and len({(r["source"], r["task_id"]) for r in rows}) == 235
    counts = collections.Counter(r["source"] for r in rows)
    assert len(counts) == 18 and counts["GPQA-Diamond"] == 60 and counts["Humanity's Last Exam"] == 18
    assert all(len(v) <= 100 for r in rows for v in r.values())
    assert all(r["upstream_id"] for r in rows if r["source"] in ("GPQA-Diamond", "AssistantBench", "OSWorld 2.0"))
