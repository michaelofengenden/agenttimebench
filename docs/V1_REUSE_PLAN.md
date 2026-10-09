# What to carry from v1 into AgentTime v1.1

1 October 2026. Recommendations from the current `TimeResearch/agenttimebench` refactor. This is an audit of a work in progress, not an implementation or release approval. The source tree was left untouched. File hashes are recorded in the [source inventory](reviews/2026-10-01-v1-refactor-sources.json); recheck them before any port.

**Keep the useful components and regression cases, while retaining v1.1's new experiment and attempt model.** The refactor is valuable as a reproducible record of v1. Its historical behavior should not become the default behavior of the new runner merely because it is now in a smaller module.

## Take, adapt, or replace

| Area in v1 | What we should take | What v1.1 should change |
| --- | --- | --- |
| Prompt compiler | Exact text preservation, exact numeric formatting, unique-anchor checks and rejection of ambiguous edits. | Natural runs append no duration request. Record original, adapted and delivered bytes and hashes. Review native-limit changes per task; retain task-defining endpoints. Do not reuse blanket removal rules or the old desktop promise to keep running until time expires. |
| Process supervisor | Ownership by process identity, Linux descendant tracking, orphan tests and controlled cleanup. | Record prompt release, native terminal event, actual CLI exit when observed, and owned-work drain separately. The old aggregate includes drain and forced cleanup. Keep those costs out of natural M and preserve their separate evidence. |
| Harbor and native adapters | Command-drift detection, explicit model/settings checks, duplicate-invocation rejection, fallback disabling and native event fixtures. | Bind checks to each new pinned agent configuration. Use Harbor's supported lifecycle where it meets the contract, adding only the missing measurement and preservation hooks. Qualify the actual route; do not reuse old CLI constants or infer identity from a model label. |
| Benchmark preparation and grading | Task-specific staging knowledge, native grader integration and verifier regression cases. | Reacquire pinned sources and images, apply explicit patches, validate completion/capture contracts and re-audit selected verifiers. A historical passing score is not evidence that a new source revision or task selection is sound. |
| Scheduling and recovery | Small isolated jobs and zero automatic Harbor retries. | Keep the new durable ledger and global one-agent admission. Natural first, timed requests later. Reattach or qualify same-session continuation; allow one fresh infrastructure replacement only under the agreed stop-proof rules. Missing result files must never cause automatic model replay. |
| Native sessions and forks | Native session discovery, isolated store placement, parent-link fixtures and supported native fork calls. | Preserve original native bytes, required workspace/environment and subagent histories on the Mac. Verify new child identities and parent/sibling immutability. The old retrospective runners alter histories and discard copied stores, so they cannot be the archive implementation. |
| Analysis and figures | Small pure metric functions, explicit formulas, joined run/score records and historical regression fixtures. | Read validated, versioned exports. Use a configurable agent registry; retain missing, refused, censored and invalid outcomes with denominators. Declare score direction and normalization in task metadata. Make task ordering and resampling reproducible. |
| Older natural observations | The forecasting module identifies potentially useful no-request runs. | Audit each observation's task, prompt, resources, endpoint and censoring before calibration. Its routes and task set differ from v1.1. Old duration-following runs cannot become natural M by deleting their prompt text afterward. |
| Additional experiments | Forecasting, harness swaps, referent ablations, time-control probes and retrospective interventions remain useful research modules. | Forecasting is now selected in the [forecast plan](superpowers/specs/2026-10-01-forecast-design.md), using separate requests and the existing natural measurements. The other experiments remain optional. All have explicit accounting and must preserve original sessions. |

## Three concrete lessons for the new build

### Completion and background work must agree with grading

Suppose an agent says it is done after ten minutes but a training process continues for another half hour. We should retain both observations. Under the current v1.1 protocol, natural M ends at the native completion event or the task's declared endpoint, and the scored state must be captured at that boundary. A later model checkpoint cannot silently improve the ten-minute result.

The old supervisor's `root_end_monotonic_ns` is the observed exit of a shell pipeline, including logging, rather than a direct CLI-exit timestamp. Its `elapsed_ms` ends after owned processes drain and may include forced cleanup. Native final events are checked after that drain. Port the ownership machinery with new event observations, not the old aggregate as M.

This does not mean stopping useful background work while an agent is still actively waiting for it. Such waits remain inside the attempt. If a benchmark requires completion after native agent return, that endpoint needs an explicit adapter contract and review before qualification. If the terminal submission cannot be captured reliably, the task cannot claim a valid terminal-bound score.

### A continuation is a recorded segment of the original attempt

The screenshot's resumed METR example is a reason to retain every segment, interruption gap, native session identity and added message. It is not sufficient to store one total duration. The run-level claim in the screenshot was not independently reconstructed during this source audit.

For v1.1, keep the agreed qualified-continuation rule: preserve clock continuity and the interruption interval, prove the prior execution is fenced/stopped as required, and do not add elapsed-time or remaining-time hints. If continuity or eligibility cannot be established, leave the observation explicitly unresolved or ineligible rather than manufacturing a natural finish. A fresh replacement has a new attempt ID and does not overwrite the interrupted attempt.

