# Forecast before the natural run

1 October 2026. Requested forecast plan for AgentTime v1.1. The flow and isolation requirements below are recorded for implementation; no forecast adapter or live calls have been added. The independent [plan review](../../reviews/2026-10-01-forecast-plan-review.md) found no material issues.

## The experiment

For each selected agent configuration and task, obtain one forecast before executing that task naturally. The forecast is a fresh, text-only API request to the same underlying model. The natural execution then starts as a separate, fresh agent session. Reuse its measured M as the actual runtime for the forecast comparison; do not buy another task execution for this experiment.

The current paper's main prospective experiment uses one API message without a harness or tools, at maximum reasoning effort. Its earlier 235-task forecasting experiment used native harness sessions and a different prompt. Follow the current main-paper setup:

```text
How long will it take you to complete this task:
"{task}"
return minutes = <Number of minutes>
```

`{task}` is the exact resolved task instruction text that the natural run will receive, with no duration-following request. Resolve task revision, seed and any generated scenario before forecasting, then bind both records to that version and instruction hash. Do not substitute a task title. Keep the wrapper bytes versioned, including newline behavior. Text-only forecasts do not see task assets absent from the instruction; record that evidence limitation and duplicate instruction hashes rather than claiming full task inspection.

The paper used OpenRouter with a pinned provider and no fallback. Use that as the route candidate, subject to each new model's availability and qualification. Record requested and observed model identity, provider route, reasoning settings and request limits. Keep the intended maximum reasoning setting; the cost saving comes from one estimation response without running the task environment. Do not silently downgrade the model/effort or substitute a native-harness forecast if the API route is unavailable. An unavailable route produces an explicit missing forecast while the independent natural study can still proceed if its own route is qualified.

The final per-model request/output limits and provider mapping belong in the qualified adapter manifest before dispatch. A forecast API timeout or output limit applies only to that forecast. It never becomes a natural-run deadline. Record tokens, latency and cost when available; there is no assumed price or promised spending total.

## Keep forecast information out of the natural execution

The scheduler and analyst may link the two records. The natural agent must not receive that link, the forecast question, estimate, transcript, result status or any derived time budget.

- The forecast request contains exactly its single user message, with no prior messages, previous-response or conversation ID, tools, retrieval, agent memory or persistence hooks. Provider logging is separate from conversational state; no logged response is fed into another call.
- Build the natural environment from the pinned pre-forecast task image or pristine snapshot, not from a forecast workspace. Give it new native session identifiers and separate writable agent homes, memory directories, workspace, temporary files and conversation stores. Static binaries and immutable task inputs may be reused.
- Exclude user-global or project-global agent memories, auto-memory, unrelated instruction discovery, learned skills, hooks, MCP memory stores and provider conversation continuations. Legitimate pinned benchmark instructions remain. Inject only qualified configuration and required authentication, without copying an account home wholesale. Do not change the user's real account homes or memories to achieve isolation.
- Keep forecast records in controller-owned evidence storage that is absent from the task's filesystem mounts, environment variables, tool endpoints and retrieval sources. Retain the records for later analysis and the existing archival policy; preventing access does not mean deleting evidence.
- A malformed, refused or missing forecast does not change the task, prompt, resource allocation, duration, priority or completion rule of the natural run. Forecast values never enter timed-duration calibration, which continues to use eligible measured natural runtimes.

Prefer completing a forecast pass for one evaluated model before releasing its natural cohort, so forecast calls do not compete with its measured executions. Use the existing one-agent and total-concurrency controls for subject calls; do not launch another evaluated model's forecasts alongside the current agent. Unresolved calls retain the accounting required by the global failure policy. Definitively failed or nonnumeric forecasts do not suppress unaffected natural tasks. If a provider-wide failure or unresolved activity prevents safe admission, isolate or hold work under the existing policy rather than bypassing it.

A fresh session ID alone is not sufficient evidence of isolation. Qualification must inspect the actual request, mounts, homes and adapter configuration. Where an adapter cannot disable or exclude a persistent memory path, it is unqualified for this paired study until that gap is resolved. Provider caching or prior training is not a conversation-memory transfer; record available cache usage and avoid claiming identical timing conditions solely from the absence of session memory.

