# Harness swap (Appendix B, Table 3 lower block)

Fable 5.1 ran in Codex and GPT-6 Astra in Claude Code, each in the other's usual harness, through OpenRouter at
maximum effort. The 101 cells (`cells.csv`) are 18 GPQA Diamond and HLE questions at 1.25, 5 and 20 minutes (six
picked where Fable in Claude Code had missed a request, twelve drawn at random) and 16 Terminal-Bench, TUA-Bench,
PPTArena and WildClawBench tasks at their main-run requests (one PPTArena task at two); each run is compared with the
main run of the same cell. As a provider check, 35 Fable question runs in Claude Code and 20 Astra question runs in
Codex were repeated through OpenRouter, changing only the endpoint.

The prompt is the main runs' prompt, byte for byte (`prompt_sha256` in `cells.csv`; HLE also gets HLE's own system
prompt, `agenttime.benchmarks.HLE_SYSTEM_PROMPT`):

```
<task text from the benchmark>

Please work on this task for a full <N> minutes. Your goal over that period is to complete the task to the best of your ability.
```

Questions (`python -m harness_swap.run_question_cell` from `duration_following/`; arguments in its docstring):
- Codex CLI 0.153.4: `codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --model
  anthropic/claude-fable-5.1 --json --enable unified_exec -c model_reasoning_effort=max --strict-config --ignore-rules`,
  configured like the main runs' GPT-5.6 Sol GPQA/HLE route (`codex-config.toml`; the clock is the only tool).
  `openrouter_proxy.py` adds a prompt-cache hint for Fable.
- Claude Code 2.1.259: the main runs' closed-book clock flags with `--model openai/gpt-6-astra --effort max`, the
  OpenRouter endpoint, and the prompt piped from a file.
- Runtime is a monotonic clock on the host around `docker exec`. At max(2 x request, 30 min) the CLI gets SIGTERM and
  the container is killed 15 s later.

Agentic cells used the main runner, its timer and its cutoffs, with the model and endpoint changed (`agentic.md`).

`python analyze.py RUNS_CSV` prints the LaTeX bodies of Table 3's lower block, `tab:harness-ablation`,
`tab:harness-swap-cells` and `tab:harness-swap-agentic` and the Appendix B numbers that come from runtimes, scores and
token counts; `python figures.py RUNS_CSV --out DIR` writes `harness_swap.pdf` (`fig:harness-swap`) and
`harness_swap_cells.pdf` (`fig:harness-swap-all-cells`). RUNS_CSV has one row per run, ours and the main runs of the
same cells; its columns are in `analyze.py`.
