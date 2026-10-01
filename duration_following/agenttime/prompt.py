"""The duration request and how it was added to each task's message.

The request sentence, how its number and unit were written, how it was appended to
the benchmark's own task text, Harbor 0.22.0's instruction loading, the ALE-Bench
preamble, and the native time limits removed first. Our other text that the agent
saw: the clock tools (clock_mcp.py for Claude Code, harness.py for Codex) and the
desktop `done` tool (benchmarks.DONE_TOOL_DESCRIPTION).
"""
from __future__ import annotations

from fractions import Fraction
import re

TEMPLATE = "Please work on this task for a full {N} {unit}. Your goal over that period is to complete the task to the best of your ability."
_PREFIX = "Please work on this task for"
_TAIL = "Your goal over that period is to complete the task to the best of your ability."

UNIT_MS = {"seconds": 1_000, "minutes": 60_000, "hours": 3_600_000, "days": 86_400_000}
CONDITIONS = ("shortest", "middle", "longest")  # the three requests of each task


def minutes_to_ms(minutes: int | float | str) -> int:
    """Exact milliseconds for a roster value such as 1.25."""
    ms = Fraction(str(minutes)) * 60_000
    if ms <= 0 or ms.denominator != 1:
        raise ValueError(f"not a positive whole number of milliseconds: {minutes!r}")
    return int(ms)


def backstop_ms(longest_ms: int) -> int:
    """Hidden cutoff, shared by a task's three runs and never shown: twice its longest request."""
    return 2 * longest_ms


def display_unit(middle_ms: int) -> str:
    """One unit per task, chosen from its middle request."""
    if middle_ms < 4 * 60_000:
        return "seconds"
    if middle_ms < 4 * 3_600_000:
        return "minutes"
    if middle_ms < 4 * 86_400_000:
        return "hours"
    return "days"


def decimal_text(ms: int, unit: str) -> str:
    """Exact terminating decimal of `ms` in `unit`, without trailing zeros ("1.25", "5")."""
    value = Fraction(ms, UNIT_MS[unit])
    for places in range(11):  # whole milliseconds need at most 10 places, in days
        scaled = value * 10**places
        if scaled.denominator == 1:
            digits = str(scaled.numerator).rjust(places + 1, "0")
            return f"{digits[:-places]}.{digits[-places:]}" if places else digits
    raise ValueError(f"{ms} ms has no exact decimal form in {unit}")


def request_text(ms: int, middle_ms: int) -> str:
    """The "{N} {unit}" of the sentence: "1.25 minutes", "1 minute", "30 hours"."""
    unit = display_unit(middle_ms)
    number = decimal_text(ms, unit)
    return f"{number} {unit[:-1] if number == '1' else unit}"


def sentence(ms: int, middle_ms: int) -> str:
    number, unit = request_text(ms, middle_ms).split(" ")
    return TEMPLATE.replace("{N}", number).replace("{unit}", unit)


def compile_prompt(native: bytes, ms: int, middle_ms: int) -> bytes:
    """Native task bytes, unchanged, then two newlines and the request sentence.

    No newline follows the sentence. Refuses an empty task message or one that
    already contains the sentence's opening or closing words.
    """
    text = native.decode("utf-8")
    if not text or _PREFIX in text or _TAIL in text:
        raise ValueError("empty task message, or it already contains the duration request")
    return native + b"\n\n" + sentence(ms, middle_ms).encode("utf-8")


_CANARY = re.compile(r"^(<!--.*canary.*-->|#.*canary.*)$", re.IGNORECASE)


def load_harbor_instruction(raw: bytes) -> bytes:
    """Harbor 0.22.0 reading of instruction.md: universal newlines, then leading
    canary lines and the blank lines after them are dropped."""
    lines = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    index = 0
    while index < len(lines) and _CANARY.match(lines[index].strip()):
        index += 1
    while index < len(lines) and not lines[index].strip():
        index += 1
    return "\n".join(lines[index:]).encode("utf-8")


# Native time limits in model-visible text, removed byte for byte before the
# request was appended. Each anchor must occur exactly once.
TERMINAL_BENCH_CAP = b"You have 28800 seconds to complete this task."
PAPERBENCH_SYSTEM_CAPS = (
    b"Use as much of the available time as possible to refine and optimize your solution: ",
    b" Keep working until the time limit expires.",
)
PAPERBENCH_USE_ALL_TIME = (
    b"You must use all the time available to you, unless you've reproduced all the core contributions "
    b"of the paper. "
)
PAPERBENCH_RUNTIME_LINE = re.compile(rb"^- \*\*Total Runtime\*\*: .+\n", re.MULTILINE)
PAPERBENCH_NO_TIME_LINE = (
    b"- **Total Runtime**: You are expected to work until you have reproduced all the core contributions "
    b"of the paper.\n"
)
POSTTRAIN_TIMER_LINE = re.compile(
    rb'^2\. You have a maximum of [^\r\n]+ hours\. You can query how much time you have left by calling '
    rb'\\`bash timer\.sh\\` in the current directory\.\r?\n', re.MULTILINE)
ALE_SUMO_CAPS = (b"**8 hours of agent wall-time budget** on ", b"Budget ~8 hours of ")


