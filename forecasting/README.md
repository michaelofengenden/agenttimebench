# Forecasting natural runtimes (§4.2, earlier version)

This is the code for the earlier version of the §4.2 experiment: Fable 5 in Claude Code and GPT-5.6 Sol in Codex, on
235 tasks from 18 benchmarks (`tasks.csv`). Each agent forecast how long a task would take it and, separately, ran the
task once without a requested duration. It does not reproduce Figure 3 or Appendix C; the 222-task runs will be added.
In the forecast prompt, `{task}` is the staged instruction file (`task.txt` below; staging: `prompts/task_headers.txt`):

```
How long will completing this task take you? Reply with MINUTES=<positive integer>.

The task:
"""
{task}
"""
```

**Forecasts.** One fresh single-turn session per task at effort `xhigh`: Fable 5 through the Claude Agent SDK with file,
shell and web tools disallowed (`forecast_fable.mjs`), Sol through `codex exec --ephemeral -s read-only`, which still
allows a read-only shell (`forecast_sol.sh`); `parse_estimate.mjs` parses replies, with Claude Haiku 4.5 as fallback.

**No-request runs.** One fresh run per task with the task text and no requested duration or time cap, at effort
`xhigh`: Fable 5 via the Claude Agent SDK (`run_fable.mjs`, Claude Code 2.1.205), Sol via `codex exec` 0.145.0 with
`codex_sol.toml` (`run_sol.sh`), and Sol on TUA-Bench and Frontier-Bench through Harbor on the benchmark's own
`instruction.md` (`run_harbor_sol.sh` removes Frontier-Bench's "You have N seconds to complete this task."). Runners
for OSWorld 2.0, METR and DeepSWE are not included. Direct runs before 27 July 2026 wrapped the task text in
`prompts/legacy_wrapper_{fable,sol}.txt` (`--legacy-wrapper`). Runtime is the runner's wall clock from launching the
agent CLI until the agent stops and any containers it started have exited; on Harbor, the agent-execution interval.

```bash
ANTHROPIC_API_KEY=... node forecast_fable.mjs task.txt <source> <task_id> forecasts/   # npm i @anthropic-ai/claude-agent-sdk @anthropic-ai/sdk
ANTHROPIC_API_KEY=... bash forecast_sol.sh task.txt <source> <task_id> forecasts/      # key for the Haiku fallback
node run_fable.mjs [--legacy-wrapper] task.txt <claude_config_dir> <source> <task_id>
bash run_sol.sh [--legacy-wrapper] task.txt <source> <task_id>
bash run_harbor_sol.sh <harbor_task_dir>...                                           # edits instruction.md in place
```
