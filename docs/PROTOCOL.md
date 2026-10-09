# Experiment rules

## One agent, three runs per task

An agent configuration includes its requested and observed model identity, provider route, runner version, reasoning settings, tools and subagent policy. Keep distinct configurations separate even when they share a model name. Agents can be added over time.

For each task, first collect one fresh natural attempt. The agent sees the task without a duration-following request. M denotes that attempt's observed natural runtime, not an assigned budget. There is no experiment-imposed time cap. Task-defining endpoints remain: for example, YC's simulated year or bankruptcy endpoint. Any external operational stop is recorded as a stop or censored observation, never as natural completion.

After enough agents have run, choose an average natural runtime for each task from an explicitly identified set of comparable observations. Short and long requests use one quarter and four times that average. Those durations are unset today. Natural attempts provide the calibration observations as well as the natural comparison arm. They are not an independent calibration sample, and there is no separate timed M attempt.

Older and newer observations may both contribute after checking task identity, source/grader version, prompt, timing evidence, resources and agent configuration. A run with a duration request, native work limit, external censoring or invalid timing cannot be relabelled as an unprompted natural run. Quality is not an eligibility filter: valid wrong answers, refusals and voluntary early finishes remain visible. The exact sample-sufficiency and weighting rules must be declared before calculating the timed requests.

Each later timed batch records its calculation revision, included observation IDs and durations. Accumulating more agents does not rewrite historical requests or silently pool different task versions. Cross-version comparability may require separate summaries rather than one pooled average.

## Forecasts before natural runs

For each agent configuration and task, obtain one forecast in a fresh isolated Linux controller with the complete allowed task inputs and an explicit description of the intended natural hardware, software, tools and completion rule. This is the user-selected described-target amendment of 6 October. Record the forecast controller's actual hardware separately. Benchmark software is not installed or exercised by the tool-disabled forecaster. GPUs are provisioned only for actual task runs, not for tool-disabled forecasts. The natural copy is never restored from forecast state. Opus uses the Claude subscription through a pinned Claude Code executable; other agent routes remain subject to their own qualification.

The forecast sees the complete native objective, completion rule, allowed initial materials and required images. Freeze the natural messages, material hashes and intended agent/execution profile before dispatch. Forecast tools are disabled; the packet describes the tools and subagent policy the natural run will have. Verify the forecast controller and delivered inputs before forecasting. Verify the actual task worker against the frozen target profile before natural execution. Local emulation does not qualify native x86 performance, and describing an H100 does not prove that one was attached.

For ordinary code and browser tasks without published subject requirements, use the selected baseline of 4 CPU cores and 16 GiB RAM. Published requirements take precedence; GPU, desktop and research profiles are reviewed separately. Natural runs may use native harness subagents where the benchmark permits them. Freeze that configuration before forecasting and count concurrent work and waits once as elapsed time.

Keep forecast history and outputs outside the natural agent's request, memory, filesystem and tools. Use independent writable homes, sessions, workspaces, browser profiles and simulator state. The forecast cannot set a deadline, priority, resource allocation or calibration value. A new conversation ID alone does not establish isolation.

At the current allocation this adds one forecast per task alongside the 600 task executions. Preserve every outcome, including invalid forecasts and interpretation errors, without rerunning to obtain nicer answers. Missing forecasts do not discard valid natural measurements. The [Opus results record](results/OPUS_5_5_FORECASTS.md) preserves the completed corrected batch and its described-target amendment to the [earlier design](superpowers/specs/2026-10-06-matched-forecast-design.md).

The [7 October revision](revisions/2026-10-07-hetzner-runpod.md) refreshed exactly nine forecasts: five replacement tasks and four PaperBench environment contracts. Its verified 200-row canonical selection combines 191 retained sessions with nine fresh sessions. Preserve both earlier forecast directories, study code, attempts and format flags unchanged. A retained estimate must join its exact source task and common record; new identities cannot inherit retired tasks' values. Canonical selection does not change format qualification or turn forecasts into runtime measurements. Natural worker, tool, recovery and archive/restore qualification remain pending.

