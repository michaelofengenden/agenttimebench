# Referent ablation (Appendix E, "Whose Prior Is It?")

Fable 5 and GPT-5.6 Sol estimated how long a task would take "you", "a frontier AI agent" or "a skilled human
professional", in fresh single-turn sessions. Controls: "finish" for "complete"; "a human expert" for "a skilled human
professional". Tasks: 232 from 18 benchmarks (`tasks.csv`). Prompt bytes: `prompts.json`.

```text
How long will it take {you | a skilled human professional | a frontier AI agent} to complete this task? Reply with two lines: MINUTES=<positive number> (decimals are allowed), then REASON=<one short sentence explaining your estimate>.

The task:
"""
<task instruction>
"""
```

**Runs** (effort `xhigh`). Fable 5 (`claude-fable-5`): Claude Agent SDK `query()` with `maxTurns: 1`, file, shell and
web tools disallowed, no setting sources, hooks or skills, in an empty temporary directory (`elicit_claude.mjs`).
GPT-5.6 Sol: Codex CLI 0.146.0, `codex exec --ephemeral --skip-git-repo-check -s read-only --ignore-user-config -C
<empty dir> -c model=gpt-5.6-sol -c model_reasoning_effort=xhigh -c personality=none -` (`elicit_codex.py`).
`parse_estimate.mjs` reads one `MINUTES=` line (Claude Haiku 4.5 as fallback). Fable's refusals count as missing.

**Design** (`design.py`, seed 0; reps encode the batch): you/human r1-2 on all tasks, frontier/placebo r1-2 on a 50-task
subset (2,256 sessions); frontier r1-2, you r3-4 on the other 182 (1,456); expert r1-2, human r5-6 on the 50 (400).

**Inputs.** `<safe_id>.txt` (not redistributed): task text staged as in `../../forecasting/` (AutomationBench:
`automationbench_header.txt`), time budgets removed by `strip_anchors.py`. `actuals.csv`: one completed, answered,
uncapped forecasting-study run per task and model.

```bash
npm install @anthropic-ai/claude-agent-sdk @anthropic-ai/sdk
python run.py main --instructions texts/ --out receipts/   # then ext and expert
node code_reasons.mjs receipts/ codes.jsonl                # REASON cues, claude-haiku-4-5, blind to arm and model
python analyze.py receipts/ codes.jsonl actuals.csv        # the appendix numbers
python figures.py receipts/ codes.jsonl actuals.csv        # the four figures, written to figures/
```
