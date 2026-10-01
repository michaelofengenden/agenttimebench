# Experiment rules

## One agent, three runs per task

An agent configuration includes its requested and observed model identity, provider route, runner version, reasoning settings, tools and subagent policy. Keep distinct configurations separate even when they share a model name. Agents can be added over time.

For each task, first collect one fresh natural attempt. The agent sees the task without a duration-following request. M denotes that attempt's observed natural runtime, not an assigned budget. There is no experiment-imposed time cap. Task-defining endpoints remain: for example, YC's simulated year or bankruptcy endpoint. Any external operational stop is recorded as a stop or censored observation, never as natural completion.

After enough agents have run, choose an average natural runtime for each task from an explicitly identified set of comparable observations. Short and long requests use one quarter and four times that average. Those durations are unset today. Natural attempts provide the calibration observations as well as the natural comparison arm. They are not an independent calibration sample, and there is no separate timed M attempt.

Older and newer observations may both contribute after checking task identity, source/grader version, prompt, timing evidence, resources and agent configuration. A run with a duration request, native work limit, external censoring or invalid timing cannot be relabelled as an unprompted natural run. Quality is not an eligibility filter: valid wrong answers, refusals and voluntary early finishes remain visible. The exact sample-sufficiency and weighting rules must be declared before calculating the timed requests.

Each later timed batch records its calculation revision, included observation IDs and durations. Accumulating more agents does not rewrite historical requests or silently pool different task versions. Cross-version comparability may require separate summaries rather than one pooled average.

## Prompts and clocks

Timed requests retain Michael's wording:

> Please work on this task for a full {duration}. Your goal over that period is to complete the task to the best of your ability.

The request is an instruction to the agent, not a harness sleep or a forced finish at that time. Record voluntary completion even if it is early. Record original, adapted and delivered prompt hashes.

The proposed measured interval starts when the complete prompt is released to a prepared agent and ends at its native voluntary terminal event or task-defined endpoint. Setup, queueing, authentication, grading and cleanup are separate phases. Agent tool calls, waits and subagent work count as elapsed time while the attempt is active; child durations are not added on top. Capture monotonic timing and audit timestamps. A final perfect score does not prove the first moment of success.

At completion, preserve the submission using the task's declared contract: immutable file snapshot, a live-state checkpoint, or an external-state export. Stop writes from the full owned execution envelope without destroying required services or unsaved application state before capture. Missing or unenforceable capture prevents qualification. Preserve timing, artifact availability, raw grades and scientific admission as separate fields.

## Concurrency and recovery

Target 220 active task attempts in total, with one evaluated agent configuration at a time. Measure actual overlap and release spread. Each worker owns one trial and one resource reservation; disable Harbor's automatic trial retries. Provider permits and machine capacity are checked independently.

Reattach to healthy work first. Following a confirmed external infrastructure interruption, prefer a qualified continuation of the same session and task state. Check interruption tolerance, clock continuity and any original operational allowance before resuming. Fence the previous segment and preserve all elapsed time, including the interruption. Do not reset the clock, replay uncertain tool effects or supply a new task prompt.

If continuation is impossible, permit at most one fresh replacement for that agent/task/arm after the original is proven stopped. Give it a new attempt ID, clean state and a link to the original. Enforce the limit in the ledger, including across controller restarts. Wrong answers, refusals, voluntary finishes, unknown causes and agent-caused resource exhaustion do not qualify. Post-completion artifact or grade loss does not justify a new subject attempt. Preserve independently valid timing even when quality cannot be recovered.

Report replacements separately from the 660 nominal slots. Drain and reconcile owned resources before switching evaluated agents. Unknown onset stays unresolved until evidence establishes what happened.

## Current state

The current files describe the protocol and offline plan. The timed calculation is pending. Exact roster admission, native timing, recovery, agent routes and 220-way capacity are not yet qualified by this repository.
