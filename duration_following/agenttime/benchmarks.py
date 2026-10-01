"""The 18 benchmarks: roster, how each task was delivered, and which native grader scored it.

Task text is never stored here; every task is loaded from its pinned upstream source
(url and pin in roster.json). Scores come from each benchmark's own grader.
"""
from __future__ import annotations

import json
from pathlib import Path

from .prompt import CONDITIONS, backstop_ms, minutes_to_ms, request_text

ROSTER_PATH = Path(__file__).with_name("roster.json")
AGENTS = ("fable", "sol", "astra")

# OSWorld 2.0 and Agents' Last Exam ran on a desktop VM and were not timed by supervisor.py.
DESKTOP_TIMING = ("The desktop controller timed the CLI process from launch to exit and stopped it at the "
                  "backstop plus a grace period.")

# benchmark -> (how the task reached the agent, native time limits removed (exact texts in
# prompt.py), native grader that produced the score)
NATIVE = {
    "gpqa-diamond": (
        "Upstream zero-shot prompt (GPQA_PROMPT) as the only message; empty system prompt; the clock is the only tool.",
        "none",
        "gpqa baselines answer parser (patterns 'answer is (X)', 'Answer: (X)', 'answer: (X)', 'answer (X)', "
        "'(X)', in that order) on the final message; 1 if the letter matches the key"),
    "humanitys-last-exam": (
        "HLE_SYSTEM_PROMPT as system prompt (Codex: developer_instructions) and the question as the message; "
        "text-only multiple-choice questions; the clock is the only tool.",
        "none",
        "hle_eval/run_judge_results.py extract_answer, judge o3-mini-2025-01-31; 1 if judged correct"),
    "appworld": (
        "Task.load(task_id).instruction; app APIs through a ./apis command over the pinned AppWorld API server.",
        "max_interactions=1000",
        "appworld.evaluator.evaluate_task on the saved final state; 1 if every test passes"),
    "assistantbench": (
        "The validation question; a BrowserGym browser through 7 MCP tools (observe, click, type, scroll, goto, "
        "back, final_answer).",
        "max_episode_steps=30",
        "BrowserGym AssistantBench question_scorer accuracy"),
    "osworld": (
        "task.instruction; the OSWorld 2.0 VM through 10 MCP computer tools (done: DONE_TOOL_DESCRIPTION); "
        "empty system prompt. " + DESKTOP_TIMING,
        "reference launcher turn limit (500, fallback 15) not used",
        "task.evaluate(env) on the live VM (checkpoint-weighted score in [0, 1])"),
    "terminal-bench": (
        "Harbor task directory; instruction.md through the Harbor loader.",
        "instruction sentence 'You have 28800 seconds to complete this task.'; agent.timeout_sec=28800",
        "the task's tests/test.sh in Harbor's separate verifier environment (reward 0 or 1)"),
    "programbench": (
        "Cleanroom workspace (documentation and ./executable, which runs the reference binary); native "
        "instruction.md; no internet.",
        "none (the grader's per-branch limit is kept)",
        "programbench.eval.eval_batch.run_eval_batch; passed / non-ignored tests"),
    "agents-last-exam": (
        "TaskLoader description (variant 0); input/ and software/ staged in the sandbox; computer tools, "
        "run_command and done (DONE_TOOL_DESCRIPTION) over MCP. " + DESKTOP_TIMING,
        "vm.timeout=7200; two budget phrases in the SUMO task's input/task_prompt.md",
        "the task's own TaskDriver.evaluate() (partial credit in [0, 1])"),
    "core-bench": (
        "HAL CoreBenchHard prompt (_construct_prompt) with the hard-mode capsule files.",
        "none",
        "HAL CoreBench evaluate_output / get_metrics accuracy (all answers inside the 95% prediction interval)"),
    "deepswe": (
        "Harbor task directory; instruction.md through the Harbor loader; the agent's network was limited to "
        "the model provider's hosts (Harbor agent extra_allowed_hosts) and the verifier had no network.",
        "agent.timeout_sec=10800",
        "committed diff applied in the separate verifier environment; tests/test.sh reward"),
    "tua-bench": (
        "Harbor task directory; instruction.md through the Harbor loader.",
        "agent.timeout_sec=2400",
        "the task's tests/test.sh reward in [0, 1]"),
    "pptarena": (
        "Native TASK_PROMPT of agent_bench/run_agents.py with deck.pptx and INSTRUCTION.md in the workspace.",
        "1800-second idle watchdog of run_agents.py",
        "agent_bench judge_case, judge gpt-5.1-2025-11-13, median of 3; score = (IF + VQ) x 10"),
    "sakana-ale-bench": (
        "prompt.ALE_PREAMBLE filled from the problem's data.json, then statement_en.md; ./ale (case-gen, "
        "public-eval, status) on a native ale_bench session; final program /workspace/Main.cpp.",
        "session duration (problem.metadata.duration); the session ran for backstop + 3600 s",
        "one session.private_eval (cpp23, judge 202510); performance"),
    "wildclawbench": (
        "The task's '## Prompt' section; files in /tmp_workspace after the '## Warmup' steps; Claude Code "
        "also got --add-dir /tmp_workspace.",
        "front-matter timeout_seconds (600, 900 or 1200)",
        "the task's '## Automated Checks' grade(), with a model judge only where the task needs one; "
        "overall_score"),
    "paperbench": (
        "get_system_message(False, False) and get_instructions(...), both with time-limit text removed; H100 pod.",
        "two system-prompt phrases, the timed 'Total Runtime' line and the use-all-time sentence",
        "paperbench.grade.run_judge after reproduce.sh (4 h on an H100); judge GPT-6 Luna, reasoning high; "
        "weighted rubric score"),
    "yc-bench": (
        "Native SYSTEM_PROMPT and build_initial_user_prompt; run_command, context and finalize MCP tools.",
        "none (max_turns is unlimited natively)",
        "final funds (companies.funds_cents / 100) from the world database"),
    "metr-public-tasks": (
        "TaskFamily.get_instructions and TaskFamily.start; agent user in /home/agent.",
        "none",
        "TaskFamily.score()"),
    "posttrainbench": (
        "Output of POST_TRAIN_BENCH_PROMPT=prompt python src/eval/general/get_prompt.py --agent codex "
        "--model-to-train Qwen/Qwen3-1.7B-Base --benchmark-id <task> --num-hours 10 --num-gpus 1 (with the "
        "task's test_data.json in place, as run_task.sh requires), rendered once for all agents; trailing "
        "newlines stripped, then the timer line removed; no timer.sh; H100 pod.",
        "the 'maximum of N hours ... bash timer.sh' line and task/timer.sh",
        "evaluate.py on final_model over the full test set (not the 150-item default); accuracy"),
}