## Opus access-route amendment, 7 October 2026

The user selected a subscription/API mix through Claude Code for the first
200-task Opus natural cohort and explicitly chose to keep the existing
subscription forecasts. Qualify and record each access route separately. Retain
distinct agent-configuration records inside this explicitly declared Opus cohort;
this is a narrow exception to the earlier one-configuration concurrency rule.
Other evaluated model cohorts remain separate. It is not permission for automatic
route fallback or a model/configuration switch during an attempt.

An API-backed natural attempt paired with a subscription forecast must retain a
route-difference flag. Such a pair is not an exact access-route match. Keep
route-specific summaries and disclose the mixed cohort; the shared Claude Code
harness alone does not establish identical request latency, limits or observed
model configuration. No additional API forecasts are planned by user decision.
This is the corresponding narrow exception to the forecast-per-configuration rule.

Freeze the task-to-route assignment before any selected natural attempt. The
split is not yet chosen; balance benchmark representation where feasible and do
not assign from forecast values or observed results. The [launch plan](superpowers/plans/2026-10-07-opus-natural-launch.md)
defines the qualification sequence. Current code/configuration still holds study
launch: implementing route groups requires explicit tested admission rather than
relaxing the single-agent guard for arbitrary concurrent configurations.

## Prompts and clocks

Timed requests retain Michael's wording:

> Please work on this task for a full {duration}. Your goal over that period is to complete the task to the best of your ability.

The request is an instruction to the agent, not a harness sleep or a forced finish at that time. Record voluntary completion even if it is early. Record original, adapted and delivered prompt hashes.

The proposed measured interval starts when the complete prompt is released to a prepared agent and ends at its native voluntary terminal event or task-defined endpoint. Controller-side setup, queueing, authentication, external grading and cleanup are separate phases. Setup, dependency installation and data or model downloads performed by the subject during the assignment count as task time. Agent tool calls, waits and subagent work count as elapsed time while the attempt is active; child durations are not added on top. Capture monotonic timing and audit timestamps. A final perfect score does not prove the first moment of success.

At completion, preserve the submission using the task's declared contract: immutable file snapshot, a live-state checkpoint, or an external-state export. Stop writes from the full owned execution envelope without destroying required services or unsaved application state before capture. Missing or unenforceable capture prevents qualification. Preserve timing, artifact availability, raw grades and scientific admission as separate fields.

## Concurrency and recovery

Plan one cohort of 200 task attempts, with one evaluated agent configuration at a time and a configured ceiling of 220 active attempts. Measure actual overlap and release spread. Each worker owns one trial and one resource reservation; disable Harbor's automatic trial retries. Provider permits and machine capacity are checked independently.

Reattach to healthy work first. Following a confirmed external infrastructure interruption, prefer a qualified continuation of the same session and task state. Check interruption tolerance, clock continuity and any original operational allowance before resuming. Fence the previous segment and preserve all elapsed time, including the interruption. Do not reset the clock, replay uncertain tool effects or supply a new task prompt.

If continuation is impossible, permit at most one fresh replacement for that agent/task/arm after the original is proven stopped. Give it a new attempt ID, clean state and a link to the original. Enforce the limit in the ledger, including across controller restarts. Wrong answers, refusals, voluntary finishes, unknown causes and agent-caused resource exhaustion do not qualify. Post-completion artifact or grade loss does not justify a new subject attempt. Preserve independently valid timing even when quality cannot be recovered.

Report replacements separately from the 600 nominal slots. Drain and reconcile owned resources before switching evaluated agents. Unknown onset stays unresolved until evidence establishes what happened. Database fencing is not proof that an execution stopped. A permanently unreachable original retains its reservation and blocks the next evaluated agent until stop evidence exists. Isolate an affected task and continue unaffected work; shared integrity failures stop new admissions.

## Session preservation and later forks

Every attempt must have a discoverable archive record and preserve its available native session, subagent histories, workspace and required environment state, regardless of outcome. Michael selected private storage on his Mac, independent of the workers, and can make approximately 4 to 5 TB available. The actual path, capacity guard and transfer configuration remain deployment choices; no capacity is assumed available today.