### A research fork is different from a preserved original

The retrospective code intentionally removes timing requests and hidden native reasoning, changes message structure, and sometimes reconstructs or truncates a replay. That can implement a named experimental treatment. It cannot preserve an original session for future native restoration.

Use the [session-preservation design](superpowers/specs/2026-10-01-session-preservation-design.md): exact native exports plus required task state, verified Mac receipts, separate scientifically sealed submissions and later working-state checkpoints, and a new identity for every exploratory fork. Archive-transfer failures retry transfers, never the subject. Retrospective transformations happen only on explicitly labelled derivatives.

## Keep historical reproduction separate from new analysis

The v1 duration-following analysis fixes three historical agents and three requested-duration arms. v1.1 has an expanding agent registry and an unprompted natural arm. Natural M has no requested duration, so it cannot be inserted as a third timed point in the old request-versus-runtime ratios or slopes.

Keep the historical formulas and input order when reproducing v1. For v1.1, version the cohort rules, metric definitions and score mappings. The current bootstrap samples tasks in first-appearance order: a fixed random seed alone does not make its intervals invariant to reordered input. Canonical task and row ordering, recorded resampling settings, and a row-permutation regression test should be part of the new analysis. Declare family/task weighting and eligible historical observations before computing timed requests; those choices remain pending user review.

The native score code also fixes an all-three-agent intersection and infers ALE score direction from observed correlations. Replace those assumptions with declared comparison cohorts and task score contracts. Preserve raw grades and report why rows are absent from any aggregate. A zero score and a missing grade remain distinct.

No v1 paper or published numbers were edited. This audit did not rerun its reproduction tests or confirm the screenshot's overall test claim. The referent-ablation README itself documents differences from the published joins. Judge the refactor by reproducibility and explicit behavior, not by total line count.

## Small implementation sequence

1. **Port the contracts and tests first.** Pin an audited snapshot. Adapt prompt-byte, model/command identity, orphan-process and terminal-event fixtures to v1.1. Add cases for an uncapped natural prompt and for terminal completion before background shutdown.
2. **Connect one Harbor trial to the durable worker.** Preserve the existing ledger and measurement modules. Prove one subject invocation, terminal-bound capture, detached-process ownership and restart without replay. Native Linux descendant behavior needs a Linux qualification; a Mac fixture alone does not prove it.
3. **Implement the archive before production cleanup.** Follow the existing archive slices: local streaming store, complete state inventory, restore-only reconstruction, isolated child forks, then lifecycle/transfer integration. Prove restoration using only the archive after deleting the disposable source workspace.
4. **Build analysis from sealed exports.** Add the agent registry, explicit cohorts, score contracts and deterministic resampling. Qualify each real agent/task route before scaling. The selected forecast requests precede natural execution and remain outside the 636 nominal task-execution slots per agent; other optional experiments come afterward.

The new ledger, timing validator and model-free terminal-bound artifact tests already exist in v1.1. The separate Harbor Docker probe also exists. This audit imports no runtime code and does not qualify the combined worker, real agents, restoration or concurrency target. It identifies what should feed those next steps.

## Source map

Paths below are relative to the refactor root recorded in the source inventory. Line numbers refer to the inspected snapshot and can move during the ongoing refactor.

- Prompt integrity: `duration_following/agenttime/prompt.py:69,84,118`; regression cases in `tests/test_agenttime.py:87,177`.
- Process ownership and clock boundary: `duration_following/agenttime/supervisor.py:60,121,154,189,215`; process and terminal tests in `tests/test_agenttime.py:205,217,240,268`.
- Historical planner and model-visible timing assumptions: `duration_following/agenttime/benchmarks.py:116,154`, `prompt.py:169`, `__main__.py:36,88`.
- Native adapter checks: `duration_following/agenttime/harbor_agents.py:96,143,233`, `harness.py:57`, `supervisor.py:95`.
- Native fork placement and identity: `retrospective_forks/fork_claude.py:77,188,202,306`, `fork_codex.py:67,224,254`.
- History transformations and missing workspace restore: `retrospective_forks/fork_claude.py:48,113,302`, `fork_codex.py:61,116,190,332`, `reconstruct.py:14`, `replay.py:49`, `scrub.py:2502`.
- Retry and overwritten logical records: `retrospective_forks/run_all.py:55`, `common.py:84`.
- Analysis assumptions: `duration_following/metrics.py:15,25,65`, `analyze.py:28,30,103,170,198`; metrics tests in `tests/test_duration_following.py:38,43,48,56`.
- Earlier no-request cohort: `forecasting/README.md:24`; separate historical join differences: `preliminary/referent_ablation/README.md:37`.

Two scoped independent source audits covered the runner and retrospective modules. They ran no benchmark or provider calls and no v1 refactor test suite. The retrospective audit also observed test/API drift while that module was still being edited; do not treat that moving snapshot as a verified library. The independent [v1.1 plan review](reviews/2026-10-01-v1-reuse-review.md) found no material issues and approved these recommendations; it did not qualify runtime behavior.
