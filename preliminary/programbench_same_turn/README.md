# Same-turn forecasts on ProgramBench (Appendix E)

Claude Code with `claude-opus-4-8` and Codex with `gpt-5.5` each ran the 200 ProgramBench tasks once, forecasting,
working and self-reporting in one session. `prompts/system.md` is the system prompt (Codex: first on stdin); the task:

```text
ProgramBench task: {{ID}}. Follow the rules in your system prompt exactly.

Your working area is the folder  tasks/{{ID}}/workspace/  — work ONLY there.
The original binary is NOT in your folder. Interact with it ONLY by running:
    ./pb probe {{ID}} <args...>            (pipe stdin if needed, e.g.  echo '{"a":1}' | ./pb probe {{ID}} -m )
Do NOT read, list, or open anything under ../programbench-tests (that is the answer key — off limits),
and do not fetch anything from the network.

Do this, in order:
1. Your VERY FIRST line of output must be exactly:  ESTIMATE_MINUTES=<integer>
   (your honest guess of how many wall-clock minutes this will take you; never consult a clock).
2. Read every file in tasks/{{ID}}/workspace/. Probe the binary thoroughly — flags, stdin,
   edge cases — until you understand its behavior.
3. Write an original, from-scratch implementation in tasks/{{ID}}/workspace/, plus an executable
   ./compile.sh that builds ./executable. Put build artifacts (including ./executable) in .gitignore.
4. Verify it builds:  ./pb build {{ID}}
5. Before you stop, print these two lines (estimate from your own sense of effort — never check a clock):
   PERCEIVED_MINUTES=<integer>
   SELF_SCORE=<0-100>
Then stop. Stay inside tasks/{{ID}}/workspace/ the entire time.
```

`./run.sh <claude|codex> <instance_id>` has the exact commands: Claude Code 2.1.191 with `--effort max` and Codex
with `model_reasoning_effort=xhigh`, each in a new CLI session in its own arena (`pb` runs the original binary
offline; Claude's arena also held the files in `claude_arena/`). Runtime is the wall time from just before the CLI
starts to its exit. Claude runs that printed no `PERCEIVED_MINUTES` gave it afterwards from a time-redacted transcript
(`extract.py fill-input`). After `programbench eval runs/<agent>_run` (programbench 1.2.2) and `extract.py table`,
`python analyze.py records.csv` prints the same-turn numbers and `python figures.py records.csv` draws Figures
`fig:programbench-same-turn-forecast`, `fig:same-turn-self-score` and `fig:runtime-correlation-baselines`.
