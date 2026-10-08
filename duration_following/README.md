# Duration following (Section 4.1, Appendix A)

Fable 5.1 in Claude Code, GPT-5.6 Sol and GPT-6 Astra in Codex each ran the 222 tasks once at each of their three
requested durations in a fresh CLI session; graded outputs were scored by the benchmark's own grader
(`agenttime/benchmarks.py`). The prompt (`agenttime/prompt.py`) is the native task message with its time limits
removed (ALE-Bench: our tool preamble, then the problem statement), two newlines and this sentence, no newline after:

```
Please work on this task for a full {N} {unit}. Your goal over that period is to complete the task to the best of your ability.
```

`{N} {unit}` is the request in one unit per task, chosen from its middle request: seconds under 4 minutes, minutes under
4 hours, hours under 4 days, otherwise days. Claude Code 2.1.259 ran with `--model claude-fable-5-1 --effort max`; Codex
ran with `-c model_reasoning_effort=max`, version 0.151.0 for Sol on agentic tasks and 0.153.4 for Astra (sub-agents
off) and for Sol on GPQA Diamond and HLE, where a clock was the only tool (`agenttime/harness.py`, `clock_mcp.py`,
`harbor_agents.py`). A supervisor that launches the CLI and that the agent cannot modify times each run until the CLI
and every process it started have exited (`agenttime/supervisor.py`; the OSWorld 2.0 and Agents' Last Exam desktop
controller timed the CLI). A task's three runs share a hidden cutoff at twice its longest request (the code's backstop).

```
python -m agenttime plan > runs.tsv           # all 3 x 666 runs with request and cutoff
python -m agenttime prompt --benchmark gpqa-diamond --task ID --request shortest NATIVE.txt   # the prompt of any run
python -m agenttime stage --agent astra --benchmark terminal-bench --task ID --request middle \
    --task-dir NATIVE_TASK_DIR --out run && harbor run --config run/job.json   # Terminal-Bench, TUA-Bench; Harbor 0.22.0, Docker
```

21 of the GPT-5.6 Sol runs (AssistantBench 11, PPTArena 6, WildClawBench 4) were made before the main launch with a
slightly different setup: the sentence read "for the full N" instead of "for a full N", reasoning effort was xhigh
instead of max, and Codex was launched with `codex exec` directly (the runner in `forecasting/`) rather than through
Harbor. 14 Fable 5.1 runs (11 ProgramBench, 3 METR) were completed by Claude Opus 4.8 after a Fable refusal and count as
"Fable 5.1 with fallbacks". Refused (6) and unfinished (1) Fable 5.1 runs are not counted (1,991 runs: 659, 666, 666).

`analyze.py RUNS SCORES [LABELS]` (inputs in its docstring) prints Table 3 (upper block), the Section 4.1 numbers,
the Figure 2 counts and Table 5 with its suite row; `figures.py` with the same arguments draws Figures 1 and 2, plus an
earlier one-panel figure the paper no longer uses (the script's own comments still number them 2, 3 and 1). LABELS:
Claude Opus 5.5 (Astra) and Claude Sonnet 5 (Sol, Fable) labelled condensed transcripts with `LABELING_RUBRIC.md`;
the labels are not public, and without them Figure 2b is skipped. The readers used the rubric's earlier on-time window
(0.8-1.25x); the analysis takes early and late from the clock at 0.95-1.05x, and every run inside 0.95-1.05x was also
inside 0.8-1.25x, so every on-time run has a reader's label.
RUNS and SCORES come from the website's [data release](https://agenttimebench.com/data/):
```
curl -O https://agenttimebench.com/downloads/2026-09-26-1311Z/agenttime-runs.json
python from_release.py agenttime-runs.json OUT/ && python analyze.py OUT/runs.csv OUT/scores.csv
```