## Records, accounting and analysis

At the current allocation, each agent has **220 forecast requests plus 660 task executions**: natural, short and long. The forecast adds one lightweight observation per task, not a fourth execution arm or another calibration run. Counts follow the actual roster. The offline allocation CLI currently reports task executions only; forecast accounting is a planned addition.

Create an immutable forecast record with a physical request ID, agent configuration, task revision/seed, instruction and prompt hashes, effective request configuration, raw response, parse result, finish status, timestamps, usage and available cost. Store the forecast's own identity and evidence independently of the natural attempt. Keep any provider-hidden reasoning unavailable rather than reconstructing it. Seal the forecast before the paired natural prompt release. Later analysis joins by configuration and task revision, with an explicit link to the selected natural attempt.

Parse locally and deterministically. Accept exactly one line of `minutes = <number>` or `return minutes = <number>` case-insensitively, with a finite positive integer or decimal. Preserve the raw response. Refusals, filtering, zero/negative estimates, ranges, conflicting values, missing numbers and truncation remain explicit outcomes. No extra model call repairs an answer, and no completed forecast is re-asked to obtain a nicer estimate.

Persist dispatch claims before calling the provider; disable hidden SDK retries. A missing receipt is not permission to call again. Reconcile a transport interruption where possible; otherwise retain an unknown outcome with no automatic replay. A fresh forecast replacement, if supported, must obey the existing one-replacement rules, including confirmed external infrastructure failure, no usable continuation, and proof the original stopped. Never rerun the natural subject because forecast parsing or storage failed. A permitted natural replacement retains the original sealed forecast and its own replacement identity.

Compare valid numeric forecasts with independently valid observed natural runtimes. Keep completion outcome and task quality alongside each pair, including wrong answers and refusals. Show all missing, invalid, interrupted and censored cases in the denominator accounting. Do not substitute requested durations, estimates from treated v1 runs, the forecast itself, or time-to-first-perfect-score for M. Any success-only analysis is a separately labelled breakdown, not a silent eligibility filter.

## Implementation slices and acceptance

1. **Offline contract and accounting.** Add a small forecast module for the exact wrapper, deterministic parser and immutable record schema; extend the offline planner with separately counted forecasts. Test paper-prompt bytes, preserved trailing task newlines, task/version pairing, decimal estimates, `return minutes =`, zero, ranges, multiple values and refused/truncated replies. Existing natural/short/long counts and unset timed durations must remain unchanged.
2. **Isolated invocation and paired dispatch.** Connect one qualified stateless API adapter to the durable ledger and evidence store. Forecast first; prepare the natural request independently from the original task manifest. Test crashes before and after provider acceptance, duplicate delivery, unknown outcomes and receipt loss without replay. Test forecast failure followed by an otherwise valid natural attempt without any prompt or allocation change.
3. **Contamination qualification.** In model-free fixtures, put a unique forecast marker in the response, fake memory files, logs and writable caches. Prove that the natural request and accessible filesystem contain none of it. Change the forecast value and outcome while holding the task/configuration fixed; the natural release payload and prepared state must remain unchanged apart from independent identifiers/audit metadata. Reject shared writable homes, sessions, symlinked memory, inherited hooks and previous-response IDs. Follow with a separately authorized small native/provider isolation pilot before study calls.
4. **Archive and reporting integration.** Keep forecast evidence outside worker-readable state, preserve it across controller restart, and pair it with the actual natural measurement after completion. Verify counts when the roster or agent list changes. Verify that archiving, analysis and an allowed natural replacement cannot alter the frozen forecast or trigger another invocation.

No new natural-time cap, duration values, agent route or account credentials are selected here. Implement against the existing protocol and archive design; do not build a second scheduler or execution environment for forecasts.

## Source evidence

The [source record](../../reviews/2026-10-01-forecast-sources.json) pins the local main-paper setup, natural-runtime appendix and original main-study forecast runner configuration. Main-paper prompt: `AgentTime/overleaf-paper/sections/results.tex:70`; route/effort: `sections/appendix_natural_runtime.tex:18`. The earlier refactor's `forecasting/prompts/forecast.txt` is recorded as a different historical variant, not the template selected here.
