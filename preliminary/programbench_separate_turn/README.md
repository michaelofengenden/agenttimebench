# Separate-turn forecasts on ProgramBench (Appendix E)

Opus 5 in Claude Code and GPT-5.6 Sol in Codex on 20 ProgramBench tasks (`tasks.json`); forecast, execution and
retrospection were separate sessions at effort `xhigh` (`invocations.py`; claude-code 2.1.220, codex-cli 0.146.0).

1. Forecast (`execute.py forecast`): before any execution, 3 fresh sessions per task, model and surface (P1 `text`,
   P2 `docs`, P3 `probe`), each in a read-only directory with `TASK.md` (+ `docs/`) (+ a probe-only `./pb`), tools on.
   The question for `probe` is below; `docs` drops its last evidence line and `text` drops both (`questions.py`):
   ```text
   How long will it take you to complete this task:
   "<task statement: a header with the task id, then PROMPT.md from `## SYSTEM` on>"

   The documentation is in ./docs
   You can observe the original binary with ./pb probe <args>
   return minutes = <Number of minutes>
   ```
2. Execution (`execute.py execution`): one run per task and model with all of `PROMPT.md` (ProgramBench's agent prompt
   from mini-swe-agent `src/minisweagent/config/benchmarks/programbench.yaml`) in an arena of only `./pb`, `PROMPT.md`
   and the workspace. Runtime is `time.monotonic()` from just before CLI launch to exit; cutoff 63,477.564 s.
3. Retrospection (`fork.py`): each finished session was forked twice in five arms, `native`, `context-only` (tools
   off), `elapsed-oracle` (an MCP tool returning the parent's measured seconds, `oracle_tool.py`), `replay` (session
   rebuilt and sent to the provider API) and `scrubbed` (replay with temporal cues redacted, `scrubber.py`), each
   asked two turns in the same conversation:
   ```text
   How long did it take you to complete this task?
   return minutes = <Number of minutes>
   ```
   ```text
   How well did you do?
   return self_score = <Number 0-100>
   ```

Scores: `programbench eval <run_dir>`, then `programbench info` (tests passed / tests counted).
`python analyze.py DATA_DIR` (inputs in its docstring) prints the separate-turn numbers; `python figures.py DATA_DIR`
draws `fig:programbench-separate-turn-forecast`, `fig:programbench-evidence-level` and `fig:separate-turn-self-score`.
