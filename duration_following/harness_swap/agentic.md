# Agentic cells: the main runner with the model and endpoint changed

The 16 agentic tasks (`block=agentic` in `cells.csv`) ran through the main duration-following runner
(`../agenttime/`, Harbor 0.22.0) on the main runs' task environments and prompts (`prompt_sha256`), with the same
timing supervisor, cutoff (2 x the task's longest request, `backstop_s`), `reasoning_effort: max`, transcript checks
and native grader. Runs that ended on their own and then failed a check keep their time (`failed_check`). Two checks
assumed the original model's transcript format and were relaxed: a WildClawBench skill call with an empty argument
string counts as a skill call, and a PPTArena deck is captured from the run's own directory.

**Fable 5.1 in Codex.** Terminal-Bench, TUA-Bench and PPTArena: the GPT-5.6 Sol Codex route (Codex 0.151.0) with the
model replaced and the three sub-agent flags added. The command, as seen in a run's process list:

```
if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --model anthropic/claude-fable-5.1 --json --enable unified_exec -c model_reasoning_effort=max -c agents.enabled=false -c features.multi_agent_v2=false -c features.multi_agent=false -- '<prompt>'
```

WildClawBench: the GPT-6 Astra Codex route for that benchmark (Codex 0.153.4) with only
`--model anthropic/claude-fable-5.1`. Each route's `config.toml` gained `model_provider = "openrouter"` and the
`[model_providers.openrouter]` block of `codex-config.toml` with port 18790 and `requires_openai_auth = false`
(provider id `openrouter_cached` on WildClawBench); the key reaches the container only as `OPENROUTER_API_KEY`. Port
18790 is a forwarding proxy inside the task container, a Node.js twin of `openrouter_proxy.py` that behaves like it does
with `PROXY_CLIENT=codex PROXY_CACHE_HINT=1`, started outside the timed process tree.

**GPT-6 Astra in Claude Code.** All four benchmarks: the Fable 5.1 Claude Code route (Claude Code 2.1.259, same command
and flags) with `--model openai/gpt-6-astra` and, in place of the Anthropic credentials,
`ANTHROPIC_BASE_URL=https://openrouter.ai/api`, `ANTHROPIC_AUTH_TOKEN=<OpenRouter key>`, an empty `ANTHROPIC_API_KEY`
and `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` and `CLAUDE_CODE_SUBAGENT_MODEL` set to
`openai/gpt-6-astra`.
