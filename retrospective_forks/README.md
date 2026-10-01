# Retrospective forks (Section 4.3, Figure 5, Appendix D)

After a run has finished, the agent is asked how long it took. The 69 parents are duration-following runs by Fable 5.1,
GPT-5.6 Sol and GPT-6 Astra on 23 tasks (10 CORE-Bench, 10 GPQA Diamond, 3 TUA-Bench), one requested duration per task
(`select_parents.py`), with that duration and its restatements removed (`request_removal.py`). Two answers per parent
and condition (690); an answer's last `minutes = <number>` counts if above 0 and is compared with the parent's recorded
runtime. The question is a new user turn; R-scrubbed replies without a number get one re-ask:

```
How long did it take you to complete this task?
return minutes = <Number of minutes>
```
```
Tools are not available in this conversation, and an exact measurement is not needed. Please give your best estimate.
return minutes = <Number of minutes>
```

Conditions (ids in `answers.csv`):
- R-oracle (`oracle`), R-native (`native`) and R-context-only (`context-only`): session forks (`fork_claude.py`,
  `fork_codex.py`) with ordinary tools plus `elapsed_seconds` (returns the runtime), ordinary tools, and no tools.
- R-replay (`replay`): transcript rebuilt from the session log, one API call, no tools (`replay.py`, `reconstruct.py`).
- R-scrubbed (`scrubbed`): the R-replay transcript with every time cue removed (`replay.py`, `scrub.py`, `press.py`).

Forks resume a copy of the parent session without its hidden reasoning in the `agenttime-retro:1` image (`docker/`):
Claude Code 2.1.280 (`claude -p QUESTION --resume ID --fork-session`; `--tools ""` for R-context-only) or Codex 0.156.1
(`codex exec resume`; `codex_proxy.py` drops all tools for R-context-only), with the parent's model and effort (max).
Calls go through OpenRouter with the provider pinned (`{"order": [P], "allow_fallbacks": false}`, P = the model's lab);
replays add `"reasoning": {"effort": "max"}`, `"max_tokens": 32768`, `"transforms": []`, `"require_parameters": true`.

```sh
docker build -t agenttime-retro:1 docker/
OPENROUTER_API_KEY=... python run_all.py --parents parents.csv --sessions DIR   # DIR/<run_id>/*.jsonl, not included
python analyze.py parents.csv results/answers.csv           # Figure 5 numbers; input columns in its docstring
python figures.py parents.csv results/answers.csv figures   # Figure 5: figures/retrospective_forks.pdf
```
