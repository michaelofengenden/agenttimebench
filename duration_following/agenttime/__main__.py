"""python -m agenttime {suite,plan,prompt,stage}

suite   print Table 2: tasks and request range per benchmark
plan    list every run (666 per agent) with its request and hidden cutoff (the code's backstop)
prompt  write the exact prompt for one task and request, given the native task message
        (ALE-Bench: statement_en.md plus --ale-data with the problem's data.json)
stage   write a Harbor task directory and job.json for one Terminal-Bench or TUA-Bench run;
        start it with `harbor run --config job.json`
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import signal
import sys
import tomllib

from . import benchmarks, harness, prompt, supervisor

AGENT_CLASSES = {
    "fable": f"{__package__}.harbor_agents:FableClaudeCode",
    "sol": f"{__package__}.harbor_agents:SolCodex",
    "astra": f"{__package__}.harbor_agents:AstraCodex",
}
# Left out of the staged task: the reference solution and the task-root cheat directory.
EXCLUDED_AT_TASK_ROOT = ("solution", "cheat")
# Benchmarks `stage` supports and their native agent.timeout_sec, which the backstop replaced.
HARBOR_TIMEOUT_SEC = {"terminal-bench": 28800, "tua-bench": 2400}
# The supervisor process policy that most runs of an agent on a benchmark used:
# strict-descendants for these pairs, owned-quiescence for the others.
STRICT_POLICY_RUNS = {("sol", "tua-bench"), ("astra", "tua-bench"), ("sol", "terminal-bench")}
PLAN_COLUMNS = ("agent", "benchmark", "task_id", "request", "request_text", "request_ms", "cutoff_ms")


def compiled_prompt(benchmark: str, task_id: str, condition: str, native: bytes,
                    ale_problem: dict | None = None) -> tuple[bytes, tuple]:
    requests = benchmarks.requests_ms(benchmarks.find_task(benchmark, task_id))
    text = prompt.delivered_task_text(benchmark, native, ale_problem=ale_problem)
    return prompt.compile_prompt(text, requests[prompt.CONDITIONS.index(condition)], requests[1]), requests


def suite(args) -> None:
    def text(minutes: float) -> tuple[str, str]:
        return (f"{minutes:g}", "min") if minutes < 60 else (f"{round(minutes / 60, 1):g}", "h")
    roster = benchmarks.load_roster()
    for entry in roster:
        lo, lo_unit = text(min(task["requests_minutes"][0] for task in entry["tasks"]))
        hi, hi_unit = text(max(task["requests_minutes"][2] for task in entry["tasks"]))
        span = f"{lo}–{hi} {hi_unit}" if lo_unit == hi_unit else f"{lo} {lo_unit}–{hi} {hi_unit}"
        print(f"{entry['name']}\t{len(entry['tasks'])}\t{span}")
    print(f"Total\t{sum(len(entry['tasks']) for entry in roster)}")


def plan(args) -> None:
    print("\t".join(PLAN_COLUMNS))
    for agent in [args.agent] if args.agent else benchmarks.AGENTS:
        rows = list(benchmarks.cells(agent))
        for row in rows:
            print("\t".join(str(row[column]) for column in PLAN_COLUMNS))
        print(f"{agent}: {len(rows)} runs", file=sys.stderr)


def write_prompt(args) -> None:
    ale_problem = json.loads(Path(args.ale_data).read_text()) if args.ale_data else None
    data, _ = compiled_prompt(args.benchmark, args.task, args.request, Path(args.native).read_bytes(), ale_problem)
    sys.stdout.buffer.write(data)


def stage(args) -> None:
    if args.benchmark not in HARBOR_TIMEOUT_SEC:
        raise SystemExit(f"stage supports {', '.join(HARBOR_TIMEOUT_SEC)}")
    source, out = Path(args.task_dir), Path(args.out).resolve()
    native_timeout = HARBOR_TIMEOUT_SEC[args.benchmark]
    if tomllib.loads((source / "task.toml").read_text()).get("agent", {}).get("timeout_sec") != native_timeout:
        raise SystemExit(f"expected the native agent.timeout_sec = {native_timeout}")
    data, requests = compiled_prompt(args.benchmark, args.task, args.request,
                                     (source / "instruction.md").read_bytes())
    if prompt.load_harbor_instruction(data) != data:
        raise SystemExit("Harbor would change the staged instruction")
    backstop_sec = prompt.backstop_ms(requests[2]) // 1000
    shutil.copytree(source, out / "task", ignore=lambda directory, names: [
        name for name in names if Path(directory) == source and name in EXCLUDED_AT_TASK_ROOT])
    (out / "task" / "instruction.md").write_bytes(data)
    agent = harness.VERSIONS[args.agent]
    fable = args.agent == "fable"
    policy = supervisor.STRICT if (args.agent, args.benchmark) in STRICT_POLICY_RUNS else supervisor.QUIESCENCE
    job = {
        "job_name": f"{args.benchmark}-{args.task}-{args.request}-{args.agent}",
        "jobs_dir": str(out / "jobs"),
        "n_attempts": 1, "n_concurrent_trials": 1, "retry": {"max_retries": 0},
        "timeout_multiplier": 1, "agent_timeout_multiplier": 1,
        "environment": {"type": "docker", "delete": True},
        "verifier": {"disable": False},
        "tasks": [{"path": str(out / "task")}],
        "agents": [{
            "import_path": AGENT_CLASSES[args.agent], "model_name": agent["model"],
            "override_timeout_sec": backstop_sec + 60,  # the supervisor's backstop ends the run
            "kwargs": {"version": agent["version"], "reasoning_effort": agent["effort"],
                       "config": {"reasoning_effort": agent["effort"]} if fable else {},
                       "backstop_sec": backstop_sec, "process_policy": policy},
            "env": dict(harness.CLAUDE_JOB_ENV) if fable else {},
        }],
    }
    (out / "job.json").write_text(json.dumps(job, indent=2) + "\n")
    print(out / "job.json")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m agenttime", description=__doc__.split("\n\n", 1)[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("suite", help="print Table 2").set_defaults(func=suite)
    p = commands.add_parser("plan", help="list all runs")
    p.add_argument("--agent", choices=benchmarks.AGENTS)
    p.set_defaults(func=plan)
    for name, func, help_text in (("prompt", write_prompt, "write one compiled prompt to stdout"),
                                  ("stage", stage, "stage one Harbor run")):
        p = commands.add_parser(name, help=help_text)
        p.add_argument("--benchmark", required=True)
        p.add_argument("--task", required=True)
        p.add_argument("--request", required=True, choices=prompt.CONDITIONS)
        p.set_defaults(func=func)
    commands.choices["prompt"].add_argument("native", help="the native task message (Harbor: instruction.md)")
    commands.choices["prompt"].add_argument("--ale-data", help="ALE-Bench: the problem's data.json")
    commands.choices["stage"].add_argument("--agent", required=True, choices=benchmarks.AGENTS)
    commands.choices["stage"].add_argument("--task-dir", required=True, help="native Harbor task directory")
    commands.choices["stage"].add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command in ("prompt", "stage"):
        try:
            benchmarks.find_task(args.benchmark, args.task)
        except KeyError:
            parser.exit(2, f"unknown task id {args.benchmark}/{args.task}; see python -m agenttime plan\n")
    args.func(args)


if __name__ == "__main__":
    if hasattr(signal, "SIGPIPE"):  # `plan | head` ends quietly (POSIX)
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