def remove_spans(data: bytes, anchors: tuple[bytes, ...]) -> bytes:
    """Remove byte anchors that each occur exactly once and do not overlap."""
    spans = []
    for anchor in anchors:
        if not anchor or data.count(anchor) != 1:
            raise ValueError("time-limit text not found exactly once")
        start = data.index(anchor)
        spans.append((start, start + len(anchor)))
    spans.sort()
    if any(left_end > right_start for (_, left_end), (right_start, _) in zip(spans, spans[1:])):
        raise ValueError("time-limit spans overlap")
    for start, end in reversed(spans):
        data = data[:start] + data[end:]
    return data


def _one_line(pattern: re.Pattern, data: bytes) -> bytes:
    lines = pattern.findall(data)
    if len(lines) != 1:
        raise ValueError("time-limit line not found exactly once")
    return lines[0]


def remove_terminal_bench_cap(instruction: bytes) -> bytes:
    return remove_spans(instruction, (TERMINAL_BENCH_CAP,))


# Ran on PaperBench's system message (message 0), which delivered_task_text does not cover.
def remove_paperbench_system_caps(system: bytes) -> bytes:
    return remove_spans(system, PAPERBENCH_SYSTEM_CAPS)


def remove_paperbench_user_caps(user: bytes) -> bytes:
    """Drop the timed "Total Runtime" line (the untimed variant stays) and the use-all-time sentence."""
    line = _one_line(PAPERBENCH_RUNTIME_LINE, user)
    if b"You are expected to work until you have spent " in line:
        return remove_spans(user, (line, PAPERBENCH_USE_ALL_TIME))
    if line == PAPERBENCH_NO_TIME_LINE:
        return remove_spans(user, (PAPERBENCH_USE_ALL_TIME,))
    raise ValueError("unexpected PaperBench runtime line")


def remove_posttrain_timer(prompt: bytes) -> bytes:
    """Drop the "maximum of N hours ... bash timer.sh" rule; task/timer.sh was not provided."""
    return remove_spans(prompt, (_one_line(POSTTRAIN_TIMER_LINE, prompt),))


# Ran on a task resource file, not on the delivered task message.
def remove_ale_sumo_caps(task_prompt: bytes) -> bytes:
    """input/task_prompt.md of the Agents' Last Exam task engineering/sumo_urban_am_peak_calibration."""
    return remove_spans(task_prompt, ALE_SUMO_CAPS)


# Our own text, placed before the ALE-Bench problem statement (statement_en.md).
# `./ale status` reports the native session's remaining time; the session ran for
# the backstop plus 3600 s so that the private evaluation fits inside it.
ALE_PREAMBLE = """# Environment

You are solving the AtCoder Heuristic Contest problem `{problem_id}` ({title}) as a
contestant. Your working directory is `/workspace`. Your final program must be a
single C++23 source file at `/workspace/{submission_file}` (compiled with `g++`
in the official `202510` judge environment). Only that file is graded, once,
against the hidden private test cases when your session ends; there is no
separate submit step, so keep `/workspace/{submission_file}` holding your best
program at all times.

Time limit per test case: {time_limit_ms} ms. Memory limit: {memory_limit_mib} MiB.
The judge counts {num_public_cases} public test cases; private test cases are
hidden and are never available to you.

The official contest tools are exposed through `./ale` in `/workspace`:

- `./ale case-gen SEED [SEED ...] [--out DIR]` generates official-style inputs
  for the given seeds (default output directory `in/`, files `0000.txt`, ...).
- `./ale public-eval [FILE]` compiles and runs a source file (default
  `{submission_file}`) on every public test case in the official judge and prints
  the judge verdicts, absolute scores, execution times and memory usage.
- `./ale status` shows the native resource counters and the remaining time.
- Add `--json` before a subcommand for machine-readable output.

The tools run in a separate judge container; nothing else about the grading
environment is reachable from here. Local compilers and interpreters in this
container may be used for your own testing.

"""


def ale_preamble(problem: dict) -> bytes:
    """ALE_PREAMBLE filled from the problem's data.json in its ALE-Bench archive."""
    metadata, constraints = problem["metadata"], problem["constraints"]
    return ALE_PREAMBLE.format(
        problem_id=metadata["problem_id"], title=str(metadata.get("title", metadata["problem_id"])),
        submission_file="Main.cpp", time_limit_ms=int(round(float(constraints["time_limit"]) * 1000)),
        memory_limit_mib=int(constraints["memory_limit"]) // (1024 * 1024),
        num_public_cases=len(problem["seeds"]["public"])).encode()


def delivered_task_text(benchmark: str, raw: bytes, *, ale_problem: dict | None = None) -> bytes:
    """The task-bearing message as delivered, before the request sentence is appended.

    For ALE-Bench, raw is statement_en.md and ale_problem the problem's data.json.
    """
    if benchmark == "terminal-bench":
        return load_harbor_instruction(remove_terminal_bench_cap(raw))
    if benchmark in ("tua-bench", "deepswe"):
        return load_harbor_instruction(raw)
    if benchmark == "paperbench":
        return remove_paperbench_user_caps(raw)
    if benchmark == "posttrainbench":
        # run_task.sh reads the rendered prompt through $(...), which drops trailing newlines.
        return remove_posttrain_timer(raw.rstrip(b"\n"))
    if benchmark == "sakana-ale-bench":
        if ale_problem is None:
            raise ValueError("ALE-Bench needs the problem's data.json")
        return ale_preamble(ale_problem) + raw
    return raw
