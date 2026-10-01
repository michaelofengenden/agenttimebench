# Where the decisions came from

The new repository carries forward the selected allocation, task substitutions and reviewed architecture from the preserved AgentTimeExperiment workspace. [provenance.json](provenance.json) records exact source paths, sizes and SHA-256 hashes. It is a source index, not a claim that a later machine can access those local paths.

Claude Opus 5.5 at max effort reviewed the earlier architecture, requested revisions, then approved it for build planning subject to the Stage 1 protocol gates. [historical-review.json](historical-review.json) records the observed review identity, completion receipt and scope without copying account details or native session logs. The Codex planning helper failed to initialize during that review; the primary agent performed that portion directly.

The user's subsequent decisions take precedence over that historical packet:

- Name the new repository **AgentTime v1.1**, replacing the earlier working name v2.
- Organize around **660 nominal runs per agent** for the current 220-task allocation. The agent list may grow.
- Collect uncapped, unprompted natural runtimes first. No M values or short/long durations are fixed now.
- After enough agents, consider comparable natural measurements from both versions when deciding per-task averages. Do not relabel old prompted or censored runs as natural.
- Use the natural attempt itself as the M observation, with no additional calibration or timed M run.
- Target 220 simultaneous task attempts with one evaluated agent configuration at a time.
- Prefer continuing interrupted sessions. Allow at most one fresh replacement only when confirmed external infrastructure failure cannot be continued and the original has stopped.

The scaffold and these later protocol clarifications have not received a fresh Opus review. Their offline validation does not establish native execution readiness. The original evidence, reviews and v1 runtime remain in the original repository.