Before task prompt release, require an adapter-qualified initial session/workspace checkpoint verified on the Mac. An adapter unable to provide that boundary remains unqualified unless Michael accepts its specific loss window. That initial checkpoint does not reconstruct subsequent unsaved work. Keep worker-side copies until the Mac verifies and acknowledges the complete archive. An offline Mac or failed upload must not trigger another subject attempt or deletion of the only required state copy. Stopping execution does not require an archive receipt. Disposable compute may be removed only after stop is proved and required state is verified on storage that survives removal; deleting that retained worker spool requires the Mac receipt. Keep the original session and scientific result immutable. A later fork receives a new identity, separate workspace and a link to the exact parent checkpoint; follow-up forks do not enter the original study slots or calibration data. The short and long arms remain fresh attempts.

Archived transcripts alone do not establish restoration. Each pinned agent and task adapter must prove native context plus workspace/environment restoration and fork behavior. Track stored bytes, verified archive, restored state and qualified native fork as separate facts. The [session preservation design](superpowers/specs/2026-10-01-session-preservation-design.md) defines the proposed implementation and tests. This requirement is recorded; its runtime implementation remains pending.

## Current state

The repository implements the offline plan, a model-free autonomous fixture path with PostgreSQL, detached workers and local evidence, and the completed corrected Opus forecast batch. The [natural preparation pack](NATURAL_RUN_PREPARATION.md) contains 200 controller-only task contracts; no natural attempts have started. Fixture results are always excluded from study analysis. The timed calculation is pending. Exact roster admission, native timing, recovery, agent routes and 220-way capacity are not yet qualified by this repository.

## Archived AutomationBench preparation

AutomationBench was removed from the active allocation by user decision on 5 October 2026. Its source, audit evidence and experimental `agenttime-automationbench-api-uncapped-v1` adapter remain archived. The proposed six task-specific prompt adaptations were not adopted. No AutomationBench study attempts are planned; any future inclusion would require a new selection decision and full native qualification.

## Selected natural environments

The intended provider allocation is 192 Hetzner CPU/desktop candidates and eight RunPod H100 research subjects. The Hetzner count includes 32 closed-book controller tasks, which expose no subject tools or delegation. PaperBench reproduction grading uses separate H100 compute. No natural worker, provider allocation or cloud deployment is qualified by this choice.

The five recorded replacements remove the Windows, T4 and L4 requirements. Native CPU, memory, software and storage requirements still take precedence over the ordinary baseline. Preserve the separate Ubuntu x86 desktop guests for OSWorld and qualify their VM boot and capture. A provider SKU or advertised GPU name does not prove resource equivalence.

For PaperBench fresh reproduction, specify one H100 with 80 GB GPU memory, 16 CPU units, 128 GiB RAM and 400 GB storage. Keep its original reproduction rules and seven-day external reproduction maximum in the delivered instructions. That maximum applies only to separate grading; subject natural work has no experiment time cap. Keep the native rubric unchanged and grader-only, excluded from both subject and forecast payloads. Record the exact instruction adaptation. Native reproduction on RunPod and comparability with original PaperBench remain unqualified.

CORE tasks retain the native Hard preparation rules. Apply the pinned Hard filter before exposing materials: remove reference results, `REPRODUCING.md`, the capsule environment directory, `code/run` and `code/run.sh`, and the native training-file, cache, checkpoint and bytecode exclusions. Chisel's input immutability and network restrictions need enforcement in the natural worker; its answer scorer alone does not enforce them. Keep native partial-credit scoring. Source completeness and a model-free scorer check do not establish a qualified natural environment.

The [new preparation folder](../runs/natural-opus-5.5-max-preparation-20261007/README.md) is separate from the historical 6 October preparation. Each contract must bind its own source common record and the revised roster. Only the native objective, endpoint, permitted materials and described target belong in the subject projection. Forecast results, transcripts and session state remain controller-only.