# Our desktop bridge's `done` tool (OSWorld 2.0, Agents' Last Exam), the tool text of ours
# that refers to the run's time. Claude Code saw these tools as mcp__agenttime_osworld__* and
# mcp__agenttime_agents_last_exam__*.
_OBSERVATION = ("Returns the settled observation: screenshot bytes and sha256, screen size, the native action "
                "that was executed, the step index and any native error text.")
DONE_TOOL_DESCRIPTION = {
    "osworld": "Declare the task finished. The attempt keeps running until its time is up; the grader still "
               "runs on the live desktop. " + _OBSERVATION,
    "agents-last-exam": "Declare the task finished. The attempt keeps running until its time is up; the grader "
                        "still runs on the live sandbox. " + _OBSERVATION,
}

# GPQA zero-shot prompt (format of github.com/idavidrein/gpqa baselines). Choices are
# shuffled as upstream does: one seeded random.Random shuffles each CSV row's four
# choices once, in CSV order; the resulting order was fixed for all agents and requests.
GPQA_PROMPT = (
    "What is the correct answer to this question: {question}\n\nChoices:\n"
    "(A) {c0}\n(B) {c1}\n(C) {c2}\n(D) {c3}\n\n"
    'Format your response as follows: "The correct answer is (insert answer here)"'
)

# SYSTEM_PROMPT of hle_eval/run_model_predictions.py, github.com/centerforaisafety/hle (MIT).
HLE_SYSTEM_PROMPT = (
    "Your response should be in the following format:\n"
    "Explanation: {your explanation for your answer choice}\n"
    "Answer: {your chosen answer}\n"
    "Confidence: {your confidence score between 0% and 100% for your answer}"
)


def load_roster(path: Path = ROSTER_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["benchmarks"]


def find_task(benchmark: str, task_id: str) -> dict:
    for entry in load_roster():
        if entry["benchmark"] == benchmark:
            for task in entry["tasks"]:
                if task["task_id"] == task_id:
                    return task
    raise KeyError(f"{benchmark}/{task_id} is not in the roster")


def requests_ms(task: dict) -> tuple[int, int, int]:
    return tuple(minutes_to_ms(value) for value in task["requests_minutes"])


def cells(agent: str):
    """The 666 runs of one agent: one fresh run per task and requested duration."""
    for entry in load_roster():
        for task in entry["tasks"]:
            shortest, middle, longest = requests = requests_ms(task)
            for condition, ms in zip(CONDITIONS, requests):
                yield {"agent": agent, "benchmark": entry["benchmark"], "task_id": task["task_id"],
                       "request": condition, "request_text": request_text(ms, middle), "request_ms": ms,
                       "cutoff_ms": backstop_ms(longest), "clock_tool": entry["clock_tool"]}
